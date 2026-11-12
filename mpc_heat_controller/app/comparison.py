"""Daily rolling evaluation and recent deployment models on identical windows.

Window lengths are fixed before validation. Future room temperatures are
recursive predictions. Outdoor and control inputs are historical oracle inputs.
"""
import math
from datetime import timedelta

def features(points, t, temperatures, lag):
    hour=timedelta(hours=1)
    temp=temperatures[t];out,u=points[t][1:3]
    x=[1.0,out-temp,temp,u]
    if len(points[t]) > 3: x.append(points[t][3])
    if lag:
        x += [temp-temperatures[t-hour],temperatures[t-hour]-temperatures[t-2*hour],
              sum(points[t-i*hour][2] for i in range(1,7))/6-u,
              points[t-hour][2]-u]
    return x

TRAIN_DAYS = 14
VALIDATION_DAYS = 7
MIN_TRAIN_PAIRS = 168


def fit(points, train, solve, lag):
    actual = {t: v[0] for t, v in points.items()}
    xs = [features(points, t, actual, lag) for t in train]
    ys = [actual[t+timedelta(hours=1)]-actual[t] for t in train]
    count, width = len(xs), len(xs[0])
    means = [0]+[sum(x[i] for x in xs)/count for i in range(1, width)]
    scales = [1]+[max(1e-6, math.sqrt(sum((x[i]-means[i])**2 for x in xs)/count)) for i in range(1, width)]
    xs = [[(v-m)/s for v, m, s in zip(x, means, scales)] for x in xs]
    a = [[sum(x[i]*x[j] for x in xs)/count for j in range(width)] for i in range(width)]
    if lag:
        for i in range(1, width): a[i][i] += 0.01
    b = [sum(x[i]*y for x, y in zip(xs, ys))/count for i in range(width)]
    return {'name': 'lag6' if lag else 'linear', 'coefficients': solve(a, b),
            'means': means, 'scales': scales, 'ridge': 0.01 if lag else 0}


def compare(points, solve):
    """Evaluate a fixed daily refitting policy, then fit its deployment models.

    Every validation fold learns only from outcomes strictly before its UTC day.
    Deployment coefficients are fitted separately and never score their own data.
    The 14-day window and 7-day evaluation period are fixed, not tuned here.
    """
    hour = timedelta(hours=1)
    times = sorted(points)
    latest = times[-1]
    eligible = [t for t in times if all(t+h*hour in points for h in range(-6, 2))]
    def training(cutoff):
        return [t for t in eligible if cutoff-timedelta(days=TRAIN_DAYS) <= t and t+hour < cutoff]
    deploy_train = training(latest)
    if len(deploy_train) < MIN_TRAIN_PAIRS:
        raise ValueError('För få kompletta träningstimmar under de senaste 14 dygnen; kräver minst 168 med sex timmars förhistorik.')
    starts = [t for t in times if t >= latest-timedelta(days=VALIDATION_DAYS)
              and all(t+h*hour in points for h in range(-6, 25))]
    actual = {t: v[0] for t, v in points.items()}
    errors = {name: {h: [] for h in (1, 6, 12, 24)} for name in ('linear', 'lag6', 'persistence')}
    folds, used, example = {}, [], []
    for t in starts:
        cutoff = t.replace(hour=0)
        if cutoff not in folds:
            train = training(cutoff)
            if len(train) < MIN_TRAIN_PAIRS:
                folds[cutoff] = None
            else:
                folds[cutoff] = {'models': [fit(points, train, solve, lag) for lag in (False, True)],
                                 'training_start': train[0].isoformat(),
                                 'training_end': (train[-1]+hour).isoformat(), 'training_pairs': len(train)}
        fold = folds[cutoff]
        if fold is None: continue
        trajectories = []
        for index, model in enumerate(fold['models']):
            temperatures = {t-i*hour: actual[t-i*hour] for i in range(7)}
            path = []
            for h in range(24):
                current = t+h*hour
                x = features(points, current, temperatures, index == 1)
                prediction = temperatures[current]+sum(c*(v-m)/s for c, v, m, s in
                    zip(model['coefficients'], x, model['means'], model['scales']))
                if not math.isfinite(prediction) or abs(prediction) > 1000:
                    raise ValueError('En kandidat är instabil i valideringen. Ingen modell aktiverades.')
                temperatures[current+hour] = prediction
                path.append(prediction)
            trajectories.append(path)
        used.append(t)
        for h in (1, 6, 12, 24):
            truth = actual[t+h*hour]
            for i, name in enumerate(('linear', 'lag6')):
                errors[name][h].append(abs(trajectories[i][h-1]-truth))
            errors['persistence'][h].append(abs(actual[t]-truth))
        # Show the most recent evaluated trajectory, rather than the oldest.
        example = [{'time': (t+(h+1)*hour).isoformat(), 'actual': actual[t+(h+1)*hour],
                    'predicted': trajectories[0][h], 'lagged': trajectories[1][h]} for h in range(24)]
    if not used:
        raise ValueError('Inga kompletta rullande 24-timmars testfönster under senaste veckan. Kräver sex timmars förhistorik och minst 168 tidigare träningstimmar.')
    models = [fit(points, deploy_train, solve, lag) for lag in (False, True)]
    metrics = [{'hours': h, 'windows': len(used),
                'mae': sum(errors['linear'][h])/len(used),
                'lag_mae': sum(errors['lag6'][h])/len(used),
                'baseline_mae': sum(errors['persistence'][h])/len(used)} for h in (1, 6, 12, 24)]
    return {'models': models, 'coefficients': models[0]['coefficients'], 'metrics': metrics, 'example': example,
            'training_pairs': len(deploy_train), 'split_at': used[0].isoformat(), 'common_windows': len(used),
            'training_start': deploy_train[0].isoformat(), 'training_end': (deploy_train[-1]+hour).isoformat(),
            'data_end': latest.isoformat(), 'validation_method': 'daily_rolling_14d',
            'validation_start': used[0].isoformat(), 'validation_end': (used[-1]+24*hour).isoformat(),
            'validation_folds': [{'cutoff': t.isoformat(), **{k: v for k, v in f.items() if k != 'models'}}
                                 for t, f in folds.items() if f is not None],
            'comparison': 'Rullande validering under senaste veckan: varje UTC-dygn tränas på högst 14 tidigare dygn. Driftmodellen tränas separat på de senaste 14 dygnen. Kända historiska väder-, sol- och styrvärden används, inte arkiverade beslutsprognoser. Samma starttider för alla modeller och horisonter; överlappande fönster är inte oberoende försök.'}

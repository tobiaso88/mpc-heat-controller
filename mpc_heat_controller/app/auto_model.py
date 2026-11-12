"""Automatic house-model training and read-only live MPC proposals."""
import json
import math
import sqlite3
import threading
from contextlib import closing
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from .model import evaluate_points
from . import model_store
from .comparison import features, TRAIN_DAYS, MIN_VALIDATION_WINDOWS
from .pv_history import recovered_zeros, STALE_PV, is_zero

QUALITY_VERSION = 7
QUALITY_HORIZONS = (6, 12, 24)
# Absolute limit for display-only shadow proposals, still bounded by persistence.
MAX_MAE = 0.35
HARD_MAX_MAE = 0.8
MIN_WINDOWS = MIN_VALIDATION_WINDOWS
RESPONSE_HOURS = 12
# Outside this range the signal either does next to nothing, which makes the planner push it
# to its limits, or more than any heat curve can deliver.
MIN_SIGNAL_RESPONSE = -1.0
MAX_SIGNAL_RESPONSE = -0.02
# A model this accurate is not rejected for losing to "the temperature stays the same": the
# steadier the regulation, the harder that baseline is to beat, without the model being worse.
PERSISTENCE_FLOOR = {1: 0.1, 6: 0.15, 12: 0.15, 24: 0.15}
# Hours of test signal in each direction that a model's training period must contain.
MIN_TEST_HOURS = 48
TEST_HOUR_SAMPLES = 30
# A new fit replaces the model in use only when its error is at most this share of the kept one's.
KEEP_MARGIN = 0.9

# Learning mode: active MPC within a band around PI, so a weaker model is acceptable.
PROFILE_DAYS = 7
PROFILE_MIN_SAMPLES = 3
PROFILE_LIMIT = 0.3
PLAN_MAX_AGE = timedelta(minutes=15)

MIN_COMPLETE_HOURS = 250
CHECK_INTERVAL = timedelta(hours=1)
TRAIN_INTERVAL = timedelta(hours=24)


def mapping_for(config, auto_cloud=False, pv_baseline=False):
    mapping = {'indoor': sorted(config.get('indoor', [])),
               'outdoor': config.get('outdoor', ''),
               'signal': config.get('applied_signal', ''), 'solar': config.get('solar', ''), 'cloud': config.get('cloud', ''),
               'pv_power': '' if pv_baseline else config.get('pv_power', ''),
               'pv_forecast': config.get('pv_forecast', '')}
    if pv_baseline:
        mapping['pv_baseline'] = True
        mapping['pv_selected'] = config.get('pv_power', '')
    if auto_cloud and not config.get('pv_power') and not mapping['solar'] and not mapping['cloud'] and config.get('weather'):
        mapping['cloud'] = config['weather'] + '#cloud_coverage'
        mapping['cloud_auto'] = True
    if not mapping['indoor'] or not mapping['outdoor'] or not mapping['signal']:
        return None
    if sum(bool(mapping[k]) for k in ('solar', 'cloud', 'pv_power')) > 1: return None
    selected = mapping['indoor'] + [mapping['outdoor'], mapping['signal']] + list(filter(None, [mapping['solar'], mapping['cloud'], mapping['pv_power']]))
    if len(set(selected)) != len(selected):
        return None
    return mapping


def local_points(data, mapping):
    """Aggregate valid snapshots for the current sensor selection into UTC hours."""
    path = data / 'measurements.sqlite'
    if not path.exists():
        return {}, {'samples': 0, 'incomplete_hours': 0, 'excluded_samples': 0}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT time, readings, settings FROM samples ORDER BY time').fetchall()
    wanted = mapping['indoor'] + [mapping['outdoor'], mapping['signal']] + ([mapping['solar']] if mapping.get('solar') else [mapping['cloud']] if mapping.get('cloud') else [mapping['pv_power']] if mapping.get('pv_power') else [])
    buckets = defaultdict(lambda: defaultdict(list))
    excluded = 0
    pv_samples = []
    stale_zero_hours = set()
    for stamp, raw_readings, raw_settings in rows:
        try:
            settings = json.loads(raw_settings)
            if (sorted(settings.get('indoor', [])) != mapping['indoor'] or
                    settings.get('outdoor') != mapping['outdoor'] or
                    settings.get('applied_signal') != mapping['signal'] or settings.get('solar', '') != mapping.get('solar', '') or
                    settings.get('cloud', '') != ('' if mapping.get('cloud_auto') else mapping.get('cloud', '')) or
                    settings.get('pv_power', '') != mapping.get('pv_selected', mapping.get('pv_power', '')) or
                    settings.get('pv_forecast', '') != mapping.get('pv_forecast', '') or
                    mapping.get('cloud_auto') and settings.get('weather') + '#cloud_coverage' != mapping['cloud']):
                excluded += 1
                continue
            moment = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if moment.tzinfo is None:
                raise ValueError()
            hour = moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            readings = json.loads(raw_readings)
            if mapping.get('pv_power'):
                pv_samples.append((moment.astimezone(timezone.utc), next(
                    (r for r in readings if r.get('entity') == mapping['pv_power']), {})))
                pv = pv_samples[-1][1]
                if pv.get('quality') == STALE_PV and is_zero(pv): stale_zero_hours.add(hour)
            values = {r['entity']: r['value'] for r in readings
                      if (r.get('quality') == 'OK' or r.get('entity') == mapping['signal'] and mapping['signal'].startswith('number.'))
                      and isinstance(r.get('value'), (int, float))
                      and not isinstance(r.get('value'), bool) and math.isfinite(r['value'])}
            for entity in wanted:
                if entity in values:
                    buckets[hour][entity].append(values[entity])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            excluded += 1
    recovered = recovered_zeros(pv_samples)
    for moment in recovered:
        buckets[moment.replace(minute=0, second=0, microsecond=0)][mapping['pv_power']].append(0.0)
    points = {}
    for hour, entities in buckets.items():
        if all(entities.get(entity) for entity in wanted):
            mean = lambda entity: sum(entities[entity]) / len(entities[entity])
            points[hour] = (sum(mean(entity) for entity in mapping['indoor']) / len(mapping['indoor']),
                            mean(mapping['outdoor']), mean(mapping['signal'])) + ((mean(mapping['solar']),) if mapping.get('solar') else (mean(mapping['cloud']),) if mapping.get('cloud') else (mean(mapping['pv_power']),) if mapping.get('pv_power') else ())
    return points, {'samples': len(rows), 'incomplete_hours': len(buckets) - len(points),
                    'excluded_samples': excluded, 'recovered_pv_zero_samples': len(recovered),
                    'pv_stale_zero_hours': [t.isoformat() for t in sorted(stale_zero_hours-set(points))][-8:]}


def test_hours(data, since):
    """UTC hours with a confirmed test offset of at least half a degree.
    Legacy requested-only records cannot establish an applied test signal."""
    path = data / 'measurements.sqlite'
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='control_samples'").fetchone():
            return {}
        rows = db.execute('SELECT time, result FROM control_samples WHERE time >= ? ORDER BY time', (since.isoformat(),)).fetchall()
    counts = defaultdict(lambda: [0, 0, 0])
    for stamp, raw in rows:
        try:
            moment = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            test = (json.loads(raw).get('decision') or {}).get('applied_test_signal') or 0
            count = counts[moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)]
            count[0] += 1
            count[1] += test >= 0.5-1e-9
            count[2] += test <= -0.5+1e-9
        except (ValueError, TypeError, AttributeError):
            continue
    return {hour: 1 if plus >= minus else -1 for hour, (total, plus, minus) in counts.items()
            if total >= TEST_HOUR_SAMPLES and max(plus, minus) >= 0.75*total}


def count_tests(tested, start, end):
    signs = [sign for hour, sign in tested.items() if start <= hour < end]
    return {'plus': signs.count(1), 'minus': signs.count(-1)}


def annotate(report, tested, clock):
    """Record on each new model when it was trained and how much test signal its period held."""
    try:
        start, end = datetime.fromisoformat(report['training_start']), datetime.fromisoformat(report['training_end'])
    except (KeyError, TypeError, ValueError):
        return
    for model in report.get('models', []):
        model.update(training_start=report['training_start'], training_end=report['training_end'],
                     trained_at=clock.isoformat(), test_hours=count_tests(tested, start, end))


def retain(report, previous):
    """Keep each model from `previous` while it still passes on this week's windows, unless the
    new fit is clearly better. The house does not change from day to day, and a fit made on data
    with test signal says more about the signal than a later fit made without it."""
    kept = report.pop('kept_metrics', None) or {}
    if previous:
        report['in_use'] = {key: previous.get(key) for key in ('selected_model', 'learning_model')}
    for index, model in enumerate(report.get('models', [])):
        name = model['name']
        column = 'lag_mae' if name == 'lag6' else 'mae'
        old = next((m for m in (previous or {}).get('models', []) if m.get('name') == name), None)
        if not old or name not in kept:
            continue
        models = list(report['models'])
        models[index] = dict(old, kept=True)
        trial = dict(report, models=models, metrics=[dict(m, **{column: kept[name][m['hours']]}) for m in report['metrics']])
        if learning_quality(trial, name):
            continue
        if not learning_quality(report, name) and _total_mae(report, name) < KEEP_MARGIN*_total_mae(trial, name):
            continue
        report['models'], report['metrics'] = trial['models'], trial['metrics']
    if report.get('models'):
        report['coefficients'] = report['models'][0]['coefficients']


def _predict(model, values):
    return sum(c * (v-m) / s for c, v, m, s in
               zip(model['coefficients'], values, model['means'], model['scales']))


def forecast_day(forecast, blockers=None):
    def reject(reason):
        if blockers is not None: blockers.append(reason)
        return []
    try:
        now = datetime.now(timezone.utc)
        first = 0
        # Cached forecasts retain earlier hours. Advance to the usable window.
        while first < len(forecast):
            moment = datetime.fromisoformat(forecast[first]['datetime'].replace('Z', '+00:00'))
            if moment.tzinfo is None: return reject('Väderprognosen saknar tidszon.')
            if moment >= now-timedelta(minutes=30): break
            first += 1
        day = forecast[first:first+24]
        if len(day) < 24:
            return reject(f'Väderprognosen har {len(day)} aktuella timmar; kräver 24.')
        times = [datetime.fromisoformat(row['datetime'].replace('Z', '+00:00')) for row in day]
        if any(t.tzinfo is None for t in times): return reject('Väderprognosen saknar tidszon.')
        if times[0] > now+timedelta(hours=2): return reject('Väderprognosen börjar mer än två timmar framåt.')
        if any(not timedelta(minutes=30) <= b-a <= timedelta(minutes=90) for a,b in zip(times,times[1:])):
            return reject('Väderprognosens första dygn har luckor eller fel timordning.')
        if times[-1] < now+timedelta(hours=22): return reject('Väderprognosen täcker mindre än 22 timmar framåt.')
        if any(not math.isfinite(float(row['temperature'])) for row in day):
            return reject('Väderprognosen innehåller ogiltig utetemperatur.')
        return day
    except (KeyError, TypeError, ValueError, AttributeError):
        return reject('Väderprognosen innehåller ogiltiga timvärden eller tidsstämplar.')


def live_plan(report, indoor, applied, forecast, config, output=None, history=None, blockers=None, profile=None):
    """Hourly plan; each signal is on the output's 0.5 °C grid. `profile` adds a learned
    hour-of-day disturbance (°C/h, keyed by UTC hour) to every predicted step."""
    def reject(reason):
        if blockers is not None: blockers.append(reason)
        return []
    day = forecast_day(forecast, blockers)
    if not report: return reject('Ingen godkänd modell är tillgänglig.')
    stale = validation_reasons(report, datetime.now(timezone.utc))
    if stale: return reject(' '.join(stale))
    if indoor is None: return reject('En eller flera valda rumsgivare saknar giltigt värde.')
    if applied is None: return reject('Ohmigos inställda signal saknar giltigt värde.')
    if not day: return []
    if not output or output.get('state') != 'confirmed':
        return reject('Number-utgången saknar bekräftat värde eller giltig enhet, rapporttid, gränser och steg om 0,5 °C.')
    try:
        model = next(m for m in report['models'] if m['name'] == report.get('selected_model', 'linear'))
        lag = model['name'] == 'lag6'
        hour = timedelta(hours=1)
        start = datetime.fromisoformat(day[0]['datetime'].replace('Z', '+00:00')).replace(minute=0, second=0, microsecond=0)
        past = {}
        if lag:
            history = history or {}
            anchor = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            for i in range(1, 7):
                if anchor-i*hour not in history:
                    return reject(f'Lag6 saknar komplett historiktimme {(anchor-i*hour).isoformat()}.')
                past[start-i*hour] = history[anchor-i*hour]
        solar = bool(report.get('mapping', {}).get('solar'))
        cloud = bool(report.get('mapping', {}).get('cloud'))
        field = 'solar_irradiance' if solar else 'cloud_coverage' if cloud else 'pv_power' if report.get('mapping', {}).get('pv_power') else None
        if field:
            missing = [row['datetime'] for row in day if field not in row]
            if missing:
                return reject(f'Prognosen saknar {field} för {len(missing)} av 24 timmar; första luckan {missing[0]}.')
            if any(not math.isfinite(float(row[field])) for row in day):
                return reject(f'Prognosen innehåller ogiltiga värden för {field}.')
        lo=max(float(output['min']),float(config['signal_min']))
        hi=min(float(output['max']),float(config['signal_max']))
        first=math.ceil(lo*2-1e-9);last=math.floor(hi*2+1e-9)
        if first>last or not lo<=applied<=hi:
            return reject(f'Aktuell signal {applied} °C eller tillåtna halvgradsteg ligger utanför signalgränserna {lo}–{hi} °C.')
        candidates=[(0.0,float(indoor),float(applied),[],past)]
        for index, row in enumerate(day):
            outside=float(row['temperature']);expanded=[]
            for cost, temperature, previous, path, previous_points in candidates:
                allowance=min(float(config['max_step']),float(config['pi_rate']))
                for tick in range(max(first,math.ceil((previous-allowance)*2-1e-9)),
                                  min(last,math.floor((previous+allowance)*2+1e-9))+1):
                    signal=tick/2
                    current = start+index*hour
                    points = dict(previous_points)
                    points[current] = (temperature, outside, signal) + ((float(row[field]),) if field else ())
                    temperatures = {t: values[0] for t, values in points.items()}
                    predicted=temperature+_predict(model,features(points,current,temperatures,lag))+(profile or {}).get(current.hour,0.0)
                    if not math.isfinite(predicted) or abs(predicted)>100: continue
                    violation=max(config['comfort_min']-predicted,0,predicted-config['comfort_max'])
                    score=cost+(predicted-config['target'])**2+20*violation**2+0.02*(signal-previous)**2
                    expanded.append((score,predicted,signal,path+[{'datetime':row['datetime'],
                        'indoor':round(predicted,3),'outdoor':round(outside,2),'signal':signal}],
                        {t: v for t, v in points.items() if t >= current-5*hour}))
            if not expanded: return reject('Ingen stabil signalbana ryms inom signalgränser och tillåten ändringstakt.')
            # Keep the cheapest paths per signal level. A global top list prunes paths that
            # start heating early for a cold night before their lower later cost shows.
            kept={}
            for item in sorted(expanded,key=lambda item:item[0]):
                same=kept.setdefault(item[2],[])
                if len(same)<3: same.append(item)
            candidates=sorted((item for same in kept.values() for item in same),key=lambda item:item[0])
        return candidates[0][3]
    except (KeyError,TypeError,ValueError,OverflowError,StopIteration):
        return reject('Planeringen saknar giltiga modellvärden, historik eller prognosfält.')


def signal_effect(report, name='linear'):
    """Mean cumulative response at 6 and 12 hours to a sustained +1 °C step."""
    try:
        model = next(m for m in report['models'] if m['name'] == name)
        hour = timedelta(hours=1)
        start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        responses = []
        for step in (0, 1):
            points = {start+i*hour: (21.5, 5.0, step if i >= 0 else 0.0) +
                      ((0.0,) if any(report.get('mapping', {}).get(k) for k in ('solar', 'cloud', 'pv_power')) else ())
                      for i in range(-6, RESPONSE_HOURS)}
            temperatures = {start+i*hour: 21.5 for i in range(-6, 1)}
            for i in range(RESPONSE_HOURS):
                t = start+i*hour
                temperatures[t+hour] = temperatures[t]+_predict(model, features(points, t, temperatures, name == 'lag6'))
            responses.append([temperatures[start+i*hour] for i in (6, 12)])
        effects = [b-a for a,b in zip(*responses)]
        if not all(math.isfinite(e) for e in effects): return None
        # A positive response at either horizon must never be averaged away.
        return max(effects) if max(effects) > 0 else sum(effects)/len(effects)
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, StopIteration, OverflowError):
        return None


def validation_reasons(report, clock=None):
    """Reject stale rolling evidence; legacy offline reports have no period metadata."""
    if not report or report.get('validation_method') != 'daily_rolling_14d':
        return []
    try:
        end = datetime.fromisoformat(report['validation_end'])
        data_end = datetime.fromisoformat(report['data_end'])
        trained = datetime.fromisoformat(report['training_end'])
        if any(t.tzinfo is None for t in (end, data_end, trained)): raise ValueError()
        if data_end-end > timedelta(hours=48):
            return ['Valideringen saknar kompletta testdygn nära senaste mätningen (mer än 48 h).']
        # The training itself may be old: a kept model is scored again on fresh windows every day.
        if clock is not None and clock-end > timedelta(hours=48):
            return ['Valideringen är äldre än 48 h; PI styr tills färska data har validerats.']
    except (KeyError, ValueError, TypeError):
        return ['Modellens tränings- eller valideringsperiod är ogiltig.']
    return []


def test_reasons(report, name):
    model = next((m for m in (report or {}).get('models', []) if m.get('name') == name), {})
    hours = model.get('test_hours') or {}
    plus, minus = hours.get('plus', 0), hours.get('minus', 0)
    if min(plus, minus) >= MIN_TEST_HOURS:
        return []
    return [f'{name}: träningsperioden har {plus} h testsignal med höjd signal och {minus} h med sänkt; kräver minst '
            f'{MIN_TEST_HOURS} h av vardera. Testsignalen körs under aktiv styrning med MPC-inlärning påslagen.']


def quality(report, name=None):
    """Return rejection reasons; an empty list means the candidate passes."""
    name = name or (report or {}).get('selected_model', 'linear')
    reasons = validation_reasons(report) + test_reasons(report, name)
    effect = signal_effect(report, name)
    if effect is None or not MIN_SIGNAL_RESPONSE <= effect <= MAX_SIGNAL_RESPONSE:
        reasons.append(f'{name}: signalrespons vid 6–12 h = {effect}; kräver {MIN_SIGNAL_RESPONSE} ≤ respons ≤ {MAX_SIGNAL_RESPONSE} °C per °C.')
    if (report or {}).get('mapping', {}).get('pv_power'):
        try:
            model = next(m for m in report['models'] if m['name'] == name)
            gain = model['coefficients'][4]/model['scales'][4]
            if not math.isfinite(gain) or not 0 < gain <= 0.001: raise ValueError(str(gain))
        except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, StopIteration) as error:
            reasons.append(f'{name}: solcellseffekt {error}; kräver 0 < effekt ≤ 0,001 °C/h per W.')
    metrics = {m['hours']: m for m in (report or {}).get('metrics', [])}
    for h in QUALITY_HORIZONS:
        m = metrics.get(h, {})
        mae = m.get('lag_mae' if name == 'lag6' else 'mae', float('inf'))
        baseline = m.get('baseline_mae', float('nan'))
        if m.get('windows', 0) < MIN_WINDOWS:
            reasons.append(f'{name} {h} h: {m.get("windows", 0)} fönster; kräver ≥ {MIN_WINDOWS}.')
        if not math.isfinite(mae) or mae > MAX_MAE or mae > HARD_MAX_MAE:
            reasons.append(f'{name} {h} h: MAE {mae} °C; kräver ≤ {MAX_MAE} °C (absolut tak {HARD_MAX_MAE} °C).')
        if not math.isfinite(baseline) or mae > max(baseline, PERSISTENCE_FLOOR[h]):
            reasons.append(f'{name} {h} h: MAE {mae} °C > persistens {baseline} °C (godtas upp till {PERSISTENCE_FLOOR[h]} °C).')
    return reasons


def learning_quality(report, name):
    """Rejection reasons for MPC learning mode: enough test signal, plausible signal response,
    stable and below the absolute ceiling at every horizon, and for the next hour either better
    than persistence or within the accuracy floor."""
    reasons = validation_reasons(report) + [r for r in quality(report, name) if 'signalrespons' in r or 'solcellseffekt' in r or 'testsignal' in r]
    metrics = {m['hours']: m for m in (report or {}).get('metrics', [])}
    for h in (1,) + QUALITY_HORIZONS:
        m = metrics.get(h, {})
        mae = m.get('lag_mae' if name == 'lag6' else 'mae', float('inf'))
        if m.get('windows', 0) < MIN_WINDOWS:
            reasons.append(f'{name} {h} h: {m.get("windows", 0)} fönster; kräver ≥ {MIN_WINDOWS}.')
        if not math.isfinite(mae) or mae > HARD_MAX_MAE:
            reasons.append(f'{name} {h} h: MAE {mae} °C; inlärning kräver ≤ {HARD_MAX_MAE} °C.')
        if h == 1 and not mae <= max(m.get('baseline_mae', float('nan')), PERSISTENCE_FLOOR[1]):
            reasons.append(f'{name} 1 h: MAE {mae} °C > persistens {m.get("baseline_mae")} °C (godtas upp till {PERSISTENCE_FLOOR[1]} °C).')
    return reasons


def _total_mae(report, name):
    return sum(m['lag_mae' if name == 'lag6' else 'mae'] for m in report['metrics'] if m['hours'] in QUALITY_HORIZONS)


def _pick(report, names, current):
    """Lowest error, but the model type already in use stays unless the other is clearly better.
    The two types can differ a lot in signal response; swapping on a daily coin toss is no gain."""
    if not names:
        return None
    best = min(names, key=lambda name: _total_mae(report, name))
    if current in names and _total_mae(report, best) >= KEEP_MARGIN*_total_mae(report, current):
        return current
    return best


def select_model(report):
    names = [m['name'] for m in report['models'] if m['name'] in ('linear', 'lag6')]
    checks = {name: quality(report, name) for name in names}
    in_use = report.pop('in_use', None) or {}
    report['quality_version'] = QUALITY_VERSION
    report['quality_reasons'] = checks
    approved = [name for name, reasons in checks.items() if not reasons]
    report['selected_model'] = _pick(report, approved, in_use.get('selected_model'))
    learning = {name: learning_quality(report, name) for name in names}
    report['learning_reasons'] = learning
    usable = [name for name, reasons in learning.items() if not reasons]
    report['learning_model'] = _pick(report, usable, in_use.get('learning_model'))
    return bool(approved)


def disturbance_profile(report, name, points, clock):
    """Mean one-step model error per UTC hour over recent days: heat the model has no input for,
    such as cooking, people and evening sun. Hours with too few samples get no correction."""
    model = next(m for m in report['models'] if m['name'] == name)
    lag, hour = name == 'lag6', timedelta(hours=1)
    temperatures = {t: v[0] for t, v in points.items()}
    errors = defaultdict(list)
    for t in points:
        if not clock-timedelta(days=PROFILE_DAYS) <= t < clock-hour or not all(t+i*hour in points for i in range(-6, 2)):
            continue
        error = temperatures[t+hour]-temperatures[t]-_predict(model, features(points, t, temperatures, lag))
        if math.isfinite(error):
            errors[t.hour].append(error)
    return {h: max(-PROFILE_LIMIT, min(PROFILE_LIMIT, sum(v)/len(v))) for h, v in errors.items() if len(v) >= PROFILE_MIN_SAMPLES}


class AutoModel:
    def __init__(self, data):
        self.data = data
        self.lock = threading.Lock()
        self.last_check = None
        self.points_cache = {}

    def hourly_points(self, mapping, clock):
        """Logged hours for a mapping, read at most once per UTC hour."""
        key = (json.dumps(mapping, sort_keys=True), clock.replace(minute=0, second=0, microsecond=0))
        if key not in self.points_cache:
            self.points_cache = {key: local_points(self.data, mapping)[0]}
        return self.points_cache[key]

    def learning(self, config, saved, inside, applied, forecast, output, clock):
        """Plan for MPC learning mode from the best usable model; reasons when none is usable."""
        status = saved.get('status') or {}
        report = saved.get('report')
        if status.get('state') == 'ready' and report:
            candidates = [report] + ([report['fallback']] if report.get('fallback') else [])
        else:
            evaluations = status.get('evaluations') or {}
            candidates = [evaluations[k] for k in ('pv', 'cloud', 'temperature') if evaluations.get(k)]
        reasons, model_ready = [], False
        for candidate in candidates:
            mapping = candidate.get('mapping') or {}
            if mapping != mapping_for(config, auto_cloud=bool(mapping.get('cloud_auto')), pv_baseline=bool(mapping.get('pv_baseline'))):
                reasons.append('Modellen är tränad med ett annat givarval.')
                continue
            names = [m['name'] for m in candidate.get('models', []) if m['name'] in ('linear', 'lag6')]
            usable = [n for n in names if not learning_quality(candidate, n)]
            if not usable:
                reasons += [r for n in names for r in learning_quality(candidate, n)]
                continue
            model_ready = True
            try:
                points = self.hourly_points(mapping, clock)
            except (OSError, sqlite3.Error):
                reasons.append('Mätloggen kunde inte läsas.')
                continue
            # The type chosen at training first; the other only if that one cannot plan.
            for name in sorted(usable, key=lambda n: (n != candidate.get('learning_model'), _total_mae(candidate, n))):
                try:
                    profile = disturbance_profile(candidate, name, points, clock)
                except (KeyError, StopIteration, ValueError, TypeError, OverflowError):
                    reasons.append(f'{name}: dygnsprofilen kunde inte beräknas.')
                    continue
                blockers = []
                plan = live_plan(dict(candidate, selected_model=name), inside, applied, forecast, config, output, points, blockers, profile)
                if plan:
                    return {'state': 'ready', 'model_ready': True, 'model': name, 'source': 'pv' if mapping.get('pv_power') else 'cloud' if mapping.get('cloud') else 'temperature',
                            'signal': plan[0]['signal'], 'plan': plan, 'planned_at': clock.isoformat(),
                            'profile': {str(h): round(v, 4) for h, v in sorted(profile.items())}}
                reasons += blockers
        # `model_ready` False means no model passes, which is what the test signal is for. A model
        # that passes but cannot plan right now, for want of a forecast say, needs no test signal.
        return {'state': 'blocked', 'model_ready': model_ready, 'reasons': list(dict.fromkeys(reasons)) or ['Ingen modell är tränad ännu.'], 'plan': []}

    def maybe_train(self, config, clock=None):
        clock = clock or datetime.now(timezone.utc)
        if not self.lock.acquire(blocking=False):
            return
        try:
            saved = model_store.load_auto(self.data)
            current_mapping = mapping_for(config)
            if saved and saved.get('report') and saved['report'].get('mapping', {}).get('cloud_auto'):
                current_mapping = mapping_for(config, auto_cloud=True)
            if saved and saved.get('report') and saved['report'].get('mapping', {}).get('pv_baseline'):
                current_mapping = mapping_for(config, pv_baseline=True)
            same_model = bool(saved and (saved.get('status') or {}).get('state') == 'ready' and (saved.get('report') or {}).get('quality_version') == QUALITY_VERSION and saved.get('report') and saved['report'].get('mapping') == current_mapping)
            interval = TRAIN_INTERVAL if same_model else CHECK_INTERVAL
            if self.last_check and clock-self.last_check < interval:
                return
            if same_model:
                try:
                    trained = datetime.fromisoformat(saved['status']['trained_at'])
                    if clock-trained < TRAIN_INTERVAL:
                        self.last_check = clock
                        return
                except (KeyError, TypeError, ValueError):
                    pass
            self.last_check = clock
            mapping = mapping_for(config, pv_baseline=bool(config.get('pv_power')))
            if config.get('mode') != 'shadow' or not mapping:
                model_store.save_auto_status(self.data, {
                    'state': 'waiting', 'message': 'Välj skuggläge, rumsgivare, utegivare och Ohmigos inställda värde för automatisk träning.',
                    'checked_at': clock.isoformat(), 'complete_hours': 0})
                return
            points, info = local_points(self.data, mapping)
            # Training reaches TRAIN_DAYS back from the newest logged hour, which need not be this hour.
            latest = max(points) if points else clock
            tested = test_hours(self.data, latest-timedelta(days=TRAIN_DAYS+1))
            previous = ((saved or {}).get('status') or {}).get('evaluations') or {}

            carried = set()

            def earlier(key, mapping):
                old = previous.get(key)
                return old if old and old.get('mapping') == mapping and old.get('quality_version') == QUALITY_VERSION else None

            def carry(key, mapping):
                """A day without enough data to evaluate must not cost the model in use. Its old
                validation still blocks planning after 48 h, until it can be scored again."""
                if earlier(key, mapping):
                    status['evaluations'][key] = earlier(key, mapping)
                    carried.add(key)

            def evaluated(key, points, mapping, incomplete):
                old = earlier(key, mapping)
                report = evaluate_points(points, mapping, incomplete_hours=incomplete, source='local_log',
                                         kept=old['models'] if old else ())
                annotate(report, tested, clock)
                retain(report, old)
                return report
            status = {'state': 'collecting', 'checked_at': clock.isoformat(),
                      'complete_hours': len(points), **info, 'mapping': mapping,
                      'test_signal': dict(count_tests(tested, latest-timedelta(days=TRAIN_DAYS), latest+timedelta(hours=1)), required=MIN_TEST_HOURS),
                      'evaluations': {}}
            if len(points) < MIN_COMPLETE_HOURS:
                status['message'] = f'Samlar data: {len(points)} av minst {MIN_COMPLETE_HOURS} kompletta timmar.'
                model_store.save_auto_status(self.data, status)
                return
            try:
                try:
                    report = evaluated('temperature', points, mapping, info['incomplete_hours'])
                except ValueError as error:
                    status['quality_reasons'] = {'temperature': {'validation': [str(error)]}}
                    carry('temperature', mapping)
                    if not config.get('pv_power'):
                        raise
                    report = None
                status['evaluations'].setdefault('temperature', report)
                if report is None or not select_model(report):
                    report = None
                automatic = mapping_for(config, auto_cloud=True)
                if automatic and automatic.get('cloud_auto'):
                    cloud_points, cloud_info = local_points(self.data, automatic)
                    if len(cloud_points) >= MIN_COMPLETE_HOURS and max(cloud_points) >= clock-timedelta(hours=2):
                        try:
                            cloud_report = evaluated('cloud', cloud_points, automatic, cloud_info['incomplete_hours'])
                            status['evaluations']['cloud'] = cloud_report
                            if select_model(cloud_report):
                                cloud_report['fallback'] = report
                                report = cloud_report
                                status['complete_hours'] = len(cloud_points)
                                status['mapping'] = automatic
                        except ValueError:
                            carry('cloud', automatic)
                    else:
                        carry('cloud', automatic)
                pv_reason = ''
                if config.get('pv_power'):
                    pv_mapping = mapping_for(config)
                    pv_points, pv_info = local_points(self.data, pv_mapping)
                    status['pv_diagnostics'] = pv_info
                    if len(pv_points) >= MIN_COMPLETE_HOURS and max(pv_points) >= clock-timedelta(hours=2):
                        try:
                            pv_report = evaluated('pv', pv_points, pv_mapping, pv_info['incomplete_hours'])
                            pv_report['recovered_pv_zero_samples'] = pv_info.get('recovered_pv_zero_samples', 0)
                            status['evaluations']['pv'] = pv_report
                            if select_model(pv_report):
                                if report:
                                    pv_report['fallback'] = report
                                report = pv_report
                                status['complete_hours'] = len(pv_points)
                                status['mapping'] = pv_mapping
                            else:
                                pv_reason = ' Solcellsmodellen klarade ännu inte kvalitetskontrollen.'
                        except ValueError as error:
                            status.setdefault('quality_reasons', {})['pv'] = {'validation': [str(error)]}
                            pv_reason = ' Solcellsmodellen kunde inte valideras.'
                            carry('pv', pv_mapping)
                    else:
                        carry('pv', pv_mapping)
                        pv_reason = f' Solcellsmodellen samlar giltig produktionshistorik: {len(pv_points)} av minst {MIN_COMPLETE_HOURS} timmar.'
                if pv_reason and report is not None:
                    pv_reason += ' Temperaturmodellen används.'
                status.setdefault('quality_reasons', {}).update({key: value.get('quality_reasons', {}) for key, value in status['evaluations'].items() if value and key not in carried})
                if report is None:
                    raise ValueError(('Varken temperaturmodellen eller solcellsmodellen klarade kvalitetskontrollen. ' if config.get('pv_power') else 'Temperaturmodellen klarade inte kvalitetskontrollen. ') + pv_reason.strip())
                status.update({'state': 'ready', 'message': (('Molnmodell tränad från rumstemperatur och väderentitetens aktuella molnighet; utetemperaturmodell finns som reserv.' if report.get('fallback') else 'Molnmodell tränad; ingen validerad temperaturmodell som reserv.')
                                                          if report.get('mapping', {}).get('cloud_auto') else
                                                          ('Solcellsmodell tränad från uppmätt produktion; utetemperaturmodell finns som reserv.'
                                                           if report.get('fallback') else 'Solcellsmodell tränad från uppmätt produktion; ingen validerad temperaturmodell som reserv.')
                                                          if report.get('mapping', {}).get('pv_power') else
                                                          'Automatisk temperaturmodell tränad och validerad från appens mätlogg.') + pv_reason,
                               'trained_at': clock.isoformat()})
                model_store.save_auto(self.data, report, status)
            except ValueError as error:
                status.update({'state': 'needs_data', 'message': str(error)})
                reports = status.get('evaluations', {})
                model_store.save_auto(self.data, reports.get('temperature'), status)
        finally:
            self.lock.release()

    def result(self, config, readings, forecast, states=None, control=None):
        saved = model_store.load_auto(self.data)
        if not saved:
            return {'state': 'waiting', 'message': 'Väntar på första kontrollen av mätloggen.', 'plan': []}
        status = saved.get('status') or {}
        report = saved.get('report')
        mapping = mapping_for(config, auto_cloud=bool(report and report.get('mapping', {}).get('cloud_auto')),
                              pv_baseline=bool(report and report.get('mapping', {}).get('pv_baseline')))
        if report and report.get('mapping') != mapping:
            return dict(status, state='collecting', message='Givarvalet har ändrats. Samlar en ny sammanhängande träningsperiod.', plan=[])
        values = {r['entity']: r.get('value') for r in readings
                  if r.get('quality') == 'OK' or mapping and r.get('entity') == mapping['signal'] and mapping['signal'].startswith('number.')}
        inside = None
        if mapping and all(values.get(e) is not None for e in mapping['indoor']):
            inside = sum(values[e] for e in mapping['indoor']) / len(mapping['indoor'])
        applied = values.get(mapping['signal']) if mapping else None
        output=None
        if mapping and states is not None:
            state=next((s for s in states if s.get('entity_id')==mapping['signal']), {})
            attrs=state.get('attributes', {})
            try:
                low=float(attrs['min']);high=float(attrs['max']);step=float(attrs['step'])
                current=float(state['state'])
                active=bool(control and control.get().get('active'))
                confirmed=active and control.pending_ack is None and control.last_sent is not None and abs(current-control.last_sent)<=step/2+1e-6
                stamp=state.get('last_reported') or state.get('last_updated')
                recent=stamp and 0 <= (datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))).total_seconds() <= 86400
                if (mapping['signal'].startswith('number.') and not attrs.get('restored') and
                    attrs.get('unit_of_measurement')=='°C' and all(math.isfinite(v) for v in (low,high,step,current)) and
                    step>0 and abs(0.5/step-round(0.5/step))<1e-6 and abs(low/step-round(low/step))<1e-6 and
                    (confirmed or recent) and applied is not None and abs(applied-current)<1e-6):
                    output={'state':'confirmed','min':low,'max':high,'step':step}
            except (KeyError,ValueError,TypeError,AttributeError): pass
        valid_model = bool(report and not quality(report))
        history = local_points(self.data, mapping)[0] if mapping and report and status.get('state') == 'ready' and report.get('selected_model') == 'lag6' else None
        blockers = []
        plan = live_plan(report if status.get('state') == 'ready' and valid_model else None, inside, applied, forecast, config, output, history, blockers)
        fallback_used = False
        if not plan and report and status.get('state') == 'ready' and report.get('fallback') and not quality(report['fallback']):
            fallback_mapping = report['fallback']['mapping']
            history = local_points(self.data, fallback_mapping)[0]
            plan = live_plan(report['fallback'], inside, applied, forecast, config, output, history)
            fallback_used = bool(plan)
        message = status.get('message', '')
        if report and status.get('state') == 'ready' and not valid_model and not fallback_used:
            message += ' Sparad modell har felvänd eller orimlig styrverkan och spärras.'
        elif report and status.get('state') == 'ready' and not plan:
            message += ' Liveförslag väntar: ' + ' '.join(blockers)
        elif plan:
            message += (' Sol- eller molnprognos saknas; utetemperaturmodellen används.' if fallback_used else '')
            message += ' MPC-förslaget är endast skuggläge och skickas inte.'
        result = dict(status, report=report, plan=plan, message=message, plan_blockers=blockers if not plan else [])
        if config.get('mpc_learning'):
            learning = self.learning(config, saved, inside, applied, forecast, output, datetime.now(timezone.utc))
            result['learning'] = learning
            if learning['plan']:
                result['plan'] = learning['plan']
                result['message'] = message + ' MPC-inlärning: planen används inom ±' + f"{config['mpc_max_deviation']:g}".replace('.', ',') + ' °C från PI när styrningen är aktiv.'
            else:
                result['message'] = message + ' MPC-inlärning väntar: ' + ' '.join(learning['reasons'][:3])
        return result

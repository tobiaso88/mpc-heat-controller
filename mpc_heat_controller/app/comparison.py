"""Fixed lagged candidate vs original model on identical 24-hour windows.

No hyperparameter selection on validation data. Future room temperatures are
recursive predictions. Outdoor and control inputs are historical oracle inputs.
"""
import math
from datetime import timedelta

def compare(points, solve):
    hour=timedelta(hours=1)
    times=sorted(points)
    cutoff=times[int(len(times)*0.7)]
    train=[t for t in times if t+hour<cutoff and all(t+h*hour in points for h in range(-6,2))]
    starts=[t for t in times if t>=cutoff and all(t+h*hour in points for h in range(-6,25))]
    if len(train)<168: raise ValueError('För få sammanhängande träningstimmar med sex timmars förhistorik.')
    if not starts: raise ValueError('Inga gemensamma 24-timmars testfönster med sex timmars förhistorik. Välj en period med färre luckor.')

    def features(t, temperatures, lag):
        temp=temperatures[t];out,u=points[t][1:]
        x=[1.0,out-temp,temp,u]
        if lag:
            x += [temp-temperatures[t-hour],temperatures[t-hour]-temperatures[t-2*hour],
                  sum(points[t-i*hour][2] for i in range(1,7))/6-u,
                  points[t-hour][2]-u]
        return x

    actual={t:v[0] for t,v in points.items()}
    models=[]
    for lag in (False,True):
        xs=[features(t,actual,lag) for t in train]
        ys=[actual[t+hour]-actual[t] for t in train]
        count=len(xs);width=len(xs[0])
        means=[0]+[sum(x[i] for x in xs)/count for i in range(1,width)]
        scales=[1]+[max(1e-6,math.sqrt(sum((x[i]-means[i])**2 for x in xs)/count)) for i in range(1,width)]
        standardized=[[(v-m)/s for v,m,s in zip(x,means,scales)] for x in xs]
        a=[[sum(x[i]*x[j] for x in standardized)/count for j in range(width)] for i in range(width)]
        # Fixed ridge penalty for lag model only, selected before validation.
        if lag:
            for i in range(1,width): a[i][i]+=0.01
        b=[sum(x[i]*y for x,y in zip(standardized,ys))/count for i in range(width)]
        coef=solve(a,b)
        models.append({'name':'lag6' if lag else 'linear','coefficients':coef,'means':means,'scales':scales,'ridge':0.01 if lag else 0})

    errors={name:{h:[] for h in (1,6,12,24)} for name in ('linear','lag6','persistence')}
    example=[]
    for t in starts:
        trajectories=[]
        for index,model in enumerate(models):
            temperatures={t-i*hour:actual[t-i*hour] for i in range(7)}
            path=[]
            for h in range(24):
                current=t+h*hour
                x=features(current,temperatures,index==1)
                prediction=temperatures[current]+sum(c*(v-m)/s for c,v,m,s in zip(model['coefficients'],x,model['means'],model['scales']))
                if not math.isfinite(prediction) or abs(prediction)>1000:
                    raise ValueError('En kandidat är instabil i valideringen. Ingen modell sparades eller aktiverades.')
                temperatures[current+hour]=prediction;path.append(prediction)
            trajectories.append(path)
        for h in (1,6,12,24):
            truth=actual[t+h*hour]
            errors['linear'][h].append(abs(trajectories[0][h-1]-truth))
            errors['lag6'][h].append(abs(trajectories[1][h-1]-truth))
            errors['persistence'][h].append(abs(actual[t]-truth))
        if not example:
            example=[{'time':(t+(h+1)*hour).isoformat(),'actual':actual[t+(h+1)*hour],
                      'predicted':trajectories[0][h],'lagged':trajectories[1][h]} for h in range(24)]
    metrics=[]
    for h in (1,6,12,24):
        mean=lambda key:sum(errors[key][h])/len(starts)
        metrics.append({'hours':h,'windows':len(starts),'mae':mean('linear'),'lag_mae':mean('lag6'),'baseline_mae':mean('persistence')})
    return {'models':models,'coefficients':models[0]['coefficients'],'metrics':metrics,'example':example,
            'training_pairs':len(train),'split_at':cutoff.isoformat(),'common_windows':len(starts),
            'validation_start':starts[0].isoformat(),'validation_end':(starts[-1]+24*hour).isoformat(),
            'comparison':'Alla modeller och horisonter använder samma starttider, med sex timmars förhistorik och 24 kompletta framtida timmar. Överlappande fönster är inte oberoende försök.'}

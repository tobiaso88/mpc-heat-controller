"""Offline candidate model. Historical inputs are known, not weather forecasts.

No model from this module is enabled for live prediction or actuation.
"""
import csv
import io
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta

def solve(a, b):
    m = [list(row) + [v] for row, v in zip(a, b)]
    for i in range(len(b)):
        pivot = max(range(i, len(b)), key=lambda j: abs(m[j][i]))
        m[i], m[pivot] = m[pivot], m[i]
        if abs(m[i][i]) < 1e-10: raise ValueError('För lite variation för att identifiera modellen.')
        divisor = m[i][i]
        m[i] = [v/divisor for v in m[i]]
        for j in range(len(b)):
            if j != i:
                scale = m[j][i]
                m[j] = [v-scale*w for v,w in zip(m[j],m[i])]
    return [row[-1] for row in m]

def evaluate(content, mapping):
    indoor = mapping.get('indoor', [])
    outside, signal = mapping.get('outdoor'), mapping.get('signal')
    if not isinstance(indoor,list) or not indoor or len(indoor)>20 or any(not isinstance(x,str) for x in indoor):
        raise ValueError('Välj minst en historisk rumsgivare.')
    selected = indoor + [outside,signal]
    if any(not isinstance(x,str) or not x for x in selected) or len(set(selected)) != len(selected):
        raise ValueError('Välj olika entiteter för rum, utetemperatur och historisk styrsignal.')
    try:
        start = datetime.fromisoformat(mapping['start']).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(mapping['end']).replace(tzinfo=timezone.utc)+timedelta(days=1)
    except (KeyError,TypeError,ValueError): raise ValueError('Ange från- och tilldatum.')
    if start>=end: raise ValueError('Ogiltig tidsperiod.')
    reader=csv.DictReader(io.StringIO(content))
    if not {'entity_id','state','last_changed'}.issubset(reader.fieldnames or []): raise ValueError('CSV-kolumner saknas.')
    buckets=defaultdict(lambda:defaultdict(list)); invalid=0
    for row in reader:
        if row['entity_id'] not in selected: continue
        try:
            stamp=datetime.fromisoformat(row['last_changed'].replace('Z','+00:00'))
            value=float(row['state'])
            if stamp.tzinfo is None or not math.isfinite(value): raise ValueError()
            stamp=stamp.astimezone(timezone.utc)
            if start<=stamp<end:
                buckets[stamp.replace(minute=0,second=0,microsecond=0)][row['entity_id']].append(value)
        except (TypeError,ValueError): invalid+=1
    points={}
    for stamp, entities in buckets.items():
        if all(e in entities for e in selected):
            mean=lambda e:sum(entities[e])/len(entities[e])
            points[stamp]=(sum(mean(e) for e in indoor)/len(indoor),mean(outside),mean(signal))
    times=sorted(points)
    if len(times)<240: raise ValueError('Minst 240 kompletta timvärden behövs. Välj en längre gemensam period.')
    cutoff=times[int(len(times)*0.7)]
    pairs=[t for t in times if t+timedelta(hours=1) in points]
    train=[t for t in pairs if t+timedelta(hours=1)<cutoff]
    if len(train)<168: raise ValueError('För få sammanhängande träningstimmar.')
    # dT = intercept + a*(Tout-Tin) + b*(Tin) + c*(fake Tout)
    features=lambda temp,out,u:[1.0,out-temp,temp,u]
    xs=[features(*points[t]) for t in train]
    ys=[points[t+timedelta(hours=1)][0]-points[t][0] for t in train]
    a=[[sum(x[i]*x[j] for x in xs) for j in range(4)] for i in range(4)]
    b=[sum(x[i]*y for x,y in zip(xs,ys)) for i in range(4)]
    coefficients=solve(a,b)
    predict=lambda temp,out,u:temp+sum(c*x for c,x in zip(coefficients,features(temp,out,u)))
    metrics=[]; example=[]
    for horizon in (1,6,12,24):
        errors=[]; baseline=[]
        for t in times:
            if t<cutoff or any(t+timedelta(hours=h) not in points for h in range(horizon+1)): continue
            temp=points[t][0]; path=[]
            for h in range(horizon):
                current=t+timedelta(hours=h)
                temp=predict(temp,points[current][1],points[current][2])
                truth=points[current+timedelta(hours=1)][0]
                path.append({'time':(current+timedelta(hours=1)).isoformat(),'predicted':round(temp,3),'actual':round(truth,3)})
            if not math.isfinite(temp) or abs(temp)>1000: raise ValueError('Modellen är instabil i valideringen.')
            errors.append(abs(temp-truth));baseline.append(abs(points[t][0]-truth))
            if horizon==24 and not example:example=path
        metrics.append({'hours':horizon,'windows':len(errors),'mae':sum(errors)/len(errors) if errors else None,
                        'baseline_mae':sum(baseline)/len(baseline) if baseline else None})
    return {'status':'offline_candidate','coefficients':coefficients,'complete_hours':len(times), 'training_pairs':len(train),
            'split_at':cutoff.isoformat(),'invalid_rows':invalid,'incomplete_hours':len(buckets)-len(points),
            'metrics':metrics,'example':example,'mapping':mapping,
            'message':'Offline kandidat, inte aktiverad. Validering använder känd historisk utetemperatur och beräknad styrsignal. Det mäter inte effekten av alternativ MPC-styrning.',
            'aggregation':'Aritmetiska timmedel av tillgängliga rader. Luckor fylls inte. Blandad råhistorik och timstatistik kan ge olika viktning.'}

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
    return evaluate_points(points, mapping, invalid_rows=invalid,
                           incomplete_hours=len(buckets)-len(points), source='csv')

def evaluate_points(points, mapping, invalid_rows=0, incomplete_hours=0, source='local_log'):
    times=sorted(points)
    if len(times)<240: raise ValueError('Minst 240 kompletta timvärden behövs. Välj en längre gemensam period.')
    from .comparison import compare
    result = compare(points, solve)
    return dict(result, status="offline_candidate", complete_hours=len(times), invalid_rows=invalid_rows,
                incomplete_hours=incomplete_hours, mapping=mapping, source=source,
                message="Offlinejämförelse, inte aktiverad. Använder känd historisk utetemperatur och styrsignal, inte alternativa MPC-kommandon.",
                aggregation="Aritmetiska timmedel. Luckor fylls inte. Blandad råhistorik och timstatistik kan ge olika viktning.")

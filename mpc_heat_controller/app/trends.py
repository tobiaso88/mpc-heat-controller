"""Read-only recent trends from the existing collector log."""
import json
import sqlite3
from datetime import datetime,timezone,timedelta

def read(data):
    path=data/'measurements.sqlite'
    if not path.exists():return {'points':[],'message':'Ingen mätlogg ännu. Loggning sker i skuggläge och aktiv PI.'}
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=48)).isoformat()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        has_pi=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pi_samples'").fetchone()
        has_mpc=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mpc_samples'").fetchone()
        query='SELECT s.time,s.readings,s.settings,'+('p.result' if has_pi else 'NULL')+','+('m.result' if has_mpc else 'NULL')+' FROM samples s'
        if has_pi:query+=' LEFT JOIN pi_samples p ON p.time=s.time'
        if has_mpc:query+=' LEFT JOIN mpc_samples m ON m.time=s.time'
        query+=' WHERE s.time>=? ORDER BY s.time'
        rows=db.execute(query,(cutoff,)).fetchall()
    buckets={}
    for stamp,raw,config,pi,mpc in rows:
        try:
            c=json.loads(config);items=json.loads(raw);regulator=json.loads(pi) if pi else {}
            mpc_result=json.loads(mpc) if mpc else {};mpc_plan=mpc_result.get('plan') or []
            values={r['entity']:r['value'] for r in items if r.get('quality')=='OK' and r.get('value') is not None}
            ids=c.get('indoor',[])
            inside=sum(values[e] for e in ids)/len(ids) if ids and all(e in values for e in ids) else None
            time=datetime.fromisoformat(stamp).timestamp()
            buckets[int(time//600)]={'datetime':stamp,'indoor':inside,'target':c.get('target'),
                'outdoor':values.get(c.get('outdoor')),'applied':values.get(c.get('applied_signal')),
                'pump_outdoor':values.get(c.get('pump_outdoor')),
                'proposal':(regulator or {}).get('signal'),
                'mpc_proposal':mpc_result.get('signal',mpc_plan[0].get('signal') if mpc_plan else None),
                'group':json.dumps([ids,c.get('outdoor'),c.get('applied_signal'),c.get('pump_outdoor')])}
        except (ValueError,KeyError,TypeError):continue
    return {'points':list(buckets.values()),'message':'Senaste 48 timmarna · sista loggade värdet per 10 minuter. Luckor och ändrade givarval bryter linjerna.'}

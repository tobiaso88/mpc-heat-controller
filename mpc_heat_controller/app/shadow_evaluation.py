"""Retrospective checks of archived shadow proposals, never counterfactual control tests."""
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def _nearest(samples, target, mapping, entity):
    best = None
    for stamp, readings, settings in samples:
        if (settings.get('indoor') != mapping['indoor'] or
                settings.get('outdoor') != mapping['outdoor'] or
                settings.get('solar', '') != mapping['solar'] or
                settings.get('cloud', '') != ('' if mapping.get('cloud_auto') else mapping['cloud']) or
                settings.get('pv_power', '') != mapping.get('pv_power', '') or
                settings.get('pv_forecast', '') != mapping.get('pv_forecast', '') or
                mapping.get('cloud_auto') and settings.get('weather') != mapping.get('weather')):
            continue
        distance = abs((_time(stamp)-target).total_seconds())
        if distance > 1800 or best is not None and distance >= best[0]:
            continue
        values = {r['entity']: r['value'] for r in readings
                  if r.get('quality') == 'OK' and isinstance(r.get('value'), (int, float))
                  and not isinstance(r['value'], bool) and math.isfinite(r['value'])}
        if entity == 'indoor':
            ids = mapping['indoor']
            value = sum(values[e] for e in ids)/len(ids) if ids and all(e in values for e in ids) else None
        else:
            value = values.get(entity)
        if value is not None:
            best = (distance, value, stamp)
    return best[1:] if best else (None, None)


def read(data, limit=24, before=None):
    """Latest matured proposals, each compared to nearby valid observations."""
    path = data/'measurements.sqlite'
    if not path.exists():
        return {'kind':'shadow_proposal_evaluation','proposals':[], 'message':'Ingen arkiverad prognos ännu.'}
    matured = datetime.now(timezone.utc)-timedelta(hours=27)
    cutoff = min(_time(before), matured) if before else matured
    limit = max(1, min(int(limit), 100))
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mpc_forecasts'").fetchone():
            return {'kind':'shadow_proposal_evaluation','proposals':[], 'message':'Ingen arkiverad prognos ännu.'}
        archives = db.execute('SELECT time,entity,fetched_at,fields,forecast,plan,mapping FROM mpc_forecasts WHERE time<=? ORDER BY time DESC LIMIT ?', (cutoff.isoformat(),limit)).fetchall()
        if not archives: return {'kind':'shadow_proposal_evaluation','proposals':[], 'message':'Inga skuggförslag har ännu hunnit följas upp under ett dygn.'}
        earliest = min(_time(row[0]) for row in archives)-timedelta(hours=1)
        latest = max(_time(row[0]) for row in archives)+timedelta(hours=27)
        raw = db.execute('SELECT time,readings,settings FROM samples WHERE time BETWEEN ? AND ? ORDER BY time', (earliest.isoformat(),latest.isoformat())).fetchall()
    samples = [(stamp,json.loads(readings),json.loads(settings)) for stamp,readings,settings in raw]
    proposals=[]
    for stamp,entity,fetched,fields,forecast,plan,mapping in archives:
        mapping=json.loads(mapping);forecast=json.loads(forecast);plan=json.loads(plan)
        points=[]
        for index,(weather,predicted) in enumerate(zip(forecast,plan)):
            outside, outside_at = _nearest(samples,_time(weather['datetime']),mapping,mapping['outdoor'])
            inside, inside_at = _nearest(samples,_time(predicted['datetime']),mapping,'indoor')
            item={'hours':index+1,'weather_time':weather['datetime'],'indoor_time':predicted['datetime'],
                  'forecast_outdoor':weather['temperature'],'actual_outdoor':outside,'actual_outdoor_at':outside_at,
                  'predicted_indoor':predicted['indoor'],'actual_indoor':inside,'actual_indoor_at':inside_at,
                  'proposed_signal':predicted['signal']}
            for field,key in (('solar_irradiance',mapping['solar']),('cloud_coverage',mapping['cloud'])):
                if field in weather:
                    actual, actual_at = _nearest(samples,_time(weather['datetime']),mapping,key) if key else (None,None)
                    item['forecast_'+field]=weather[field]
                    item['actual_'+field]=actual
                    item['actual_'+field+'_at']=actual_at
            if 'pv_power' in weather:
                actual, actual_at = _nearest(samples,_time(weather['datetime']),mapping,mapping.get('pv_power')) if mapping.get('pv_power') else (None,None)
                item['forecast_pv_power'] = weather['pv_power']
                item['actual_pv_power'] = actual
                item['actual_pv_power_at'] = actual_at
            points.append(item)
        def mae(forecast_key,actual_key):
            errors=[abs(p[forecast_key]-p[actual_key]) for p in points if p.get(actual_key) is not None and p.get(forecast_key) is not None]
            return round(sum(errors)/len(errors),3) if errors else None
        proposals.append({'time':stamp,'weather_entity':entity,'forecast_fetched_at':fetched,
                          'pv_forecast_source':mapping.get('pv_forecast'),'pv_forecast_fetched_at':mapping.get('pv_fetched_at'),
                          'fields':json.loads(fields),'points':points,
                          'outdoor_mae':mae('forecast_outdoor','actual_outdoor'),
                          'pv_power_mae':mae('forecast_pv_power','actual_pv_power'),
                          'indoor_mae':mae('predicted_indoor','actual_indoor')})
    return {'kind':'shadow_proposal_evaluation','proposals':proposals,
            'next_before':(datetime.fromisoformat(proposals[-1]['time'])-timedelta(microseconds=1)).isoformat() if proposals else None,
            'message':'Utvärdering av skuggförslag mot senare mätvärden. Innetemperaturen påverkades av faktisk PI-styrning och väder; resultatet visar inte effekten av alternativa MPC-kommandon.'}

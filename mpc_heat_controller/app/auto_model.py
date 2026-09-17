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

MIN_COMPLETE_HOURS = 250
CHECK_INTERVAL = timedelta(hours=1)
TRAIN_INTERVAL = timedelta(hours=24)


def mapping_for(config):
    mapping = {'indoor': sorted(config.get('indoor', [])),
               'outdoor': config.get('outdoor', ''),
               'signal': config.get('applied_signal', ''), 'solar': config.get('solar', ''), 'cloud': config.get('cloud', '')}
    if not mapping['indoor'] or not mapping['outdoor'] or not mapping['signal']:
        return None
    if mapping['solar'] and mapping['cloud']: return None
    selected = mapping['indoor'] + [mapping['outdoor'], mapping['signal']] + list(filter(None, [mapping['solar'], mapping['cloud']]))
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
    wanted = mapping['indoor'] + [mapping['outdoor'], mapping['signal']] + ([mapping['solar']] if mapping.get('solar') else [mapping['cloud']] if mapping.get('cloud') else [])
    buckets = defaultdict(lambda: defaultdict(list))
    excluded = 0
    for stamp, raw_readings, raw_settings in rows:
        try:
            settings = json.loads(raw_settings)
            if (sorted(settings.get('indoor', [])) != mapping['indoor'] or
                    settings.get('outdoor') != mapping['outdoor'] or
                    settings.get('applied_signal') != mapping['signal'] or settings.get('solar', '') != mapping.get('solar', '') or settings.get('cloud', '') != mapping.get('cloud', '')):
                excluded += 1
                continue
            moment = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if moment.tzinfo is None:
                raise ValueError()
            hour = moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            values = {r['entity']: r['value'] for r in json.loads(raw_readings)
                      if (r.get('quality') == 'OK' or r.get('entity') == mapping['signal'] and mapping['signal'].startswith('number.'))
                      and isinstance(r.get('value'), (int, float))
                      and not isinstance(r.get('value'), bool) and math.isfinite(r['value'])}
            for entity in wanted:
                if entity in values:
                    buckets[hour][entity].append(values[entity])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            excluded += 1
    points = {}
    for hour, entities in buckets.items():
        if all(entities.get(entity) for entity in wanted):
            mean = lambda entity: sum(entities[entity]) / len(entities[entity])
            points[hour] = (sum(mean(entity) for entity in mapping['indoor']) / len(mapping['indoor']),
                            mean(mapping['outdoor']), mean(mapping['signal'])) + ((mean(mapping['solar']),) if mapping.get('solar') else (mean(mapping['cloud']),) if mapping.get('cloud') else ())
    return points, {'samples': len(rows), 'incomplete_hours': len(buckets) - len(points),
                    'excluded_samples': excluded}


def _predict(model, values):
    return sum(c * (v-m) / s for c, v, m, s in
               zip(model['coefficients'], values, model['means'], model['scales']))


def forecast_day(forecast):
    if len(forecast) < 24: return []
    try:
        times = [datetime.fromisoformat(row['datetime'].replace('Z', '+00:00')) for row in forecast[:24]]
        if any(t.tzinfo is None for t in times): return []
        now = datetime.now(timezone.utc)
        if not now-timedelta(minutes=30) <= times[0] <= now+timedelta(hours=2): return []
        if any(not timedelta(minutes=30) <= b-a <= timedelta(minutes=90) for a,b in zip(times,times[1:])): return []
        if times[-1] < now+timedelta(hours=22): return []
        if any(not math.isfinite(float(row['temperature'])) for row in forecast[:24]): return []
        return forecast[:24]
    except (KeyError, TypeError, ValueError, AttributeError): return []


def live_plan(report, indoor, applied, forecast, config, output=None):
    """Display-only hourly plan; each signal is on the output's 0.5 °C grid."""
    day = forecast_day(forecast)
    if not report or indoor is None or applied is None or not day or not output or output.get('state') != 'confirmed':
        return []
    try:
        model = next(m for m in report['models'] if m['name'] == 'linear')
        solar = bool(report.get('mapping', {}).get('solar'))
        cloud = bool(report.get('mapping', {}).get('cloud'))
        field = 'solar_irradiance' if solar else 'cloud_coverage' if cloud else None
        if field and any(field not in row for row in day): return []
        lo=max(float(output['min']),float(config['signal_min']))
        hi=min(float(output['max']),float(config['signal_max']))
        first=math.ceil(lo*2-1e-9);last=math.floor(hi*2+1e-9)
        if first>last or not lo<=applied<=hi: return []
        candidates=[(0.0,float(indoor),float(applied),[])]
        for row in day:
            outside=float(row['temperature']);expanded=[]
            for cost, temperature, previous, path in candidates:
                allowance=min(float(config['max_step']),float(config['pi_rate']))
                for tick in range(max(first,math.ceil((previous-allowance)*2-1e-9)),
                                  min(last,math.floor((previous+allowance)*2+1e-9))+1):
                    signal=tick/2
                    features=[1.0,outside-temperature,temperature,signal]
                    if field: features.append(float(row[field]))
                    predicted=temperature+_predict(model,features)
                    if not math.isfinite(predicted) or abs(predicted)>100: continue
                    violation=max(config['comfort_min']-predicted,0,predicted-config['comfort_max'])
                    score=cost+(predicted-config['target'])**2+20*violation**2+0.02*(signal-previous)**2
                    expanded.append((score,predicted,signal,path+[{'datetime':row['datetime'],
                        'indoor':round(predicted,3),'outdoor':round(outside,2),'signal':signal}]))
            if not expanded: return []
            candidates=sorted(expanded,key=lambda item:item[0])[:60]
        return candidates[0][3]
    except (KeyError,TypeError,ValueError,OverflowError,StopIteration): return []


def signal_effect(report):
    """Indoor degrees per hour from one degree higher simulated outdoor signal."""
    try:
        model = next(m for m in report['models'] if m['name'] == 'linear')
        scale = float(model['scales'][3])
        if not math.isfinite(scale) or scale <= 0:
            return None
        effect = float(model['coefficients'][3]) / scale
        return effect if math.isfinite(effect) else None
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, StopIteration):
        return None


def quality(report):
    """Conservative oracle-weather gate; deployment forecast quality is still unknown."""
    effect = signal_effect(report)
    if effect is None or not -0.5 <= effect <= -0.001:
        return False
    metrics={m['hours']:m for m in report['metrics']}
    for h in (6,12,24):
        m=metrics[h]
        if m['windows']<24 or m['mae']>0.8 or m['mae']>0.85*m['baseline_mae']:
            return False
    return True


class AutoModel:
    def __init__(self, data):
        self.data = data
        self.lock = threading.Lock()
        self.last_check = None

    def maybe_train(self, config, clock=None):
        clock = clock or datetime.now(timezone.utc)
        if not self.lock.acquire(blocking=False):
            return
        try:
            saved = model_store.load_auto(self.data)
            current_mapping = mapping_for(config)
            same_model = bool(saved and saved.get('report') and saved['report'].get('mapping') == current_mapping)
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
            mapping = current_mapping
            if config.get('mode') != 'shadow' or not mapping:
                model_store.save_auto_status(self.data, {
                    'state': 'waiting', 'message': 'Välj skuggläge, rumsgivare, utegivare och Ohmigos inställda värde för automatisk träning.',
                    'checked_at': clock.isoformat(), 'complete_hours': 0})
                return
            points, info = local_points(self.data, mapping)
            status = {'state': 'collecting', 'checked_at': clock.isoformat(),
                      'complete_hours': len(points), **info, 'mapping': mapping}
            if len(points) < MIN_COMPLETE_HOURS:
                status['message'] = f'Samlar data: {len(points)} av minst {MIN_COMPLETE_HOURS} kompletta timmar.'
                model_store.save_auto_status(self.data, status)
                return
            try:
                report = evaluate_points(points, mapping, incomplete_hours=info['incomplete_hours'], source='local_log')
                if not quality(report): raise ValueError('Modellkvaliteten räcker inte: styrsignalens skalade effekt måste vara mellan −0,5 och −0,001 °C/h per °C; 6, 12 och 24 h kräver minst 24 fönster, MAE ≤0,8 °C och minst 15 % lägre MAE än temperaturpersistens.')
                status.update({'state': 'ready', 'message': 'Automatisk modell tränad och validerad från appens mätlogg.',
                               'trained_at': clock.isoformat()})
                model_store.save_auto(self.data, report, status)
            except ValueError as error:
                status.update({'state': 'needs_data', 'message': str(error)})
                model_store.save_auto_status(self.data, status)
        finally:
            self.lock.release()

    def result(self, config, readings, forecast, states=None, control=None):
        saved = model_store.load_auto(self.data)
        if not saved:
            return {'state': 'waiting', 'message': 'Väntar på första kontrollen av mätloggen.', 'plan': []}
        status = saved.get('status') or {}
        report = saved.get('report')
        mapping = mapping_for(config)
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
        valid_model = bool(report and quality(report))
        plan = live_plan(report if status.get('state') == 'ready' and valid_model else None, inside, applied, forecast, config, output)
        message = status.get('message', '')
        if report and status.get('state') == 'ready' and not valid_model:
            message += ' Sparad modell har felvänd eller orimlig styrverkan och spärras.'
        elif report and status.get('state') == 'ready' and not plan:
            message += ' Liveförslag väntar på färsk, bekräftad number-utgång, giltiga mätvärden och 24 timmars matchande väderprognos.'
        elif plan:
            message += ' MPC-förslaget är endast skuggläge och skickas inte.'
        return dict(status, report=report, plan=plan, message=message)

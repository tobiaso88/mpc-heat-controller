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
               'signal': config.get('applied_signal', '')}
    if not mapping['indoor'] or not mapping['outdoor'] or not mapping['signal']:
        return None
    if len(set(mapping['indoor'] + [mapping['outdoor'], mapping['signal']])) != len(mapping['indoor']) + 2:
        return None
    return mapping


def local_points(data, mapping):
    """Aggregate valid snapshots for the current sensor selection into UTC hours."""
    path = data / 'measurements.sqlite'
    if not path.exists():
        return {}, {'samples': 0, 'incomplete_hours': 0, 'excluded_samples': 0}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT time, readings, settings FROM samples ORDER BY time').fetchall()
    wanted = mapping['indoor'] + [mapping['outdoor'], mapping['signal']]
    buckets = defaultdict(lambda: defaultdict(list))
    excluded = 0
    for stamp, raw_readings, raw_settings in rows:
        try:
            settings = json.loads(raw_settings)
            if (sorted(settings.get('indoor', [])) != mapping['indoor'] or
                    settings.get('outdoor') != mapping['outdoor'] or
                    settings.get('applied_signal') != mapping['signal']):
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
                            mean(mapping['outdoor']), mean(mapping['signal']))
    return points, {'samples': len(rows), 'incomplete_hours': len(buckets) - len(points),
                    'excluded_samples': excluded}


def _predict(model, values):
    return sum(c * (v-m) / s for c, v, m, s in
               zip(model['coefficients'], values, model['means'], model['scales']))


def live_plan(report, indoor, applied, forecast, config):
    """Optimize a display-only 24-hour plan with the validated linear model."""
    if not report or indoor is None or applied is None or len(forecast) < 24:
        return []
    try:
        model = next(m for m in report['models'] if m['name'] == 'linear')
        candidates = [(0.0, float(indoor), float(applied), [])]
        step = float(config['max_step'])
        for row in forecast[:24]:
            outside = float(row['temperature'])
            expanded = []
            for cost, temperature, previous, path in candidates:
                for delta in (-step, 0.0, step):
                    signal = min(config['signal_max'], max(config['signal_min'], previous + delta))
                    change = _predict(model, [1.0, outside-temperature, temperature, signal])
                    predicted = temperature + change
                    if not math.isfinite(predicted) or abs(predicted) > 100:
                        continue
                    violation = max(config['comfort_min']-predicted, 0, predicted-config['comfort_max'])
                    score = cost + (predicted-config['target'])**2 + 20*violation**2 + 0.02*delta**2
                    expanded.append((score, predicted, signal, path + [{
                        'datetime': row['datetime'], 'indoor': round(predicted, 3),
                        'outdoor': round(outside, 2), 'signal': round(signal, 2)}]))
            if not expanded:
                return []
            candidates = sorted(expanded, key=lambda item: item[0])[:60]
        return candidates[0][3]
    except (KeyError, TypeError, ValueError, StopIteration):
        return []


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
                status.update({'state': 'ready', 'message': 'Automatisk modell tränad och validerad från appens mätlogg.',
                               'trained_at': clock.isoformat()})
                model_store.save_auto(self.data, report, status)
            except ValueError as error:
                status.update({'state': 'needs_data', 'message': str(error)})
                model_store.save_auto_status(self.data, status)
        finally:
            self.lock.release()

    def result(self, config, readings, forecast):
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
        plan = live_plan(report if status.get('state') == 'ready' else None, inside, applied, forecast, config)
        message = status.get('message', '')
        if report and status.get('state') == 'ready' and not plan:
            message += ' Liveförslag väntar på giltiga mätvärden och 24 timmars väderprognos.'
        elif plan:
            message += ' MPC-förslaget är endast skuggläge och skickas inte.'
        return dict(status, report=report, plan=plan, message=message)

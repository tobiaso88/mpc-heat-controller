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
from .comparison import features
from .pv_history import recovered_zeros

QUALITY_VERSION = 3
QUALITY_HORIZONS = (6, 12, 24)
# Absolute limit for display-only shadow proposals, still bounded by persistence.
MAX_MAE = 0.35
HARD_MAX_MAE = 0.8
MIN_WINDOWS = 24
RESPONSE_HOURS = 12
MIN_SIGNAL_RESPONSE = -6.0
MAX_SIGNAL_RESPONSE = 0.0

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
                    'excluded_samples': excluded, 'recovered_pv_zero_samples': len(recovered)}


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


def live_plan(report, indoor, applied, forecast, config, output=None, history=None, blockers=None):
    """Display-only hourly plan; each signal is on the output's 0.5 °C grid."""
    def reject(reason):
        if blockers is not None: blockers.append(reason)
        return []
    day = forecast_day(forecast, blockers)
    if not report: return reject('Ingen godkänd modell är tillgänglig.')
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
                    predicted=temperature+_predict(model,features(points,current,temperatures,lag))
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


def quality(report, name=None):
    """Return rejection reasons; an empty list means the candidate passes."""
    name = name or (report or {}).get('selected_model', 'linear')
    reasons = []
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
        if not math.isfinite(baseline) or mae > baseline:
            reasons.append(f'{name} {h} h: MAE {mae} °C > persistens {baseline} °C.')
    return reasons


def select_model(report):
    checks = {m['name']: quality(report, m['name']) for m in report['models'] if m['name'] in ('linear', 'lag6')}
    report['quality_version'] = QUALITY_VERSION
    report['quality_reasons'] = checks
    approved = [name for name, reasons in checks.items() if not reasons]
    report['selected_model'] = min(approved, key=lambda name: sum(
        m['lag_mae' if name == 'lag6' else 'mae'] for m in report['metrics'] if m['hours'] in QUALITY_HORIZONS)) if approved else None
    return bool(approved)


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
            status = {'state': 'collecting', 'checked_at': clock.isoformat(),
                      'complete_hours': len(points), **info, 'mapping': mapping}
            if len(points) < MIN_COMPLETE_HOURS:
                status['message'] = f'Samlar data: {len(points)} av minst {MIN_COMPLETE_HOURS} kompletta timmar.'
                model_store.save_auto_status(self.data, status)
                return
            try:
                try:
                    report = evaluate_points(points, mapping, incomplete_hours=info['incomplete_hours'], source='local_log')
                except ValueError as error:
                    status['quality_reasons'] = {'temperature': {'validation': [str(error)]}}
                    if not config.get('pv_power'):
                        raise
                    report = None
                status['evaluations'] = {'temperature': report}
                if report is None or not select_model(report):
                    report = None
                automatic = mapping_for(config, auto_cloud=True)
                if automatic and automatic.get('cloud_auto'):
                    cloud_points, cloud_info = local_points(self.data, automatic)
                    if len(cloud_points) >= MIN_COMPLETE_HOURS and max(cloud_points) >= clock-timedelta(hours=2):
                        try:
                            cloud_report = evaluate_points(cloud_points, automatic,
                                incomplete_hours=cloud_info['incomplete_hours'], source='local_log')
                            status['evaluations']['cloud'] = cloud_report
                            if select_model(cloud_report):
                                cloud_report['fallback'] = report
                                report = cloud_report
                                status['complete_hours'] = len(cloud_points)
                                status['mapping'] = automatic
                        except ValueError:
                            pass
                pv_reason = ''
                if config.get('pv_power'):
                    pv_mapping = mapping_for(config)
                    pv_points, pv_info = local_points(self.data, pv_mapping)
                    if len(pv_points) >= MIN_COMPLETE_HOURS and max(pv_points) >= clock-timedelta(hours=2):
                        try:
                            pv_report = evaluate_points(pv_points, pv_mapping,
                                incomplete_hours=pv_info['incomplete_hours'], source='local_log')
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
                    else:
                        pv_reason = f' Solcellsmodellen samlar giltig produktionshistorik: {len(pv_points)} av minst {MIN_COMPLETE_HOURS} timmar.'
                if pv_reason and report is not None:
                    pv_reason += ' Temperaturmodellen används.'
                status.setdefault('quality_reasons', {}).update({key: value.get('quality_reasons', {}) for key, value in status['evaluations'].items() if value})
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
        return dict(status, report=report, plan=plan, message=message, plan_blockers=blockers if not plan else [])

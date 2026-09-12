"""Read-only HA telemetry and bounded local observation storage."""
import json
import math
import os
import sqlite3
import threading
import urllib.request
from .pi import PI
from .control import Control
from .entities import EntityPublisher
from .auto_model import AutoModel
from datetime import datetime, timezone, timedelta

def now():
    return datetime.now(timezone.utc)

def request(path, payload=None):
    token = os.environ.get('SUPERVISOR_TOKEN')
    if not token:
        raise ValueError('Home Assistant är inte ansluten.')
    req = urllib.request.Request('http://supervisor/core/api/' + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)

def readings(c, states):
    by_id = {s['entity_id']: s for s in states}
    roles = {}
    for role, ids in [('Reglering', c['indoor']), ('Uppföljning', c['observe']),
                      ('Utomhus', [c['outdoor']]), ('Framledning', [c['supply']]), ('Retur', [c['return']]),
                      ('Värmepumpens avlästa utetemperatur', [c.get('pump_outdoor', '')]),
                      ('Ohmigo inställt värde', [c.get('applied_signal', '')])]:
        for entity in filter(None, ids):
            roles.setdefault(entity, []).append(role)
    result = []
    for entity, role in roles.items():
        s = by_id.get(entity, {})
        attrs = s.get('attributes', {})
        value, quality = None, 'Saknas'
        stamp = s.get('last_reported') or s.get('last_updated')
        try:
            value = float(s['state'])
            if not math.isfinite(value): raise ValueError()
            if attrs.get('unit_of_measurement') != '°C':
                quality, value = 'Fel enhet', None
            else:
                age = (now() - datetime.fromisoformat(stamp.replace('Z', '+00:00'))).total_seconds()
                quality = 'OK' if -60 <= age <= 86400 else 'Rapporttid utanför tillåtet intervall (högst 24 h)'
                if attrs.get('restored'):quality='Återställt värde, väntar på rapport'
        except (ValueError, TypeError, KeyError, AttributeError):
            value = None
        result.append({'entity': entity, 'name': attrs.get('friendly_name', entity),
                       'roles': role, 'value': value, 'quality': quality, 'reported_at': stamp, 'timestamp_source': 'last_reported' if s.get('last_reported') else 'last_updated'})
    return result

def sync_target_climates(c,states,call=None):
    call=request if call is None else call
    entities=c.get('target_climates',[])
    if c['mode']!='shadow' or not entities:
        return {'state':'off','message':'Ingen synkning av rumstermostater är aktiv.'}
    by_id={s['entity_id']:s for s in states}
    updated=[];problems=[]
    for entity in entities:
        state=by_id.get(entity,{})
        try:
            current=float(state['attributes']['temperature'])
            if not math.isfinite(current) or state.get('state') in ('unknown','unavailable'):raise ValueError()
        except (KeyError,TypeError,ValueError):
            problems.append(entity+': börvärdet är inte tillgängligt')
            continue
        if abs(current-c['target'])<=0.01:continue
        try:
            call('services/climate/set_temperature',{'entity_id':entity,'temperature':c['target']})
            updated.append(entity)
        except Exception:
            problems.append(entity+': kunde inte ställas in')
    if problems:return {'state':'warning','message':'Termostatsynkning behöver åtgärd: '+'; '.join(problems)+'.'}
    message=f'{len(entities)} rumstermostat(er) följer {c["target"]:g} °C.'
    if updated:message+=' Uppdaterade: '+', '.join(updated)+'.'
    return {'state':'ok','message':message}

def normalize_forecast(response, entity, unit, clock=None):
    clock = clock or now()
    if unit not in ('°C', '°F'): raise ValueError('Väderentiteten saknar en stödd temperaturenhet.')
    rows = response.get('service_response', {}).get(entity, {}).get('forecast', [])
    points = {}
    for row in rows:
        try:
            t = datetime.fromisoformat(row['datetime'].replace('Z', '+00:00'))
            v = float(row['temperature'])
            if t.tzinfo is None or not math.isfinite(v): continue
            if not clock - timedelta(minutes=30) <= t <= clock + timedelta(hours=48): continue
            if unit == '°F': v = (v - 32) * 5 / 9
            points[t] = {'datetime': t.isoformat(), 'temperature': round(v, 2)}
        except (KeyError, ValueError, TypeError):
            continue
    ordered = sorted(points)
    if len(ordered) < 2 or ordered[0] > clock + timedelta(hours=2):
        raise ValueError('Ingen aktuell timprognos finns för den valda entiteten.')
    if any((b-a).total_seconds() > 5400 for a,b in zip(ordered, ordered[1:]) if b <= clock + timedelta(hours=24)):
        raise ValueError('Timprognosen har luckor under det första dygnet.')
    return [points[t] for t in ordered]

class Collector:
    def __init__(self, config, data):
        self.config, self.data = config, data
        self.lock = threading.Lock()
        self.snapshot = {'readings': [], 'forecast': {'points': [], 'message': 'Väntar på första hämtningen.'}, 'target_sync':{'state':'off','message':'Väntar på första kontrollen.'}, 'sampled_at': None, 'error': None}
        self.weather_key, self.weather_time = None, None
        self.forecast = {'points': [], 'message': 'Ingen väderentitet vald.'}
        self.wake = threading.Event()
        self.pi = PI()
        self.control = Control(request, data)
        self.cycle_lock = threading.RLock()
        self.publisher=EntityPublisher(request,config,self.get,data)
        self.auto_model=AutoModel(data)

    def cycle(self):
        with self.cycle_lock:
            self._cycle()

    def _cycle(self):
        c = self.config()
        try:
            states = request('states')
            items = readings(c, states)
            target_sync=sync_target_climates(c,states)
            if self.control.resume(c,states,items):self.pi.reset()
            if self.control.get()['active']:
                # A number state can remain unchanged for days; freshly read state is the initial setpoint, not a temperature sensor.
                self.control.target(c,states)
                for item in items:
                    if item['entity']==c['applied_signal'] and item['value'] is not None:item['quality']='OK'
            entity = c['weather']
            if entity != self.weather_key:
                self.forecast = {'points': [], 'message': 'Ingen väderentitet vald.'}
                self.weather_key, self.weather_time = entity, None
            if entity and (self.weather_time is None or (now()-self.weather_time).total_seconds() >= 1800):
                try:
                    state = next(s for s in states if s['entity_id'] == entity)
                    response = request('services/weather/get_forecasts?return_response', {'entity_id': entity, 'type': 'hourly'})
                    points = normalize_forecast(response, entity, state.get('attributes', {}).get('temperature_unit'))
                    self.weather_time = now()
                    self.forecast = {'points': points, 'entity': entity, 'fetched_at': self.weather_time.isoformat(),
                                     'message': 'Timprognos från Home Assistant. Hämtningstid är inte leverantörens publiceringstid.'}
                except Exception:
                    self.forecast = {'points': [], 'entity': entity, 'message': 'Kunde inte hämta aktuell timprognos. Kontrollera väderentiteten och stöd för timprognos.'}
            stamp = now().isoformat()
            snapshot = {'readings': items, 'forecast': self.forecast, 'target_sync':target_sync, 'sampled_at': stamp, 'error': None, 'logging': c['mode'] == 'shadow'}
            snapshot['pi'] = self.pi.step(c, items)
            required=set(c['indoor']+[c['outdoor']]+list(filter(None,[c['supply'],c['return']])))
            valid={r['entity'] for r in items if r['quality']=='OK' and r['value'] is not None}
            if self.control.get()['active'] and (not required.issubset(valid) or snapshot['pi']['signal'] is None):
                self.control.pause('PI pausad: ogiltiga givare.')
                self.pi.reset()
            if c['mode'] == 'shadow':
                self.store(stamp, items, c, snapshot['pi'])
                try:
                    self.auto_model.maybe_train(c)
                    snapshot['mpc']=self.auto_model.result(c,items,self.forecast.get('points',[]))
                    self.store_mpc(stamp,snapshot['mpc'])
                except (OSError, sqlite3.Error, ValueError):
                    snapshot['mpc']={'state':'error','message':'Automatisk modellträning kunde inte läsas eller sparas. PI fortsätter oförändrat.','plan':[]}
            else:
                snapshot['mpc']={'state':'off','message':'Automatisk modellträning är pausad i demoläge.','plan':[]}
            self.control.send(c,states,snapshot['pi'])
            if self.control.get()['active']:
                snapshot['pi']['message']='Aktiv PI. Temperaturkommandon skickas till vald utgång; se styrstatus.'
        except Exception:
            self.control.pause('PI pausad vid databortfall eller loggningsfel. Watchdog ska återgå till riktig utegivare.')
            self.pi.reset()
            snapshot = {'readings': [], 'forecast': {'points': [], 'message': 'Väderprognosen är inte tillgänglig.'},
                        'target_sync':{'state':'warning','message':'Termostatsynkning kan inte kontrolleras när HA-data saknas.'},
                        'mpc':{'state':'waiting','message':'MPC väntar på giltiga mätdata.','plan':[]},
                        'sampled_at': None, 'error': 'Kunde inte läsa eller logga data. Kontrollera HA-anslutningen och ledigt lagringsutrymme.', 'logging': False}
        snapshot['config_used']=c
        with self.lock: self.snapshot = snapshot

    def store(self, stamp, items, c, pi=None):
        self.data.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.data / 'measurements.sqlite') as db:
            db.execute('CREATE TABLE IF NOT EXISTS samples (time TEXT PRIMARY KEY, readings TEXT NOT NULL, settings TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO samples VALUES (?, ?, ?)', (stamp, json.dumps(items), json.dumps(c)))
            db.execute('DELETE FROM samples WHERE time < ?', ((now()-timedelta(days=90)).isoformat(),))
            db.execute('CREATE TABLE IF NOT EXISTS pi_samples (time TEXT PRIMARY KEY, result TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO pi_samples VALUES (?, ?)', (stamp, json.dumps(pi)))
            db.execute('DELETE FROM pi_samples WHERE time < ?', ((now()-timedelta(days=90)).isoformat(),))

    def store_mpc(self,stamp,result):
        plan=result.get('plan') or []
        stored={'state':result.get('state'),'signal':plan[0].get('signal') if plan else None}
        with sqlite3.connect(self.data/'measurements.sqlite') as db:
            db.execute('CREATE TABLE IF NOT EXISTS mpc_samples (time TEXT PRIMARY KEY, result TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO mpc_samples VALUES (?,?)',(stamp,json.dumps(stored)))
            db.execute('DELETE FROM mpc_samples WHERE time < ?',((now()-timedelta(days=90)).isoformat(),))

    def get(self):
        with self.lock: result=json.loads(json.dumps(self.snapshot))
        result['control']=self.control.get()
        result['entities']=self.publisher.get()
        return result

    def run(self):
        while True:
            self.wake.clear()
            self.cycle()
            state=self.control.get()
            self.wake.wait(60 if state['active'] or state['auto_restart_pending'] else 300)

    def start(self):
        self.publisher.start()
        threading.Thread(target=self.run, daemon=True).start()

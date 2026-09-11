"""Read-only MQTT Discovery telemetry, isolated from the control loop."""
import json
import math
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

SENSORS = {
    'pi_status': ('PI-status', None),
    'mode': ('Grundläge', None),
    'target': ('Börvärde', 'temperature'),
    'indoor': ('Medeltemperatur', 'temperature'),
    'proposal': ('PI föreslagen utetemperatur', 'temperature'),
    'last_sent': ('Senast skickad utetemperatur', 'temperature'),
    'last_sent_at': ('Senaste temperaturkommando', 'timestamp'),
    'error': ('Temperaturfel', 'delta'),
    'p': ('PI P-del', 'delta'),
    'i': ('PI I-del', 'delta'),
    'compensation': ('PI utetemperaturkompensation', 'delta'),
}

def identity(data):
    data.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(data/'entities.sqlite') as db:
        db.execute('CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO identity VALUES (1,?)',(uuid.uuid4().hex,))
        return db.execute('SELECT value FROM identity WHERE id=1').fetchone()[0]

def discovery(instance):
    topic=f'mpc_heat_controller/{instance}/state'
    device={'identifiers':[f'mpc_heat_controller_{instance}'],'name':'MPC Heat Controller',
            'manufacturer':'mpc-heat-controller','model':'PI heat controller','sw_version':'0.8.0'}
    configs={}
    for key,(name,kind) in SENSORS.items():
        config={'name':name,'unique_id':f'mpc_{instance}_{key}','device':device,
                'state_topic':topic,'value_template':"{{ value_json."+key+" if value_json."+key+" is not none else '' }}",
                'availability_topic':topic,'availability_template':"{{ 'online' if value_json."+key+" is not none else 'offline' }}",
                'expire_after':180,
                'json_attributes_topic':topic,
                'json_attributes_template':"{{ {'updated_at': value_json.updated_at, 'message': value_json.message} | tojson }}"}
        if kind in ('temperature','delta'):
            config.update(unit_of_measurement='°C',state_class='measurement')
        if kind in ('temperature','timestamp'):config['device_class']=kind
        configs[f'homeassistant/sensor/mpc_{instance}/{key}/config']=config
    return configs

def payload(c,snapshot,clock=None):
    clock=time.time() if clock is None else clock
    result={key:None for key in SENSORS}
    control=snapshot.get('control',{})
    result.update(mode=c['mode'],target=c['target'],updated_at=datetime.fromtimestamp(clock,timezone.utc).isoformat(),message=control.get('message',''))
    result['pi_status']='active' if control.get('active') else 'waiting' if control.get('auto_restart_pending') else 'error' if snapshot.get('error') or control.get('fault') else 'stopped'
    last=control.get('last_command')
    if last:
        result['last_sent']=last['value']
        result['last_sent_at']=datetime.fromtimestamp(last['sent_at'],timezone.utc).isoformat()
    try:
        age=clock-datetime.fromisoformat(snapshot['sampled_at']).timestamp()
        fresh=0<=age<=420 and not snapshot.get('error') and snapshot.get('config_used')==c
    except (KeyError,TypeError,ValueError):fresh=False
    if not fresh and control.get('active'):
        result['pi_status']='error'
        result['message']='Aktivering finns men färskt beräkningsunderlag saknas. Kontrollera appens driftstatus.'
    if fresh:
        valid={r['entity']:r['value'] for r in snapshot.get('readings',[]) if r['quality']=='OK' and isinstance(r['value'],(int,float)) and math.isfinite(r['value'])}
        if c['indoor'] and all(e in valid for e in c['indoor']):result['indoor']=sum(valid[e] for e in c['indoor'])/len(c['indoor'])
        pi=snapshot.get('pi',{})
        if c['mode']=='shadow' and pi.get('signal') is not None:
            for dest,src in [('proposal','signal'),('error','error'),('p','p'),('i','i'),('compensation','compensation')]:result[dest]=pi.get(src)
    return result

class EntityPublisher:
    def __init__(self,request,config,snapshot,data):
        self.request,self.config,self.snapshot,self.data=request,config,snapshot,data
        self.instance=None;self.discovered_at=None
        self.lock=threading.Lock()
        self.info={'message':'Väntar på MQTT-publicering.','ok':False}

    def publish(self,topic,value,retain=False):
        self.request('services/mqtt/publish',{'topic':topic,'payload':json.dumps(value,ensure_ascii=False,allow_nan=False),'qos':1,'retain':retain})

    def cycle(self):
        try:
            if self.instance is None:self.instance=identity(self.data)
            if self.discovered_at is None or time.monotonic()-self.discovered_at>=300:
                for topic,config in discovery(self.instance).items():self.publish(topic,config,True)
                self.discovered_at=time.monotonic()
            # Obtain fresh snapshot AFTER potentially slow discovery requests.
            self.publish(f'mpc_heat_controller/{self.instance}/state',payload(self.config(),self.snapshot()))
            info={'ok':True,'message':'Egna sensorer publicerade via HA MQTT. Leta efter enheten MPC Heat Controller under MQTT.','published_at':time.time()}
        except Exception:
            info={'ok':False,'message':'Kunde inte publicera egna sensorer. Kontrollera HA:s MQTT-integration, broker och MQTT Discovery. PI påverkas inte.'}
        with self.lock:self.info=info

    def get(self):
        with self.lock:return dict(self.info)

    def run(self):
        while True:
            self.cycle()
            time.sleep(60)

    def start(self):threading.Thread(target=self.run,daemon=True).start()

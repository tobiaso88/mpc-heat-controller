"""Explicit, process-local authority to send PI commands. Restart means disarmed."""
import math
import threading
import time
import json
import sqlite3
from datetime import datetime, timezone

class Control:
    def __init__(self, request, data=None):
        self.request=request
        self.lock=threading.RLock()
        self.armed=False
        self.settings=None
        self.last_sent=None
        self.changed_at=None
        self.last_command=None
        self.data=data
        self.resume_settings=None
        self.ready_since=None
        self.recovery_since=time.time()
        if data is not None and (data/'control.sqlite').exists():
            with sqlite3.connect(data/'control.sqlite') as db:
                row=db.execute('SELECT settings FROM intent WHERE id=1').fetchone()
            if row:self.resume_settings=json.loads(row[0])
        self.info={'active':False,'message':'Avstängd. Aktiv PI måste startas uttryckligen efter varje omstart.'}
        if self.resume_settings is not None:self.info={'active':False,'message':'Väntar på att givare och utgång ska bli redo för automatisk återstart.'}

    def persist(self, settings):
        if self.data is not None:
            self.data.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(self.data/'control.sqlite') as db:
                db.execute('CREATE TABLE IF NOT EXISTS intent (id INTEGER PRIMARY KEY, settings TEXT)')
                db.execute('DELETE FROM intent')
                if settings is not None:db.execute('INSERT INTO intent VALUES (1,?)',(json.dumps(settings),))
        self.resume_settings=settings

    def pause(self,message):
        with self.lock:
            if self.armed:self.recovery_since=time.time()
            self.armed=False;self.ready_since=None
            self.info={'active':False,'fault':True,'message':message+' Inga kommandon skickas. Väntar på nya giltiga mätvärden.'}

    def resume(self,c,states,items):
        with self.lock:
            if self.armed or self.resume_settings is None:return False
            if not c['auto_restart'] or c!=self.resume_settings:
                self.stop('Automatisk återstart avbruten: inställningarna har ändrats.');return False
            try:
                self.target(c,states)
                required=set(c['indoor']+[c['outdoor']]+list(filter(None,[c['supply'],c['return']])))
                by_id={r['entity']:r for r in items}
                raw={s['entity_id']:s for s in states}
                for entity in required:
                    r=by_id[entity]
                    stamp=datetime.fromisoformat(r['reported_at'].replace('Z','+00:00')).timestamp()
                    if r['quality']!='OK' or r['value'] is None or not math.isfinite(r['value']) or stamp<self.recovery_since or raw[entity].get('attributes',{}).get('restored'):
                        raise ValueError()
                if self.ready_since is None:self.ready_since=time.monotonic()
                if time.monotonic()-self.ready_since<60:
                    self.info={'active':False,'message':'Givare och utgång tillgängliga. Väntar på ny kontroll efter minst 60 sekunder; inget skickas.'};return False
                self.arm(c,states,items)
                return True
            except (ValueError,KeyError,TypeError,AttributeError):
                self.ready_since=None
                self.info={'active':False,'message':'Väntar på nya giltiga givarrapporter, tillgänglig Ohmigo och avstängd gammal automation. Inget skickas.'}
                return False

    def stop(self, message='Stoppad. Inga fler kommandon skickas; verifierad watchdog måste återgå till riktig utegivare.', fault=False):
        with self.lock:
            self.armed=False
            self.persist(None)
            self.ready_since=None
            self.info={'active':False,'fault':fault,'message':message}

    def target(self,c,states):
        by_id={s['entity_id']:s for s in states}
        if c['mode']!='shadow':raise ValueError('Välj skuggläge för att förbereda PI.')
        if not c['watchdog_verified'] or c['watchdog_seconds']<180:
            raise ValueError('Verifiera att uteblivna temperaturkommandon ger fallback och ange timeout, minst 180 sekunder.')
        if not c['exclusive_writer_confirmed']:raise ValueError('Bekräfta att alla andra skrivare är avstängda.')
        old=by_id.get(c['old_automation'],{})
        if old.get('state')!='off':raise ValueError('Den angivna gamla automationen måste finnas och vara avstängd.')
        entity=c['applied_signal']
        if not entity.startswith('number.'):raise ValueError('Aktiv PI kräver en number-entitet som utgång.')
        s=by_id.get(entity,{})
        a=s.get('attributes',{})
        if a.get('restored'):raise ValueError('Utgången är ett återställt värde och ännu inte tillgänglig.')
        try:
            value=float(s['state']);low=float(a['min']);high=float(a['max']);step=float(a['step'])
            if not all(math.isfinite(x) for x in (value,low,high,step)) or step<=0 or low>=high:raise ValueError()
            if a.get('unit_of_measurement')!='°C':raise ValueError()
            if not max(low,c['signal_min'])<=value<=min(high,c['signal_max']):raise ValueError()
        except (KeyError,ValueError,TypeError):raise ValueError('Utgången saknar giltigt värde, enhet eller gränser; kontrollera även appens signalgränser.')
        return value,low,high,step

    def arm(self,c,states,items):
        with self.lock:
            value,*_=self.target(c,states)
            required=set(c['indoor']+[c['outdoor']])
            valid={r['entity'] for r in items if r['quality']=='OK' and r['value'] is not None and math.isfinite(r['value'])}
            if not c['indoor'] or not required.issubset(valid):raise ValueError('Aktuella inne- och utetemperaturer krävs.')
            if self.armed:raise ValueError('PI är redan aktiv.')
            self.persist(dict(c) if c.get('auto_restart') else None)
            self.settings=dict(c);self.armed=True;self.last_sent=value;self.changed_at=time.monotonic()
            self.info={'active':True,'message':'Aktiverad. Väntar på första temperaturkommandot.'}

    def send(self,c,states,pi,clock=None):
        with self.lock:
            if not self.armed:return
            try:
                if c!=self.settings:raise ValueError('Inställningarna ändrades. Aktivera på nytt efter granskning.')
                current,low,high,step=self.target(c,states)
                if abs(current-self.last_sent)>step/2+1e-6:raise ValueError('Utgången ändrades oväntat. Kontrollera andra skrivare.')
                proposed=pi.get('signal')
                if proposed is None or not math.isfinite(proposed):raise ValueError('PI saknar giltigt förslag.')
                clock=time.monotonic() if clock is None else clock
                lo=max(low,c['signal_min']);hi=min(high,c['signal_max'])
                min_tick=math.ceil((lo-low)/step-1e-9);max_tick=math.floor((hi-low)/step+1e-9)
                if min_tick>max_tick:raise ValueError('Inget tillåtet utgångsvärde inom gränserna.')
                tick=max(min_tick,min(max_tick,round((proposed-low)/step)))
                value=round(low+tick*step,6)
                if abs(value-self.last_sent)>c['pi_rate']*max(0,clock-self.changed_at)/3600+1e-6:
                    value=self.last_sent
                # Always send: this heartbeat depends on verified repeated-command semantics.
                self.request('services/number/set_value',{'entity_id':c['applied_signal'],'value':value})
                if value!=self.last_sent:self.changed_at=clock
                self.last_sent=value
                self.last_command={'value':value,'sent_at':time.time()}
                self.info={'active':True,'value':value,'sent_at':time.time(),
                           'message':'Temperaturkommando skickat via HA. Detta är inte kvittens från värmepumpen.'}
            except Exception as e:
                message='PI stoppad: '+(str(e) if isinstance(e,ValueError) else 'Skrivning till Home Assistant misslyckades.')
                if isinstance(e,ValueError):self.stop(message,fault=True)
                else:self.pause(message)

    def get(self):
        with self.lock:return dict(self.info, auto_restart_pending=self.resume_settings is not None and not self.armed,last_command=self.last_command)

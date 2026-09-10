"""Explicit, process-local authority to send PI commands. Restart means disarmed."""
import math
import threading
import time

class Control:
    def __init__(self, request):
        self.request=request
        self.lock=threading.RLock()
        self.armed=False
        self.settings=None
        self.last_sent=None
        self.changed_at=None
        self.info={'active':False,'message':'Avstängd. Aktiv PI måste startas uttryckligen efter varje omstart.'}

    def stop(self, message='Stoppad. Inga fler kommandon skickas; verifierad watchdog måste återgå till riktig utegivare.'):
        with self.lock:
            self.armed=False
            self.info={'active':False,'message':message}

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
                self.info={'active':True,'value':value,'sent_at':time.time(),
                           'message':'Temperaturkommando skickat via HA. Detta är inte kvittens från värmepumpen.'}
            except Exception as e:
                self.stop('PI stoppad: '+(str(e) if isinstance(e,ValueError) else 'Skrivning till Home Assistant misslyckades.')+' Inga fler kommandon skickas; watchdog ska återgå till riktig utegivare.')

    def get(self):
        with self.lock:return dict(self.info)

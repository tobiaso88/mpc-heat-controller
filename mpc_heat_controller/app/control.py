"""Explicit, process-local authority to send PI commands. Restart means disarmed."""
import math
import threading
import time
import json
import sqlite3
from datetime import datetime, timezone, timedelta

from .core import validate

class Control:
    ACK_GRACE_SECONDS=120
    DISPATCH_SECONDS=300
    # Once PI has taken over at a comfort limit, the temperature must come this far back
    # inside before MPC decides again. Without it the two swap every minute at the limit.
    GUARD_HYSTERESIS=0.2
    # Test signal while no house model is approved. PI alone ties the signal to weather and room
    # temperature, so its data cannot show what the signal does. Eight-hour blocks drift against
    # the day; twelve-hour blocks would follow it and be mistaken for the daily cycle.
    TEST_AMPLITUDE=1.0
    TEST_HALF_PERIOD=8*3600

    def __init__(self, request, data=None):
        self.request=request
        self.lock=threading.RLock()
        self.armed=False
        self.settings=None
        self.last_sent=None
        self.changed_at=None
        self.last_command=None
        self.last_dispatch=None
        self.pending_ack=None
        self.data=data
        self.resume_settings=None
        self.ready_since=None
        self.decision=None
        self.last_source=None
        self.guard=None
        if data is not None and (data/'control.sqlite').exists():
            with sqlite3.connect(data/'control.sqlite') as db:
                row=db.execute('SELECT settings FROM intent WHERE id=1').fetchone()
            if row:
                self.resume_settings=json.loads(row[0])
                # Settings saved by an earlier version lack newer keys; their defaults apply.
                try:self.resume_settings=validate(self.resume_settings)
                except (ValueError,TypeError):pass
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
            self.armed=False;self.ready_since=None
            self.info={'active':False,'fault':True,'message':message+' Inga kommandon skickas. Väntar på tillgängliga, giltiga mätvärden som är högst 24 timmar gamla.'}

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
                blockers=[]
                for entity in sorted(required):
                    r=by_id.get(entity,{})
                    reason=None
                    try:
                        stamp=datetime.fromisoformat(r['reported_at'].replace('Z','+00:00'))
                        if stamp.tzinfo is None:raise ValueError()
                        if r['quality']!='OK' or r['value'] is None or not math.isfinite(r['value']):reason=r.get('quality','Ogiltigt värde')
                        elif raw.get(entity,{}).get('attributes',{}).get('restored'):reason='Återställt värde'
                        elif not -60 <= time.time()-stamp.timestamp() <= 86400:reason='Rapporttid utanför tillåtet intervall (högst 24 h)'
                    except (KeyError,ValueError,TypeError,AttributeError):reason='Saknar giltig rapporttid'
                    if reason:blockers.append({'entity':entity,'reason':reason,'reported_at':r.get('reported_at')})
                if blockers:
                    self.ready_since=None
                    self.info={'active':False,'message':'Väntar på givare: '+ '; '.join(b['entity']+': '+b['reason'] for b in blockers)+'. Inget skickas.', 'blockers':blockers}
                    return False
                if self.ready_since is None:self.ready_since=time.monotonic()
                if time.monotonic()-self.ready_since<60:
                    self.info={'active':False,'message':'Givare och utgång tillgängliga. Väntar på ny kontroll efter minst 60 sekunder; inget skickas.'};return False
                self.arm(c,states,items)
                return True
            except (ValueError,KeyError,TypeError,AttributeError) as e:
                self.ready_since=None
                self.info={'active':False,'message':'Återstart väntar: '+(str(e) if isinstance(e,ValueError) and str(e) else 'Ogiltiga uppgifter från HA')+'. Inget skickas.'}
                return False

    def stop(self, message='Stoppad. Inga fler kommandon skickas; verifierad watchdog måste återgå till riktig utegivare.', fault=False):
        with self.lock:
            self.armed=False
            self.persist(None)
            self.ready_since=None
            self.info={'active':False,'fault':fault,'message':message}
        if fault:threading.Thread(target=self.notify_fault,args=(message,),daemon=True).start()

    def notify_fault(self,message):
        try:
            self.request('services/persistent_notification/create',{
                'notification_id':'mpc_heat_controller_pi_fault',
                'title':'MPC Heat Controller: PI har stoppats',
                'message':message+' Kontrollera PI-status innan styrningen aktiveras igen.'})
        except Exception:
            pass

    def target(self,c,states):
        by_id={s['entity_id']:s for s in states}
        if c['mode']!='shadow':raise ValueError('Välj skuggläge för att förbereda PI.')
        if not c['watchdog_verified'] or c['watchdog_seconds']<600:
            raise ValueError('Verifiera att uteblivna temperaturkommandon ger fallback och ange timeout, minst 600 sekunder för sändning var femte minut.')
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
        if abs(0.5/step-round(0.5/step))>1e-6 or abs(low/step-round(low/step))>1e-6:
            raise ValueError('Utgångens steg och gränser måste stödja temperaturer i steg om 0,5 °C.')
        if math.ceil(max(low,c['signal_min'])*2)>math.floor(min(high,c['signal_max'])*2):
            raise ValueError('Signalgränserna måste innehålla ett värde i steg om 0,5 °C.')
        return value,low,high,step

    def arm(self,c,states,items):
        with self.lock:
            value,*_=self.target(c,states)
            required=set(c['indoor']+[c['outdoor']])
            valid={r['entity'] for r in items if r['quality']=='OK' and r['value'] is not None and math.isfinite(r['value'])}
            if not c['indoor'] or not required.issubset(valid):raise ValueError('Aktuella inne- och utetemperaturer krävs.')
            if self.armed:raise ValueError('PI är redan aktiv.')
            self.persist(dict(c) if c.get('auto_restart') else None)
            self.settings=dict(c);self.armed=True;self.last_sent=value;self.last_source=None;self.decision=None;self.guard=None;self.changed_at=time.monotonic();self.pending_ack=None
            self.info={'active':True,'message':'Aktiverad. Väntar på första temperaturkommandot.'}

    def reconfigure_comfort(self,c):
        with self.lock:
            if not self.armed and self.resume_settings is None:return
            if self.armed:
                self.settings=dict(c)
                self.info=dict(self.info,message='Komfortmålet uppdaterades utan att stoppa PI.')
            self.persist(dict(c) if c.get('auto_restart') else None)

    def verify_output(self,current,step,clock):
        tolerance=step/2+1e-6
        if abs(current-self.last_sent)<=tolerance:
            self.pending_ack=None
            return
        pending=self.pending_ack
        if pending and abs(current-pending['previous'])<=tolerance:
            if clock-pending['sent_at']<=self.ACK_GRACE_SECONDS:return
            raise ValueError(f'Senaste kommandot {self.last_sent:g} °C har inte bekräftats av HA; utgången visar fortfarande {current:g} °C.')
        raise ValueError(f'Utgången ändrades oväntat från {self.last_sent:g} till {current:g} °C. Kontrollera andra skrivare.')

    @staticmethod
    def guard_state(c,inside,previous=None):
        """'cold' below the comfort range and 'warm' above it. Either is kept until the temperature
        is GUARD_HYSTERESIS back inside the range, though never past the target."""
        if inside<c['comfort_min']:return 'cold'
        if inside>c['comfort_max']:return 'warm'
        if previous=='cold' and inside<min(c['comfort_min']+Control.GUARD_HYSTERESIS,c['target'])-1e-9:return 'cold'
        if previous=='warm' and inside>max(c['comfort_max']-Control.GUARD_HYSTERESIS,c['target'])+1e-9:return 'warm'
        return None

    @staticmethod
    def test_signal(c,now):
        amplitude=min(Control.TEST_AMPLITUDE,c['mpc_max_deviation'])
        return amplitude if int(now.timestamp()//Control.TEST_HALF_PERIOD)%2==0 else -amplitude

    @staticmethod
    def choose(c,pi,mpc,now=None,guard=None):
        """Pick the signal to send. MPC learning stays within ±mpc_max_deviation of PI's proposal;
        PI alone decides when it is cold, when no usable plan exists or when the plan is stale.
        When it is too warm, MPC may only give less heat than PI. While no model is approved and
        the temperature is inside the comfort range, PI carries the test signal. `guard` is the
        previous decision's guard state; the new one is returned in the decision."""
        signal=pi.get('signal')
        if not c.get('mpc_learning'):return signal,{'source':'pi','reason':'MPC-inlärning är avstängd.'}
        inside=c['target']-pi['error']
        guard=Control.guard_state(c,inside,guard)
        sv=lambda value,digits=2:f'{value:.{digits}f}'.replace('.',',')
        learning=(mpc or {}).get('learning') or {}
        now=now or datetime.now(timezone.utc)
        if learning.get('state')!='ready':
            reason='MPC saknar plan: '+' '.join((learning.get('reasons') or ['ingen inlärningsmodell'])[:2])
            # Only a missing model calls for the test signal, not a missing forecast or a stale plan.
            if learning.get('model_ready') is not False:return signal,{'source':'pi','guard':guard,'reason':reason}
            if guard:return signal,{'source':'pi','guard':guard,'reason':reason+' Testsignalen pausar utanför komfortområdet.'}
            test=Control.test_signal(c,now)
            return signal+test,{'source':'pi','guard':guard,'test_signal':test,'pi_signal':signal,
                                'reason':reason+f' PI styr med testsignal {test:+g} °C'.replace('.',',')+' så att husmodellen kan lära sig signalens effekt.'}
        try:
            planned=float(learning['signal']);age=now-datetime.fromisoformat(learning['planned_at'])
            if not math.isfinite(planned):raise ValueError()
        except (KeyError,TypeError,ValueError):return signal,{'source':'pi','guard':guard,'reason':'MPC-planen är ogiltig.'}
        if not timedelta(0)<=age<=timedelta(minutes=15):return signal,{'source':'pi','guard':guard,'reason':'MPC-planen är för gammal.'}
        if inside<c['comfort_min']:
            return signal,{'source':'pi','guard':guard,'reason':f'Innetemperaturen {sv(inside)} °C är under komfortområdet; PI styr.'}
        if guard=='cold':
            back=min(c['comfort_min']+Control.GUARD_HYSTERESIS,c['target'])
            return signal,{'source':'pi','guard':guard,'reason':f'Innetemperaturen {sv(inside)} °C har varit under komfortområdet; PI styr tills den nått {sv(back,1)} °C.'}
        deviation=c['mpc_max_deviation']
        chosen=min(signal+deviation,max(signal-deviation,planned))
        # A higher signal means less heat. Above comfort, MPC may only reduce heat compared with PI.
        if guard=='warm' and chosen<signal:
            return signal,{'source':'pi','guard':guard,'reason':'Varmt inne och MPC ville ge mer värme än PI; PI styr.','mpc_signal':planned}
        return chosen,{'source':'mpc','guard':guard,'reason':'MPC styr inom ±'+f'{deviation:g}'.replace('.',',')+' °C från PI.','mpc_signal':planned,'pi_signal':signal,
                       'limited':abs(chosen-planned)>1e-9,'model':learning.get('model')}

    @staticmethod
    def toward(last,target,allowance):
        """The half-degree value nearest `target` that lies between `last` and `target` and at most
        `allowance` from `last`. None while an off-grid `last` has no such value within reach."""
        if target>=last:
            value=math.floor(min(target,last+allowance+1e-6)*2+1e-9)/2
            return value if value>=last-1e-9 else None
        value=math.ceil(max(target,last-allowance-1e-6)*2-1e-9)/2
        return value if value<=last+1e-9 else None

    def send(self,c,states,pi,clock=None,mpc=None):
        with self.lock:
            if not self.armed:return None
            decision=None
            try:
                if c!=self.settings:raise ValueError('Inställningarna ändrades. Aktivera på nytt efter granskning.')
                current,low,high,step=self.target(c,states)
                if pi.get('signal') is None or not math.isfinite(pi['signal']):raise ValueError('PI saknar giltigt förslag.')
                proposed,decision=self.choose(c,pi,mpc,guard=self.guard)
                self.decision=decision;self.guard=decision.get('guard')
                clock=time.monotonic() if clock is None else clock
                self.verify_output(current,step,clock)
                lo=max(low,c['signal_min']);hi=min(high,c['signal_max'])
                min_tick=math.ceil(lo*2-1e-9);max_tick=math.floor(hi*2+1e-9)
                if 'test_signal' in decision:
                    # Credit the observed HA output, never the proposal or an unacknowledged
                    # write. Compare with PI on the same bounded half-degree grid. During a
                    # sign change the preceding block's offset must not count for the new one.
                    test=decision['test_signal']
                    sent_test=(self.last_command or {}).get('test_signal',0)
                    baseline=max(min_tick,min(max_tick,math.floor(pi['signal']*2+0.5)))/2
                    effective=0.0
                    if self.last_source=='pi' and sent_test*test>0 and self.pending_ack is None and abs(current-self.last_sent)<1e-6:
                        offset=current-baseline
                        if offset*test>0:effective=math.copysign(min(abs(test),abs(offset)),test)
                    decision['applied_test_signal']=effective
                if self.last_dispatch is not None and clock-self.last_dispatch<self.DISPATCH_SECONDS:return decision
                value=max(min_tick,min(max_tick,math.floor(proposed*2+0.5)))/2
                # The allowance grows with time since the last change, up to one half-degree step or one
                # dispatch interval's worth. A larger change is taken in steps at pi_rate, never held back
                # and then sent whole, and a long steady period does not license a jump.
                rate=c['pi_rate']/3600
                allowance=min(max(0.5,rate*self.DISPATCH_SECONDS),rate*max(0,clock-self.changed_at))
                value=self.toward(self.last_sent,value,allowance)
                if value is None:
                    # Initial HA value may be off the half-degree grid. Wait for enough
                    # rate allowance rather than transmitting an unrounded value.
                    self.info={'active':True,'message':'Väntar på tillåten ändring till närmaste 0,5 °C. Inget skickas ännu.'}
                    return decision
                # Repeat unchanged values every five minutes for the watchdog.
                previous=self.last_sent
                self.request('services/number/set_value',{'entity_id':c['applied_signal'],'value':value})
                self.last_dispatch=clock
                # Allowance not used by this change counts towards the next one.
                if value!=self.last_sent:self.changed_at=clock-max(0,allowance-abs(value-self.last_sent))/rate
                self.last_sent=value
                self.pending_ack={'previous':previous,'sent_at':clock} if value!=previous else None
                self.last_command={'value':value,'sent_at':time.time(),'test_signal':decision.get('test_signal',0)}
                self.last_source=decision['source']
                self.info={'active':True,'value':value,'sent_at':time.time(),'source':decision['source'],'source_reason':decision['reason'],
                           'message':('MPC' if decision['source']=='mpc' else 'PI')+'-kommando skickat via HA. Detta är inte kvittens från värmepumpen.'}
                return decision
            except Exception as e:
                message='PI stoppad: '+(str(e) if isinstance(e,ValueError) else 'Skrivning till Home Assistant misslyckades.')
                if isinstance(e,ValueError):self.stop(message,fault=True)
                else:self.pause(message)
                return None

    def get(self):
        with self.lock:return dict(self.info, auto_restart_pending=self.resume_settings is not None and not self.armed,last_command=self.last_command,
                                   decision=self.decision if self.armed else None,
                                   source=self.last_source if self.armed else None)

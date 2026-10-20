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
            self.settings=dict(c);self.armed=True;self.last_sent=value;self.last_source=None;self.decision=None;self.changed_at=time.monotonic();self.pending_ack=None
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
    def choose(c,pi,mpc,now=None):
        """Pick the signal to send. MPC learning stays within ±mpc_max_deviation of PI's proposal;
        PI alone decides when it is cold, when no usable plan exists or when the plan is stale.
        When it is too warm, MPC may only give less heat than PI."""
        signal=pi.get('signal')
        if not c.get('mpc_learning'):return signal,{'source':'pi','reason':'MPC-inlärning är avstängd.'}
        learning=(mpc or {}).get('learning') or {}
        if learning.get('state')!='ready':
            return signal,{'source':'pi','reason':'MPC saknar plan: '+' '.join((learning.get('reasons') or ['ingen inlärningsmodell'])[:2])}
        try:
            planned=float(learning['signal']);age=(now or datetime.now(timezone.utc))-datetime.fromisoformat(learning['planned_at'])
            if not math.isfinite(planned):raise ValueError()
        except (KeyError,TypeError,ValueError):return signal,{'source':'pi','reason':'MPC-planen är ogiltig.'}
        if not timedelta(0)<=age<=timedelta(minutes=15):return signal,{'source':'pi','reason':'MPC-planen är för gammal.'}
        inside=c['target']-pi['error']
        if inside<c['comfort_min']:
            return signal,{'source':'pi','reason':f'Innetemperaturen {inside:.2f} °C är under komfortområdet; PI styr.'.replace('.',',',1)}
        deviation=c['mpc_max_deviation']
        chosen=min(signal+deviation,max(signal-deviation,planned))
        # A higher signal means less heat. Above comfort, MPC may only reduce heat compared with PI.
        if inside>c['comfort_max'] and chosen<signal:
            return signal,{'source':'pi','reason':'Varmt inne och MPC ville ge mer värme än PI; PI styr.','mpc_signal':planned}
        return chosen,{'source':'mpc','reason':'MPC styr inom ±'+f'{deviation:g}'.replace('.',',')+' °C från PI.','mpc_signal':planned,'pi_signal':signal,
                       'limited':abs(chosen-planned)>1e-9,'model':learning.get('model')}

    def send(self,c,states,pi,clock=None,mpc=None):
        with self.lock:
            if not self.armed:return None
            decision=None
            try:
                if c!=self.settings:raise ValueError('Inställningarna ändrades. Aktivera på nytt efter granskning.')
                current,low,high,step=self.target(c,states)
                if pi.get('signal') is None or not math.isfinite(pi['signal']):raise ValueError('PI saknar giltigt förslag.')
                proposed,decision=self.choose(c,pi,mpc)
                self.decision=decision
                clock=time.monotonic() if clock is None else clock
                self.verify_output(current,step,clock)
                if self.last_dispatch is not None and clock-self.last_dispatch<300:return decision
                lo=max(low,c['signal_min']);hi=min(high,c['signal_max'])
                min_tick=math.ceil(lo*2-1e-9);max_tick=math.floor(hi*2+1e-9)
                value=max(min_tick,min(max_tick,math.floor(proposed*2+0.5)))/2
                if abs(value-self.last_sent)>c['pi_rate']*max(0,clock-self.changed_at)/3600+1e-6:
                    # Initial HA value may be off the half-degree grid. Wait for enough
                    # rate allowance rather than transmitting an unrounded value.
                    if abs(self.last_sent*2-round(self.last_sent*2))>1e-6:
                        self.info={'active':True,'message':'Väntar på tillåten ändring till närmaste 0,5 °C. Inget skickas ännu.'}
                        return decision
                    value=self.last_sent
                # Repeat unchanged values every five minutes for the watchdog.
                previous=self.last_sent
                self.request('services/number/set_value',{'entity_id':c['applied_signal'],'value':value})
                self.last_dispatch=clock
                if value!=self.last_sent:self.changed_at=clock
                self.last_sent=value
                self.pending_ack={'previous':previous,'sent_at':clock} if value!=previous else None
                self.last_command={'value':value,'sent_at':time.time()}
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

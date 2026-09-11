import unittest
from unittest.mock import Mock
from app.control import Control
from app.core import validate

class ControlTests(unittest.TestCase):
    def setup_control(self):
        c=validate(dict(mode='shadow',indoor=['sensor.in'],outdoor='sensor.out',applied_signal='number.hp',old_automation='automation.old',watchdog_verified=True,exclusive_writer_confirmed=True,watchdog_seconds=7200))
        states=[{'entity_id':'number.hp','state':'5','attributes':{'unit_of_measurement':'°C','min':-20,'max':40,'step':0.5}}, {'entity_id':'automation.old','state':'off'}]
        items=[{'entity':e,'value':20,'quality':'OK'} for e in ['sensor.in','sensor.out']]
        request=Mock();control=Control(request)
        return c,states,items,request,control

    def test_explicit_arm_repeated_heartbeat_and_stop(self):
        c,s,i,r,x=self.setup_control()
        x.send(c,s,{'signal':5});r.assert_not_called()
        x.arm(c,s,i);r.assert_not_called()
        t=x.changed_at
        x.send(c,s,{'signal':5},clock=t);x.send(c,s,{'signal':5},clock=t+299)
        self.assertEqual(r.call_count,1)
        x.send(c,s,{'signal':5},clock=t+300)
        self.assertEqual(r.call_count,2)
        r.assert_called_with('services/number/set_value',{'entity_id':'number.hp','value':5})
        x.stop();x.send(c,s,{'signal':5});self.assertEqual(r.call_count,2)
        self.assertFalse(Control(r).get()['active'])

    def test_preconditions(self):
        for key,value in [('watchdog_verified',False),('watchdog_seconds',60),('exclusive_writer_confirmed',False),('applied_signal','sensor.hp')]:
            c,s,i,r,x=self.setup_control();c[key]=value
            with self.assertRaises(ValueError):x.arm(c,s,i)
            r.assert_not_called()
        c,s,i,r,x=self.setup_control();s[1]['state']='on'
        with self.assertRaises(ValueError):x.arm(c,s,i)

    def test_fault_latches_off(self):
        for fault in ['sensor','automation','external','config','network']:
            c,s,i,r,x=self.setup_control();x.arm(c,s,i);pi={'signal':5}
            if fault=='sensor':pi={'signal':None}
            if fault=='automation':s[1]['state']='on'
            if fault=='external':s[0]['state']='9'
            if fault=='config':c=dict(c,target=21.8)
            if fault=='network':r.side_effect=OSError()
            x.send(c,s,pi)
            self.assertFalse(x.get()['active'])
            self.assertLessEqual(r.call_count,1)

    def test_quantized_rate_limit(self):
        c,s,i,r,x=self.setup_control();x.arm(c,s,i);t=x.changed_at
        x.send(c,s,{'signal':4.5},clock=t+60)
        self.assertEqual(r.call_args.args[1]['value'],5)
        x.send(c,s,{'signal':4.5},clock=t+900)
        self.assertEqual(r.call_args.args[1]['value'],4.5)

    def test_comfort_reconfiguration_keeps_active_control(self):
        c,s,i,r,x=self.setup_control();x.arm(c,s,i)
        updated=dict(c,target=21.7,comfort_max=22.5,target_climates=['climate.living'])
        x.reconfigure_comfort(updated)
        self.assertTrue(x.get()['active']);self.assertEqual(x.settings,updated)
        x.send(updated,s,{'signal':5},clock=x.changed_at)
        self.assertTrue(x.get()['active'])

    def test_collector_active_cycle(self):
        import tempfile
        from pathlib import Path
        from datetime import datetime,timezone
        from unittest.mock import patch
        from app.telemetry import Collector,readings
        c,s,i,r,x=self.setup_control()
        for entity,value in [('sensor.in',21),('sensor.out',5)]:
            s.append({'entity_id':entity,'state':str(value),'last_reported':datetime.now(timezone.utc).isoformat(),'attributes':{'unit_of_measurement':'°C'}})
        with tempfile.TemporaryDirectory() as d,patch('app.telemetry.request',return_value=s) as req:
            collector=Collector(lambda:c,Path(d))
            collector.control.arm(c,s,readings(c,s))
            collector.cycle()
            self.assertTrue(collector.get()['control']['active'])
            self.assertEqual(req.call_args.args[0],'services/number/set_value')
            self.assertEqual(req.call_args.args[1]['value'],5)

    def test_half_degree_rounding_and_changed_value_waits_five_minutes(self):
        c,s,i,r,x=self.setup_control();s[0]['attributes']['step']=0.1
        x.arm(c,s,i);t=x.changed_at
        x.send(c,s,{'signal':4.6},clock=t+900)
        self.assertEqual(r.call_args.args[1]['value'],4.5)
        s[0]['state']='4.5'
        x.send(c,s,{'signal':4.1},clock=t+1199)
        self.assertEqual(r.call_count,1)
        x.send(c,s,{'signal':4.1},clock=t+1200)
        self.assertEqual(r.call_count,2)
        self.assertEqual(r.call_args.args[1]['value'],4.5)  # Rate limit still applies.
        x.send(c,s,{'signal':4.1},clock=t+1800)
        self.assertEqual(r.call_args.args[1]['value'],4.0)

    def test_fault_checked_between_sends(self):
        c,s,i,r,x=self.setup_control();x.arm(c,s,i);t=x.changed_at
        x.send(c,s,{'signal':5},clock=t)
        s[1]['state']='on'
        x.send(c,s,{'signal':5},clock=t+60)
        self.assertFalse(x.get()['active'])
        self.assertEqual(sum(call.args[0]=='services/number/set_value' for call in r.call_args_list),1)

    def test_delayed_ha_state_after_changed_command_gets_short_grace(self):
        c,s,i,r,x=self.setup_control();t=1000
        x.arm(c,s,i);x.changed_at=t-900
        x.send(c,s,{'signal':4.5},clock=t)
        self.assertEqual(r.call_args.args[1]['value'],4.5)
        x.send(c,s,{'signal':4.5},clock=t+60)
        self.assertTrue(x.get()['active'])
        s[0]['state']='4.5'
        x.send(c,s,{'signal':4.5},clock=t+120)
        self.assertTrue(x.get()['active'])

    def test_missing_ack_and_different_external_value_stop(self):
        c,s,i,r,x=self.setup_control();t=1000
        x.arm(c,s,i);x.changed_at=t-900
        x.send(c,s,{'signal':4.5},clock=t)
        x.send(c,s,{'signal':4.5},clock=t+121)
        self.assertFalse(x.get()['active'])
        self.assertIn('inte bekräftats',x.get()['message'])

        c,s,i,r,x=self.setup_control();x.arm(c,s,i);x.changed_at=t-900
        x.send(c,s,{'signal':4.5},clock=t)
        s[0]['state']='6'
        x.send(c,s,{'signal':4.5},clock=t+60)
        self.assertFalse(x.get()['active'])
        self.assertIn('från 4.5 till 6',x.get()['message'])

    def test_fault_notification_is_scoped_and_failure_is_harmless(self):
        c,s,i,r,x=self.setup_control()
        x.notify_fault('PI stoppad: testfel.')
        r.assert_called_once_with('services/persistent_notification/create',{
            'notification_id':'mpc_heat_controller_pi_fault',
            'title':'MPC Heat Controller: PI har stoppats',
            'message':'PI stoppad: testfel. Kontrollera PI-status innan styrningen aktiveras igen.'})
        r.side_effect=OSError()
        x.notify_fault('PI stoppad: nytt testfel.')

    def test_off_grid_handover_and_short_watchdog(self):
        c,s,i,r,x=self.setup_control();c['watchdog_seconds']=300
        with self.assertRaises(ValueError):x.arm(c,s,i)
        c['watchdog_seconds']=7200;s[0]['attributes']['step']=0.1;s[0]['state']='5.1'
        x.arm(c,s,i);t=x.changed_at
        x.send(c,s,{'signal':5.1},clock=t)
        r.assert_not_called()
        x.send(c,s,{'signal':5.1},clock=t+300)
        self.assertEqual(r.call_args.args[1]['value'],5)

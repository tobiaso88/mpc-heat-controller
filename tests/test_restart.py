import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime,timezone,timedelta
from app.control import Control
from app.core import validate

class RestartTests(unittest.TestCase):
    def setup_data(self):
        c=validate(dict(mode='shadow',indoor=['sensor.in'],outdoor='sensor.out',applied_signal='number.hp',old_automation='automation.old',watchdog_verified=True,exclusive_writer_confirmed=True,watchdog_seconds=300,auto_restart=True))
        states=[{'entity_id':'number.hp','state':'5','attributes':{'unit_of_measurement':'°C','min':-20,'max':40,'step':0.5}},{'entity_id':'automation.old','state':'off'}]
        items=[]
        for e in ['sensor.in','sensor.out']:
            states.append({'entity_id':e,'attributes':{}})
            items.append({'entity':e,'value':20,'quality':'OK','reported_at':datetime.now(timezone.utc).isoformat()})
        return c,states,items

    def test_persist_wait_fresh_stable_then_resume(self):
        c,s,i=self.setup_data();req=Mock()
        with tempfile.TemporaryDirectory() as d:
            first=Control(req,Path(d));first.arm(c,s,i)
            reboot=Control(req,Path(d))
            self.assertFalse(reboot.get()['active'])
            req.assert_not_called()
            for r in i:r['reported_at']=(datetime.now(timezone.utc)-timedelta(hours=23)).isoformat()
            with patch('app.control.time.monotonic',return_value=100):self.assertFalse(reboot.resume(c,s,i))
            with patch('app.control.time.monotonic',return_value=161):self.assertTrue(reboot.resume(c,s,i))
            req.assert_not_called()
            reboot.stop()
            self.assertFalse(Control(req,Path(d)).get()['auto_restart_pending'])

    def test_no_start_from_checkbox_and_invalid_resets_stability(self):
        c,s,i=self.setup_data();req=Mock();x=Control(req)
        self.assertFalse(x.resume(c,s,i))
        x.arm(c,s,i);x.pause('offline')
        for r in i:r['reported_at']=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        with patch('app.control.time.monotonic',return_value=100):self.assertFalse(x.resume(c,s,i))
        i[0]['quality']='Saknas'
        with patch('app.control.time.monotonic',return_value=170):self.assertFalse(x.resume(c,s,i))
        i[0]['quality']='OK'
        with patch('app.control.time.monotonic',return_value=180):self.assertFalse(x.resume(c,s,i))
        req.assert_not_called()

    def test_changed_settings_cancel_resume(self):
        c,s,i=self.setup_data();x=Control(Mock());x.arm(c,s,i);x.pause('offline')
        c=dict(c,target=21.8)
        self.assertFalse(x.resume(c,s,i));self.assertFalse(x.get()['auto_restart_pending'])

    def test_restart_names_stale_sensor_and_output_blocker(self):
        c,s,i=self.setup_data();req=Mock();x=Control(req)
        x.arm(c,s,i);x.pause('offline')
        i[0]['reported_at']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
        i[1]['reported_at']=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        self.assertFalse(x.resume(c,s,i))
        self.assertEqual(x.get()['blockers'][0]['entity'],'sensor.in')
        self.assertIn('högst 24 h',x.get()['message'])
        s[1]['state']='on'
        self.assertFalse(x.resume(c,s,i))
        self.assertIn('gamla automationen',x.get()['message'])
        req.assert_not_called()

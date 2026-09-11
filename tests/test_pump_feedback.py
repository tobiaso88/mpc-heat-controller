import unittest
from datetime import datetime, timezone
from app.core import validate
from app.telemetry import readings
from app.pi import PI

class PumpFeedbackTests(unittest.TestCase):
    def test_optional_feedback_does_not_block_pi(self):
        c=validate({'mode':'shadow','indoor':['sensor.room'],'outdoor':'sensor.real','pump_outdoor':'sensor.pump'})
        states=[{'entity_id':entity,'state':value,'attributes':{'unit_of_measurement':'°C'},'last_reported':datetime.now(timezone.utc).isoformat()} for entity,value in [('sensor.room','21'),('sensor.real','5'),('sensor.pump','unavailable')]]
        items=readings(c,states)
        self.assertIsNone(next(r for r in items if r['entity']=='sensor.pump')['value'])
        self.assertIsNotNone(PI().step(c,items)['signal'])
        states[-1]['state']='3'
        self.assertEqual(next(r for r in readings(c,states) if r['entity']=='sensor.pump')['value'],3)

    def test_config_is_optional_and_sensor_only(self):
        self.assertEqual(validate({})['pump_outdoor'],'')
        with self.assertRaises(ValueError):validate({'pump_outdoor':'number.output'})

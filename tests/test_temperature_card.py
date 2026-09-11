import unittest
from datetime import datetime,timezone,timedelta
from app.server import status
from app.core import validate

class TemperatureCardTests(unittest.TestCase):
    def test_outdoor_missing_does_not_hide_indoor(self):
        c=validate({'mode':'shadow','indoor':['sensor.in'],'outdoor':'sensor.out'})
        state={'entity_id':'sensor.in','state':'21.4','attributes':{'unit_of_measurement':'°C'},'last_reported':datetime.now(timezone.utc).isoformat()}
        result=status(c,{'entities':[state]})
        self.assertEqual(result['temperature'],21.4);self.assertTrue(result['temperature_valid'])
        self.assertIn('sensor.out',result['message'])

    def test_stale_is_labeled_and_missing_is_not_partial_mean(self):
        c=validate({'mode':'shadow','indoor':['sensor.in'],'outdoor':'sensor.out'})
        state={'entity_id':'sensor.in','state':'21.4','attributes':{'unit_of_measurement':'°C'},'last_reported':(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()}
        result=status(c,{'entities':[state]})
        self.assertEqual(result['temperature'],21.4);self.assertFalse(result['temperature_valid'])
        self.assertIn('Senast kända',result['temperature_message'])
        c['indoor'].append('sensor.missing')
        self.assertIsNone(status(c,{'entities':[state]})['temperature'])

import tempfile
import unittest
from pathlib import Path
from app import model_store
from app.core import validate
from app.telemetry import readings
from datetime import datetime, timezone

class StoreTests(unittest.TestCase):
    def test_saved_input_and_result_survive_reload(self):
        with tempfile.TemporaryDirectory() as d:
            data=Path(d)
            self.assertIsNone(model_store.load(data))
            report={'mapping':{'indoor':['sensor.room']},'metrics':[{'mae':0.3}]}
            model_store.save(data,'entity_id,state,last_changed\n',report)
            saved=model_store.load(data)
            self.assertEqual(saved['report'],report)
            self.assertEqual(saved['csv'],'entity_id,state,last_changed\n')
            model_store.save(data,'replacement',{'metrics':[]})
            self.assertEqual(model_store.load(data)['csv'],'replacement')

    def test_automatic_status_does_not_require_imported_evaluation(self):
        with tempfile.TemporaryDirectory() as d:
            data=Path(d)
            model_store.save_auto_status(data,{'state':'collecting'})
            self.assertIsNone(model_store.load(data))
            self.assertEqual(model_store.load_auto(data)['status']['state'],'collecting')

    def test_old_settings_and_readonly_number(self):
        self.assertEqual(validate({})['applied_signal'],'')
        c=validate({'applied_signal':'number.ohmigo'})
        result=readings(c,[{'entity_id':'number.ohmigo','state':'4.5','last_reported':datetime.now(timezone.utc).isoformat(),'attributes':{'unit_of_measurement':'°C'}}])
        self.assertEqual(result[0]['value'],4.5)
        self.assertEqual(result[0]['roles'],['Ohmigo inställt värde'])
        with self.assertRaises(ValueError):validate({'applied_signal':'switch.heat'})

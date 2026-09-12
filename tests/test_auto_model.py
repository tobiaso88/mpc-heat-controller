import json
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.auto_model import AutoModel, local_points, live_plan
from app.core import validate
from app import model_store


class AutoModelTests(unittest.TestCase):
    def make_log(self, data, hours=250):
        config = validate({'mode': 'shadow', 'indoor': ['sensor.room'],
                           'outdoor': 'sensor.out', 'applied_signal': 'number.signal'})
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        temperature = 21.0
        with sqlite3.connect(data / 'measurements.sqlite') as db:
            db.execute('CREATE TABLE samples (time TEXT PRIMARY KEY, readings TEXT, settings TEXT)')
            for h in range(hours):
                outside = 5 + 4*math.sin(h/19)
                signal = 3 + 3*math.sin(h/13)
                readings = [{'entity': entity, 'value': value, 'quality': 'OK'} for entity, value in
                            [('sensor.room', temperature), ('sensor.out', outside), ('number.signal', signal)]]
                stamp = (start+timedelta(hours=h)).isoformat()
                db.execute('INSERT INTO samples VALUES (?,?,?)',
                           (stamp, json.dumps(readings), json.dumps(config)))
                temperature += 0.5 + 0.025*(outside-temperature) - 0.01*signal
        return config, start

    def test_trains_from_local_measurements_and_builds_shadow_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config, start = self.make_log(data)
            trainer = AutoModel(data)
            trainer.maybe_train(config, start+timedelta(days=30))
            saved = model_store.load_auto(data)
            self.assertEqual(saved['status']['state'], 'ready')
            self.assertEqual(saved['report']['source'], 'local_log')
            self.assertEqual(saved['report']['complete_hours'], 250)
            forecast = [{'datetime': (start+timedelta(hours=601+h)).isoformat(), 'temperature': 3+h/10}
                        for h in range(24)]
            readings = [{'entity': 'sensor.room', 'value': 21.0, 'quality': 'OK'},
                        {'entity': 'number.signal', 'value': 4.0, 'quality': 'Rapporttid utanför tillåtet intervall'}]
            result = trainer.result(config, readings, forecast)
            self.assertEqual(len(result['plan']), 24)

    def test_waits_for_enough_complete_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config, start = self.make_log(data, 100)
            points, info = local_points(data, {'indoor':['sensor.room'], 'outdoor':'sensor.out', 'signal':'number.signal'})
            self.assertEqual(len(points), 100)
            self.assertEqual(info['incomplete_hours'], 0)
            AutoModel(data).maybe_train(config, start+timedelta(days=5))
            saved = model_store.load_auto(data)
            self.assertEqual(saved['status']['state'], 'collecting')
            self.assertEqual(saved['status']['complete_hours'], 100)
            self.assertIsNone(saved['report'])

    def test_live_plan_requires_full_forecast(self):
        self.assertEqual(live_plan({}, 21, 5, [], validate({})), [])


if __name__ == '__main__':
    unittest.main()

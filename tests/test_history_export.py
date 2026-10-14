import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.core import validate
from app.history_export import build


class HistoryExportTests(unittest.TestCase):
    def test_exports_raw_history_and_only_eligible_hourly_values(self):
        config = validate({'indoor': ['sensor.room'], 'outdoor': 'sensor.out',
                           'applied_signal': 'number.signal'})
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            with sqlite3.connect(data / 'measurements.sqlite') as db:
                db.execute('CREATE TABLE samples (time TEXT, readings TEXT, settings TEXT)')
                for hour, quality in [(0, 'OK'), (1, 'unavailable')]:
                    readings = [{'entity': entity, 'value': value, 'quality': quality}
                                for entity, value in [('sensor.room', 21), ('sensor.out', 5), ('number.signal', 3)]]
                    db.execute('INSERT INTO samples VALUES (?, ?, ?)',
                               (f'2026-10-14T0{hour}:00:00+00:00', json.dumps(readings), json.dumps(config)))
            with zipfile.ZipFile(io.BytesIO(build(data, config))) as archive:
                raw = list(csv.DictReader(io.StringIO(archive.read('measurements.csv').decode('utf-8-sig'))))
                self.assertEqual(len(raw), 6)
                self.assertEqual(raw[-1]['quality'], 'unavailable')
                hourly = list(csv.DictReader(io.StringIO(archive.read('hourly_temperature.csv').decode('utf-8-sig'))))
                self.assertEqual(len(hourly), 1)
                self.assertEqual(hourly[0]['indoor_mean_c'], '21.0')
                metadata = json.loads(archive.read('model.json'))
                self.assertEqual(metadata['datasets']['temperature']['complete_hours'], 1)

    def test_empty_log_is_downloadable_without_creating_database(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            with zipfile.ZipFile(io.BytesIO(build(data, validate({})))) as archive:
                self.assertIn('measurements.csv', archive.namelist())
                self.assertEqual(json.loads(archive.read('model.json'))['datasets'], {})
            self.assertFalse((data / 'measurements.sqlite').exists())

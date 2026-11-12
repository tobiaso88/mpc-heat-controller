import csv
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.core import validate
from app.history_export import build, ExportJob


class HistoryExportTests(unittest.TestCase):
    def wait_for_export(self, job, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = job.status(job_id)
            if status['state'] != 'running':
                return status
            time.sleep(0.01)
        self.fail('Export did not finish')

    def test_background_export_reuses_running_job_and_serves_finished_zip(self):
        entered, release = threading.Event(), threading.Event()
        original = build
        def slow_build(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args)
        job = ExportJob()
        with tempfile.TemporaryDirectory() as folder, patch('app.history_export.build', side_effect=slow_build):
            first = job.start(Path(folder), validate({}))
            self.assertTrue(entered.wait(5))
            self.assertEqual(first['state'], 'running')
            self.assertEqual(job.start(Path(folder), validate({})), first)
            self.assertIsNone(job.open(first['id']))
            release.set()
            self.assertEqual(self.wait_for_export(job, first['id'])['state'], 'ready')
            with job.open(first['id']) as source, zipfile.ZipFile(source) as archive:
                self.assertIn('model.json', archive.namelist())
            self.assertIsNone(job.status('unknown'))
            self.assertIsNone(job.open('unknown'))
            second = job.start(Path(folder), validate({}))
            self.assertNotEqual(second['id'], first['id'])
            self.assertIsNone(job.status(first['id']))
            self.wait_for_export(job, second['id'])
        job.directory.cleanup()

    def test_export_failure_logs_traceback_and_returns_actionable_error(self):
        job = ExportJob()
        with tempfile.TemporaryDirectory() as folder, \
                patch('app.history_export.build', side_effect=sqlite3.OperationalError('database is locked')), \
                self.assertLogs(level='ERROR') as logs:
            started = job.start(Path(folder), validate({}))
            result = self.wait_for_export(job, started['id'])
        self.assertIn('Model history export failed', logs.output[0])
        self.assertIn('database is locked', logs.output[0])
        self.assertEqual(result['state'], 'failed')
        self.assertIn('Logg', result['error'])
        self.assertIsNone(job.open(started['id']))
        job.directory.cleanup()

    def test_export_can_write_directly_to_disk(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'history.zip'
            self.assertIsNone(build(Path(folder), validate({}), target))
            with zipfile.ZipFile(target) as archive:
                self.assertIn('measurements.csv', archive.namelist())

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

    def test_csv_compression_does_not_lock_live_measurements(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            live = data / 'measurements.sqlite'
            with sqlite3.connect(live) as db:
                db.execute('CREATE TABLE samples (time TEXT, readings TEXT, settings TEXT)')
                db.execute('INSERT INTO samples VALUES (?, ?, ?)', ('2026-11-12', '[]', '{}'))
            original = csv.writer
            def writer_with_live_write(*args, **kwargs):
                with sqlite3.connect(live, timeout=0) as db:
                    db.execute('INSERT INTO samples VALUES (?, ?, ?)', ('new row', '[]', '{}'))
                return original(*args, **kwargs)
            with patch('app.history_export.csv.writer', side_effect=writer_with_live_write):
                with zipfile.ZipFile(io.BytesIO(build(data, validate({})))) as archive:
                    self.assertIn('measurements.csv', archive.namelist())

    def test_empty_log_is_downloadable_without_creating_database(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            with zipfile.ZipFile(io.BytesIO(build(data, validate({})))) as archive:
                self.assertIn('measurements.csv', archive.namelist())
                self.assertEqual(json.loads(archive.read('model.json'))['datasets'], {})
            self.assertFalse((data / 'measurements.sqlite').exists())

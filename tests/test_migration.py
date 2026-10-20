import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import model_store
from app.auto_model import local_points, mapping_for
from app.core import validate
from app.migration import migrate_outdoor, remove_indoor
from app.shadow_evaluation import read
from app.telemetry import Collector


def logged(data, config, hours=6, extra=()):
    collector = Collector(lambda: config, data)
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours + 2)
    for h in range(hours):
        for minute in (0, 30):
            stamp = (start + timedelta(hours=h, minutes=minute)).isoformat()
            readings = [{'entity': e, 'roles': [role], 'value': v, 'quality': 'OK'} for e, role, v in
                        [('sensor.room', 'Reglering', 21.0), ('sensor.old_out', 'Utomhus', 3.0 + h), ('number.signal', 'Ohmigo inställt värde', 1.5), *extra]]
            collector.store(stamp, readings, config)
    return collector, start


class OutdoorMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.data = Path(self.directory.name)
        self.old = validate({'mode': 'shadow', 'indoor': ['sensor.room'], 'outdoor': 'sensor.old_out',
                             'applied_signal': 'number.signal', 'weather': 'weather.x'})
        self.new = dict(self.old, outdoor='sensor.new_out')

    def tearDown(self):
        self.directory.cleanup()

    def test_history_model_and_archive_follow_new_sensor_without_changing_values(self):
        collector, start = logged(self.data, self.old)
        before = local_points(self.data, mapping_for(self.old))[0]
        self.assertEqual(local_points(self.data, mapping_for(self.new))[0], {})
        report = {'mapping': mapping_for(self.old), 'fallback': {'mapping': mapping_for(self.old)}, 'models': [{'name': 'linear', 'features': ['outdoor']}]}
        model_store.save_auto(self.data, report, {'state': 'ready', 'mapping': mapping_for(self.old), 'evaluations': {'temperature': {'mapping': mapping_for(self.old)}}})
        collector.store_mpc(start.isoformat(), {'state': 'ready', 'plan': [{'datetime': start.isoformat(), 'indoor': 21, 'signal': 1.5}]},
                            {'entity': 'weather.x', 'fetched_at': start.isoformat(), 'points': [{'datetime': start.isoformat(), 'temperature': 3}]}, self.old)

        result = migrate_outdoor(self.data, 'sensor.old_out', 'sensor.new_out')

        self.assertEqual(result['samples'], 12)
        self.assertEqual(result['forecasts'], 1)
        self.assertEqual(result['model'], 4)
        self.assertEqual(local_points(self.data, mapping_for(self.new))[0], before)
        saved = model_store.load_auto(self.data)
        self.assertEqual(saved['report']['mapping'], mapping_for(self.new))
        self.assertEqual(saved['report']['fallback']['mapping']['outdoor'], 'sensor.new_out')
        self.assertEqual(saved['report']['models'][0]['features'], ['outdoor'])
        self.assertEqual(saved['status']['evaluations']['temperature']['mapping']['outdoor'], 'sensor.new_out')
        with sqlite3.connect(self.data / 'measurements.sqlite') as db:
            readings = json.loads(db.execute('SELECT readings FROM samples LIMIT 1').fetchone()[0])
            self.assertEqual(json.loads(db.execute('SELECT mapping FROM mpc_forecasts').fetchone()[0])['outdoor'], 'sensor.new_out')
            self.assertEqual(db.execute('SELECT kind FROM migrations').fetchone()[0], 'outdoor')
        moved = next(r for r in readings if r['entity'] == 'sensor.new_out')
        self.assertEqual((moved['value'], moved['migrated_from']), (3.0, 'sensor.old_out'))
        self.assertTrue((self.data / result['backups']['measurements']).exists())
        self.assertTrue((self.data / result['backups']['model']).exists())
        read(self.data)

    def test_refuses_when_new_sensor_already_logged_and_leaves_data_untouched(self):
        logged(self.data, self.old, extra=[('sensor.new_out', 'Uppföljning', 1.0)])
        with sqlite3.connect(self.data / 'measurements.sqlite') as db:
            before = db.execute('SELECT readings, settings FROM samples ORDER BY time').fetchall()
        with self.assertRaisesRegex(ValueError, 'finns redan'):
            migrate_outdoor(self.data, 'sensor.old_out', 'sensor.new_out')
        with sqlite3.connect(self.data / 'measurements.sqlite') as db:
            self.assertEqual(db.execute('SELECT readings, settings FROM samples ORDER BY time').fetchall(), before)

    def test_rejects_unchanged_or_missing_sensor(self):
        logged(self.data, self.old)
        for old, new in [('sensor.old_out', 'sensor.old_out'), ('', 'sensor.new_out'), ('sensor.old_out', '')]:
            with self.assertRaises(ValueError):
                migrate_outdoor(self.data, old, new)

    def test_failure_restores_both_databases(self):
        logged(self.data, self.old)
        model_store.save_auto(self.data, {'mapping': mapping_for(self.old)}, {'state': 'ready'})
        with sqlite3.connect(self.data / 'measurements.sqlite') as db:
            before = db.execute('SELECT readings, settings FROM samples ORDER BY time').fetchall()
        with patch('app.migration._replace_outdoor', side_effect=[0, RuntimeError('disk')]):
            with self.assertRaises(RuntimeError):
                migrate_outdoor(self.data, 'sensor.old_out', 'sensor.new_out')
        with sqlite3.connect(self.data / 'measurements.sqlite') as db:
            self.assertEqual(db.execute('SELECT readings, settings FROM samples ORDER BY time').fetchall(), before)
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='migrations'").fetchone())
        self.assertEqual(model_store.load_auto(self.data)['report']['mapping']['outdoor'], 'sensor.old_out')

    def test_keeps_only_latest_backups(self):
        logged(self.data, self.old)
        for target in ('sensor.a', 'sensor.b', 'sensor.c', 'sensor.d'):
            with closing(sqlite3.connect(self.data / 'measurements.sqlite')) as db:
                current = json.loads(db.execute('SELECT settings FROM samples LIMIT 1').fetchone()[0])['outdoor']
            with patch('app.migration.datetime') as clock:
                clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=len(target) + ord(target[-1]))
                migrate_outdoor(self.data, current, target)
        self.assertEqual(len(list((self.data / 'backups').glob('measurements-*.sqlite'))), 3)


class IndoorRemovalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.data = Path(self.directory.name)
        self.old = validate({'mode': 'shadow', 'indoor': ['sensor.wall', 'sensor.room'], 'outdoor': 'sensor.old_out',
                             'applied_signal': 'number.signal', 'weather': 'weather.x'})
        self.new = dict(self.old, indoor=['sensor.room'])

    def tearDown(self):
        self.directory.cleanup()

    def test_history_matches_remaining_rooms_without_changing_readings(self):
        collector, start = logged(self.data, self.old, extra=[('sensor.wall', 'Reglering', 18.0)])
        self.assertEqual(local_points(self.data, mapping_for(self.new))[0], {})
        with sqlite3.connect(self.data / 'measurements.sqlite') as db:
            readings_before = db.execute('SELECT readings FROM samples ORDER BY time').fetchall()
        model_store.save_auto(self.data, {'mapping': mapping_for(self.old)}, {'state': 'ready'})

        result = remove_indoor(self.data, self.old['indoor'], self.new['indoor'])

        self.assertEqual((result['samples'], result['removed']), (12, ['sensor.wall']))
        points = local_points(self.data, mapping_for(self.new))[0]
        self.assertEqual(len(points), 6)
        self.assertTrue(all(p[0] == 21.0 for p in points.values()))
        with sqlite3.connect(self.data / 'measurements.sqlite') as db:
            self.assertEqual(db.execute('SELECT readings FROM samples ORDER BY time').fetchall(), readings_before)
            settings = json.loads(db.execute('SELECT settings FROM samples LIMIT 1').fetchone()[0])
            self.assertEqual(db.execute('SELECT kind FROM migrations').fetchone()[0], 'indoor')
        self.assertEqual(settings['indoor_previous'], [['sensor.wall', 'sensor.room']])
        # The model was trained on another mean and must be retrained, not relabelled.
        self.assertEqual(model_store.load_auto(self.data)['report']['mapping'], mapping_for(self.old))
        self.assertTrue((self.data / result['backups']['measurements']).exists())

    def test_archived_proposals_are_still_followed_up_with_their_own_mean(self):
        collector, start = logged(self.data, self.old, hours=30, extra=[('sensor.wall', 'Reglering', 18.0)])
        collector.store_mpc(start.isoformat(), {'state': 'ready', 'plan': [{'datetime': (start + timedelta(hours=1)).isoformat(), 'indoor': 19.5, 'signal': 1.5}]},
                            {'entity': 'weather.x', 'fetched_at': start.isoformat(), 'points': [{'datetime': (start + timedelta(hours=1)).isoformat(), 'temperature': 3}]}, self.old)
        with patch('app.shadow_evaluation.datetime', wraps=datetime) as clock:
            clock.now.return_value = start + timedelta(hours=30)
            before = read(self.data)
        # Mean of the wall sensor (18.0) and the room (21.0), as logged when proposed.
        self.assertEqual(before['proposals'][0]['points'][0]['actual_indoor'], 19.5)
        remove_indoor(self.data, self.old['indoor'], self.new['indoor'])
        with patch('app.shadow_evaluation.datetime', wraps=datetime) as clock:
            clock.now.return_value = start + timedelta(hours=30)
            self.assertEqual(read(self.data), before)

    def test_rejects_anything_but_a_removal(self):
        logged(self.data, self.old)
        for old, new in [(['sensor.room'], ['sensor.room']), (['sensor.wall', 'sensor.room'], []),
                         (['sensor.room'], ['sensor.other']), (['sensor.wall', 'sensor.room'], ['sensor.room', 'sensor.other'])]:
            with self.assertRaises(ValueError):
                remove_indoor(self.data, old, new)
        self.assertFalse((self.data / 'backups').exists())


if __name__ == '__main__':
    unittest.main()

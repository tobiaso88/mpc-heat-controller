import unittest
from datetime import datetime, timezone, timedelta

from app.pv_history import recovered_zeros, STALE_PV


class PVHistoryTests(unittest.TestCase):
    def plateau(self, hours=12):
        start = datetime(2026, 10, 1, 17, tzinfo=timezone.utc)
        rows = [(start, {'value': 0, 'quality': 'OK'})]
        rows += [(start+timedelta(minutes=5*i), {'value': 0, 'quality': STALE_PV})
                 for i in range(1, hours*12)]
        rows.append((start+timedelta(hours=hours), {'value': 25, 'quality': 'OK'}))
        return rows

    def test_recovers_only_observed_bounded_zero_plateau(self):
        rows = self.plateau()
        self.assertEqual(recovered_zeros(rows), [t for t, r in rows[1:-1]])
        self.assertEqual(recovered_zeros(rows[:-1]), [])
        self.assertEqual(recovered_zeros(rows[1:]), [])
        self.assertEqual(recovered_zeros(self.plateau(3)), [])
        self.assertEqual(recovered_zeros(self.plateau(19)), [])

    def test_invalid_or_missing_data_never_becomes_zero(self):
        for reading in ({'value': 1, 'quality': STALE_PV},
                        {'value': None, 'quality': STALE_PV},
                        {'value': False, 'quality': STALE_PV},
                        {'value': 0, 'quality': 'Återställt värde, väntar på rapport'},
                        {'value': 0, 'quality': 'Saknas'}, {}):
            with self.subTest(reading=reading):
                rows = self.plateau()
                rows[30] = (rows[30][0], reading)
                self.assertEqual(recovered_zeros(rows), [])
        rows = self.plateau()
        del rows[25:40]
        self.assertEqual(recovered_zeros(rows), [])
        rows = self.plateau()
        rows[0][1]['value'] = 12
        self.assertEqual(recovered_zeros(rows), [])

    def test_saved_darkness_evidence_recovers_only_confirmed_night_zero(self):
        moment = datetime(2026, 11, 12, 18, tzinfo=timezone.utc)
        reading = {'value': 0, 'quality': STALE_PV,
                   'reported_at': (moment-timedelta(hours=2)).isoformat(),
                   'sun': {'state': 'below_horizon', 'reported_at': moment.isoformat()}}
        # No future closing report is required when this sample contains fresh darkness evidence.
        self.assertEqual(recovered_zeros([(moment, reading)]), [moment])
        for change in ({'state': 'above_horizon'}, {'restored': True},
                       {'reported_at': (moment-timedelta(hours=1)).isoformat()}):
            altered = dict(reading, sun={**reading['sun'], **change})
            self.assertEqual(recovered_zeros([(moment, altered)]), [])
        for change in ({'value': 12}, {'restored': True}, {'reported_at': 'invalid'},
                       {'reported_at': (moment-timedelta(hours=19)).isoformat()},
                       {'quality': 'Saknas'}):
            self.assertEqual(recovered_zeros([(moment, dict(reading, **change))]), [])

    def test_legacy_plateau_cannot_override_saved_daylight(self):
        rows = self.plateau()
        rows[30][1]['sun'] = {'state': 'above_horizon', 'reported_at': rows[30][0].isoformat()}
        self.assertEqual(recovered_zeros(rows), [])

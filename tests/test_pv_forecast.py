import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.core import validate
from app.pv_forecast import combine, energy_data, hourly, sources
from app.telemetry import readings
from app.server import pv_source_options


class PVForecastTests(unittest.TestCase):
    def test_selected_inverter_kw_is_logged_as_watts(self):
        config = validate({'pv_power': 'sensor.inverter', 'pv_forecast': 'a' * 32})
        states = [{'entity_id': 'sensor.inverter', 'state': '3.2',
                   'last_updated': datetime.now(timezone.utc).isoformat(),
                   'attributes': {'unit_of_measurement': 'kW'}}]
        result = readings(config, states)
        self.assertEqual(result[0]['value'], 3200)
        self.assertEqual(result[0]['quality'], 'OK')
        states[0]['attributes']['unit_of_measurement'] = 'kWh'
        self.assertEqual(readings(config, states)[0]['quality'], 'Fel enhet')
        states[0]['attributes']['unit_of_measurement'] = 'kW'
        states[0]['last_updated'] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertNotEqual(readings(config, states)[0]['quality'], 'OK')

    def test_pair_required_and_other_solar_features_exclusive(self):
        with self.assertRaises(ValueError): validate({'pv_power': 'sensor.inverter'})
        with self.assertRaises(ValueError): validate({'pv_forecast': 'a' * 32})
        with self.assertRaises(ValueError): validate({'pv_power': 'sensor.inverter', 'pv_forecast': 'a' * 32,
                                                     'cloud': 'sensor.cloud'})

    def test_energy_forecast_source_and_hourly_matching(self):
        entry = 'a' * 32
        entries = [{'entry_id': entry, 'domain': 'forecast_solar', 'title': 'Tak sydost'}]
        prefs = {'energy_sources': [{'type': 'solar', 'stat_energy_from': 'sensor.pv_energy',
                                    'config_entry_solar_forecast': [entry]}]}
        start = datetime(2026, 6, 1, 22, tzinfo=timezone.utc)
        forecast = {entry: {'wh_hours': {(start + timedelta(hours=h)).isoformat(): 100 if h else 0
                                           for h in range(24)}}}
        self.assertEqual(sources(entries, prefs, forecast)[0]['id'], entry)
        self.assertTrue(sources(entries, prefs, forecast)[0]['linked'])
        self.assertIn('timprognos saknas', sources(entries, prefs, {})[0]['label'])
        power = hourly(forecast, entry)
        weather = [{'datetime': (start + timedelta(hours=h)).isoformat(), 'temperature': 5} for h in range(24)]
        combined = combine(weather, power)
        self.assertEqual([p['pv_power'] for p in combined], [0] + [100] * 23)
        self.assertNotIn('pv_power', combine(weather, {})[0])

    def test_installed_source_is_selectable_before_energy_dashboard_setup(self):
        entry = 'a' * 32
        entries = [{'entry_id': entry, 'domain': 'forecast_solar', 'title': 'Tak sydost'}]
        result = sources(entries, {}, {})
        self.assertEqual(result[0]['id'], entry)
        self.assertFalse(result[0]['linked'])
        self.assertIn('koppla i Energipanelen', result[0]['label'])
        self.assertEqual(sources([{'entry_id': 'b' * 32, 'domain': 'forecast_solar', 'disabled_by': 'user'}], {}, {}), [])

    def test_source_list_uses_installed_entries_even_if_energy_websocket_fails(self):
        entries = [{'entry_id': 'a' * 32, 'domain': 'forecast_solar', 'title': 'Tak sydost'}]
        with patch('app.server.ha_request', return_value=entries) as rest, patch('app.server.energy_data', side_effect=OSError('offline')):
            result = pv_source_options()
        rest.assert_called_once_with('config/config_entries/entry?domain=forecast_solar')
        self.assertEqual(result['sources'][0]['id'], 'a' * 32)
        self.assertIn('kunde inte läsas', result['message'])

    def test_missing_midnight_zero_is_restored_only_between_known_hours(self):
        entry = 'b' * 32
        raw = {entry: {'wh_hours': {'2026-06-01T23:00:00+02:00': 0,
                                   '2026-06-02T01:00:00+02:00': 0}}}
        power = hourly(raw, entry)
        self.assertEqual(power[datetime(2026, 6, 1, 22, tzinfo=timezone.utc)], 0)
        self.assertEqual(len(power), 3)

    def test_websocket_reads_only_energy_forecast(self):
        class Socket:
            def __init__(self):
                self.responses = iter([{'type': 'auth_required'}, {'type': 'auth_ok'},
                                       {'id': 1, 'success': False, 'error': {'code': 'not_found'}},
                                       {'id': 2, 'success': True, 'result': {}}])
                self.sent = []
            def recv(self): return json.dumps(next(self.responses))
            def send(self, value): self.sent.append(json.loads(value))
            def close(self): pass
        socket = Socket()
        with patch.dict('os.environ', {'SUPERVISOR_TOKEN': 'test'}), patch.dict(sys.modules, {'websocket': SimpleNamespace(create_connection=lambda *args, **kwargs: socket)}):
            self.assertEqual(energy_data(), ({}, {}))
        self.assertEqual([message['type'] for message in socket.sent], ['auth', 'energy/get_prefs', 'energy/solar_forecast'])


if __name__ == '__main__': unittest.main()

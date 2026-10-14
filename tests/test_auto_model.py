import json
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from app.auto_model import AutoModel, local_points, live_plan, quality, signal_effect, select_model, forecast_day
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
            forecast = [{'datetime': (datetime.now(timezone.utc)+timedelta(hours=h+1)).isoformat(), 'temperature': 3+h/10}
                        for h in range(24)]
            readings = [{'entity': 'sensor.room', 'value': 21.0, 'quality': 'OK'},
                        {'entity': 'number.signal', 'value': 4.0, 'quality': 'OK'}]
            states=[{'entity_id':'number.signal','state':'4','last_updated':datetime.now(timezone.utc).isoformat(),
                     'attributes':{'unit_of_measurement':'°C','min':-15,'max':30,'step':0.5}}]
            result = trainer.result(config, readings, forecast, states)
            self.assertEqual(len(result['plan']), 24)

    def test_wrong_way_model_never_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            config,start=self.make_log(data)
            from app.model import evaluate_points
            def reversed_report(*args,**kwargs):
                report=evaluate_points(*args,**kwargs)
                for model in report['models']:
                    model['coefficients'] = [0] * len(model['coefficients'])
                    model['coefficients'][3] = 0.04
                return report
            with patch('app.auto_model.evaluate_points',side_effect=reversed_report):
                AutoModel(data).maybe_train(config,start+timedelta(days=30))
            saved=model_store.load_auto(data)
            self.assertEqual(saved['status']['state'],'needs_data')
            self.assertIsNotNone(saved['report'])
            self.assertTrue(saved['status']['quality_reasons']['temperature']['linear'])
            self.assertTrue(saved['status']['quality_reasons']['temperature']['lag6'])

    def test_weather_cloud_is_learned_without_extra_sensor(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            config=validate({'mode':'shadow','indoor':['sensor.room'],'outdoor':'sensor.out',
                             'applied_signal':'number.signal','weather':'weather.home'})
            start=datetime.now(timezone.utc)-timedelta(hours=259)
            temperature=21.0
            with sqlite3.connect(data/'measurements.sqlite') as db:
                db.execute('CREATE TABLE samples (time TEXT PRIMARY KEY, readings TEXT, settings TEXT)')
                for h in range(260):
                    outside=5+4*math.sin(h/19)
                    signal=3+3*math.sin(h/13)
                    cloud=50+40*math.sin(h/11)
                    readings=[{'entity':entity,'value':value,'quality':'OK'} for entity,value in
                              [('sensor.room',temperature),('sensor.out',outside),
                               ('number.signal',signal),('weather.home#cloud_coverage',cloud)]]
                    db.execute('INSERT INTO samples VALUES (?,?,?)',
                               ((start+timedelta(hours=h)).isoformat(),json.dumps(readings),json.dumps(config)))
                    temperature+=0.5+0.025*(outside-temperature)-0.01*signal-0.001*cloud
            AutoModel(data).maybe_train(config,datetime.now(timezone.utc))
            saved=model_store.load_auto(data)
            self.assertEqual(saved['status']['state'],'ready')
            self.assertTrue(saved['report']['mapping']['cloud_auto'])
            self.assertIsNone(saved['report']['fallback'])
            forecast=[{'datetime':(datetime.now(timezone.utc)+timedelta(hours=h+1)).isoformat(),
                       'temperature':5} for h in range(24)]
            readings=[{'entity':'sensor.room','value':21.0,'quality':'OK'},
                      {'entity':'number.signal','value':4.0,'quality':'OK'}]
            states=[{'entity_id':'number.signal','state':'4',
                     'last_updated':datetime.now(timezone.utc).isoformat(),
                     'attributes':{'unit_of_measurement':'°C','min':-15,'max':30,'step':0.5}}]
            result=AutoModel(data).result(config,readings,forecast,states)
            self.assertEqual(result['plan'], [])
            self.assertNotIn('utetemperaturmodellen används',result['message'])

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

    def test_selected_solar_sensor_needs_historical_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            config,start=self.make_log(data)
            config['solar']='sensor.sun'
            AutoModel(data).maybe_train(config,start+timedelta(days=30))
            saved=model_store.load_auto(data)
            self.assertEqual(saved['status']['state'],'collecting')
            self.assertIsNone(saved['report'])

    def test_pv_selection_without_history_keeps_temperature_model(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config, start = self.make_log(data)
            config['pv_power'] = 'sensor.pv'
            config['pv_forecast'] = 'a' * 32
            with sqlite3.connect(data / 'measurements.sqlite') as db:
                db.execute('UPDATE samples SET settings=?', (json.dumps(config),))
            AutoModel(data).maybe_train(config, start + timedelta(days=30))
            saved = model_store.load_auto(data)
            self.assertEqual(saved['status']['state'], 'ready')
            self.assertTrue(saved['report']['mapping']['pv_baseline'])

    def test_pv_model_can_be_ready_when_temperature_baseline_is_poor(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config = validate({'mode': 'shadow', 'indoor': ['sensor.room'], 'outdoor': 'sensor.out',
                               'applied_signal': 'number.signal', 'pv_power': 'sensor.pv',
                               'pv_forecast': 'a' * 32})
            start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=399)
            temperature = 21.0
            with sqlite3.connect(data / 'measurements.sqlite') as db:
                db.execute('CREATE TABLE samples (time TEXT PRIMARY KEY, readings TEXT, settings TEXT)')
                for h in range(400):
                    outside = 5 + 4 * math.sin(h / 19)
                    signal = 3 + 3 * math.sin(h / 13)
                    pv = max(0, math.sin((h % 24 - 6) * math.pi / 12)) * 5000 * (0.8 + 0.2 * math.sin(h / 7))
                    rows = [{'entity': entity, 'value': value, 'quality': 'OK'} for entity, value in
                            [('sensor.room', temperature), ('sensor.out', outside),
                             ('number.signal', signal), ('sensor.pv', pv)]]
                    db.execute('INSERT INTO samples VALUES (?,?,?)',
                               ((start + timedelta(hours=h)).isoformat(), json.dumps(rows), json.dumps(config)))
                    temperature += 0.5 + 0.025 * (outside - temperature) - 0.01 * signal + 0.0001 * pv
            AutoModel(data).maybe_train(config, datetime.now(timezone.utc))
            saved = model_store.load_auto(data)
            self.assertEqual(saved['status']['state'], 'ready')
            self.assertEqual(saved['report']['mapping']['pv_power'], 'sensor.pv')
            self.assertFalse(quality(saved['report']))

    def test_update_retrains_a_recent_model_with_old_quality_version(self):
        from app.auto_model import QUALITY_VERSION, mapping_for
        from app.model import evaluate_points
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config, start = self.make_log(data)
            clock = start+timedelta(days=30)
            points, info = local_points(data, mapping_for(config))
            report = evaluate_points(points, mapping_for(config))
            report['quality_version'] = QUALITY_VERSION-1
            model_store.save_auto(data, report, {'state': 'ready', 'trained_at': clock.isoformat()})
            with patch('app.auto_model.evaluate_points', wraps=evaluate_points) as evaluate:
                AutoModel(data).maybe_train(config, clock)
                evaluate.assert_called_once()
            self.assertEqual(model_store.load_auto(data)['report']['quality_version'], QUALITY_VERSION)

    def test_legacy_night_zeros_are_reused_without_rewriting_log(self):
        from app.auto_model import mapping_for
        from app.pv_history import STALE_PV
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config = validate({'mode': 'shadow', 'indoor': ['sensor.room'], 'outdoor': 'sensor.out',
                               'applied_signal': 'number.signal', 'pv_power': 'sensor.pv', 'pv_forecast': 'a'*32})
            start = datetime(2026, 10, 1, 17, tzinfo=timezone.utc)
            with sqlite3.connect(data/'measurements.sqlite') as db:
                db.execute('CREATE TABLE samples(time TEXT PRIMARY KEY, readings TEXT, settings TEXT)')
                for i in range(145):
                    rows = [{'entity': entity, 'value': value, 'quality': 'OK'} for entity, value in
                            [('sensor.room', 21.5), ('sensor.out', 5), ('number.signal', 0), ('sensor.pv', 0)]]
                    if 0 < i < 144:
                        rows[-1]['quality'] = STALE_PV
                    if i == 144:
                        rows[-1]['value'] = 50
                    db.execute('INSERT INTO samples VALUES(?,?,?)',
                               ((start+timedelta(minutes=5*i)).isoformat(), json.dumps(rows), json.dumps(config)))
            points, info = local_points(data, mapping_for(config))
            self.assertEqual(len(points), 13)
            self.assertEqual(info['recovered_pv_zero_samples'], 143)
            self.assertEqual(points[start+timedelta(hours=6)][3], 0)
            with sqlite3.connect(data/'measurements.sqlite') as db:
                raw = db.execute('SELECT readings FROM samples ORDER BY time LIMIT 1 OFFSET 1').fetchone()[0]
            self.assertEqual(json.loads(raw)[-1]['quality'], STALE_PV)
            # Selection changes still exclude the entire old log.
            config['indoor'] = ['sensor.other']
            self.assertEqual(local_points(data, mapping_for(config))[0], {})

    def test_shadow_absolute_limit_still_requires_persistence_and_sign(self):
        report = self.candidate_report()
        for m in report['metrics']:
            m.update(mae=0.34, lag_mae=0.34, baseline_mae=0.36)
        self.assertTrue(select_model(report))
        report['metrics'][0]['baseline_mae'] = 0.33
        self.assertFalse(select_model(report))
        report['metrics'][0]['baseline_mae'] = 0.36
        report['metrics'][0].update(mae=0.351, lag_mae=0.351)
        self.assertFalse(select_model(report))

    def test_pv_forecast_needs_matching_history_and_suggests_less_heat(self):
        config = validate({'pv_power': 'sensor.pv', 'pv_forecast': 'a' * 32})
        model = {'name': 'linear', 'coefficients': [0, 0, 0, -0.05, 0.0001],
                 'means': [0] * 5, 'scales': [1] * 5}
        report = {'models': [model], 'mapping': {'pv_power': 'sensor.pv'}}
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        base = [{'datetime': (start + timedelta(hours=h)).isoformat(),
                 'temperature': 5, 'pv_power': 0} for h in range(24)]
        sunny = [dict(row, pv_power=5000 if 4 <= h <= 10 else 0) for h, row in enumerate(base)]
        output = {'state': 'confirmed', 'min': -15, 'max': 30, 'step': 0.5}
        regular = live_plan(report, 21.5, 0, base, config, output)
        pv = live_plan(report, 21.5, 0, sunny, config, output)
        self.assertEqual(len(pv), 24)
        self.assertGreater(pv[1]['signal'], regular[1]['signal'])
        self.assertEqual(live_plan(report, 21.5, 0, [dict(row) for row in base[:12]], config, output), [])
        self.assertEqual(live_plan(report, 21.5, 0, [{k:v for k,v in row.items() if k != 'pv_power'} for row in base], config, output), [])

    def test_quality_gate_rejects_poor_model(self):
        report={'metrics':[{'hours':h,'windows':30,'mae':1.0,'baseline_mae':0.5} for h in (6,12,24)]}
        self.assertTrue(quality(report))

    def test_scaled_signal_direction_gate(self):
        metrics=[{'hours':h,'windows':30,'mae':0.2,'baseline_mae':1.0} for h in (6,12,24)]
        model={'name':'linear','coefficients':[0,0,0,-0.04],
               'means':[0]*4,'scales':[1,1,1,2]}
        report={'models':[model],'metrics':metrics}
        self.assertAlmostEqual(signal_effect(report),-0.18)
        self.assertFalse(quality(report))
        model['coefficients'][3]=0.04
        self.assertTrue(quality(report))
        model['coefficients'][3]=-2.0
        self.assertTrue(quality(report))

    def test_pv_model_requires_plausible_positive_gain(self):
        metrics = [{'hours': h, 'windows': 30, 'mae': 0.2, 'baseline_mae': 1.0} for h in (6, 12, 24)]
        model = {'name': 'linear', 'coefficients': [0, 0, 0, -0.04, 0.04],
                 'means': [0] * 5, 'scales': [1, 1, 1, 2, 1000]}
        report = {'models': [model], 'metrics': metrics, 'mapping': {'pv_power': 'sensor.pv'}}
        self.assertFalse(quality(report))
        model['coefficients'][4] = -0.04
        self.assertTrue(quality(report))
        model['coefficients'][4] = 10
        self.assertTrue(quality(report))

    def test_solar_forecast_requires_historical_feature_and_full_day(self):
        config=validate({'solar':'sensor.sun'})
        model={'name':'linear','coefficients':[0,0,0,-0.05,0.002],
               'means':[0,0,0,0,0],'scales':[1]*5}
        report={'models':[model],'mapping':{'solar':'sensor.sun'}}
        start=datetime.now(timezone.utc)+timedelta(hours=1)
        forecast=[{'datetime':(start+timedelta(hours=h)).isoformat(),'temperature':5,
                   'solar_irradiance':700 if 4<=h<=10 else 0} for h in range(24)]
        output={'state':'confirmed','min':-15,'max':30,'step':0.5}
        self.assertEqual(live_plan(report,21.5,5,forecast,config,output)[0]['signal'] % 0.5,0)
        self.assertEqual(live_plan(report,21.5,5,[{k:v for k,v in row.items() if k!='solar_irradiance'} for row in forecast],config,output),[])
        self.assertEqual(live_plan(report,21.5,5,forecast[:12],config,output),[])
        gapped=[dict(row) for row in forecast]
        gapped[5]['datetime']=(start+timedelta(hours=9)).isoformat()
        self.assertEqual(live_plan(report,21.5,5,gapped,config,output),[])
        self.assertEqual(live_plan(report,21.5,5,forecast,config,{'state':'stale'}),[])

    def test_sunny_period_suggests_earlier_reduction(self):
        config=validate({'solar':'sensor.sun'})
        model={'name':'linear','coefficients':[0,0,0,-0.05,0.002],
               'means':[0]*5,'scales':[1]*5}
        report={'models':[model],'mapping':{'solar':'sensor.sun'}}
        start=datetime.now(timezone.utc)+timedelta(hours=1)
        base=[{'datetime':(start+timedelta(hours=h)).isoformat(),
               'temperature':5,'solar_irradiance':0} for h in range(24)]
        sunny=[dict(row,solar_irradiance=700 if 4<=h<=10 else 0) for h,row in enumerate(base)]
        output={'state':'confirmed','min':-15,'max':30,'step':0.5}
        regular=live_plan(report,21.5,0,base,config,output)
        solar=live_plan(report,21.5,0,sunny,config,output)
        self.assertGreater(solar[1]['signal'],regular[1]['signal'])
        self.assertTrue(all(-15<=p['signal']<=30 and p['signal']*2==round(p['signal']*2) for p in solar))
        self.assertTrue(all(abs(b['signal']-a['signal'])<=1 for a,b in zip(solar,solar[1:])))

    def candidate_report(self):
        return {'models': [
            {'name': name, 'coefficients': [0, 0, 0, -0.02] + ([0]*4 if name == 'lag6' else []),
             'means': [0]*width, 'scales': [1]*width}
            for name, width in (('linear', 4), ('lag6', 8))],
            'metrics': [{'hours': h, 'windows': 30, 'mae': 0.25, 'lag_mae': 0.24,
                         'baseline_mae': 0.26} for h in (6, 12, 24)]}

    def test_absolute_gate_and_best_approved_candidate(self):
        report = self.candidate_report()
        self.assertFalse(quality(report, 'linear'))
        self.assertGreater(0.25, 0.85*0.26)
        self.assertTrue(select_model(report))
        self.assertEqual(report['selected_model'], 'lag6')
        report['models'][1]['coefficients'][3] = 0.02
        self.assertTrue(quality(report, 'lag6'))
        self.assertTrue(select_model(report))
        self.assertEqual(report['selected_model'], 'linear')

    def test_delayed_signal_response_and_live_lag_history(self):
        report = self.candidate_report()
        lag = report['models'][1]
        # Positive instantaneous coefficient, negative delayed mean signal gain.
        lag['coefficients'][3] = -0.04
        lag['coefficients'][6] = -0.05
        self.assertLess(signal_effect(report, 'lag6'), 0)
        self.assertFalse(quality(report, 'lag6'))
        report['selected_model'] = 'lag6'
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        history = {start-timedelta(hours=i): (21.5, 5, 0) for i in range(1, 7)}
        forecast = [{'datetime': (datetime.now(timezone.utc)+timedelta(hours=i+1)).isoformat(),
                     'temperature': 5} for i in range(24)]
        output = {'state': 'confirmed', 'min': -15, 'max': 30, 'step': 0.5}
        self.assertEqual(live_plan(report, 21.5, 0, forecast, validate({}), output), [])
        self.assertEqual(len(live_plan(report, 21.5, 0, forecast, validate({}), output, history)), 24)
        lag['coefficients'][3] = 0.02
        lag['coefficients'][6] = 0
        self.assertTrue(quality(report, 'lag6'))

    def test_rejected_temperature_and_pv_reports_are_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config, start = self.make_log(data)
            config.update(pv_power='sensor.pv', pv_forecast='a'*32)
            points = {start+timedelta(hours=i): (21, 5, 0) for i in range(250)}
            clock = max(points)
            def rejected(points, mapping, **kwargs):
                report = self.candidate_report()
                report['mapping'] = mapping
                for model in report['models']:
                    model['coefficients'][3] = 0.02
                return report
            with patch('app.auto_model.local_points', return_value=(points, {'incomplete_hours': 0})), patch(
                    'app.auto_model.evaluate_points', side_effect=rejected):
                trainer = AutoModel(data)
                trainer.maybe_train(config, clock)
            result = trainer.result(config, [], [])
            self.assertEqual(result['state'], 'needs_data')
            self.assertIsNotNone(result['report'])
            for kind in ('temperature', 'pv'):
                for name in ('linear', 'lag6'):
                    self.assertTrue(result['quality_reasons'][kind][name])
            self.assertNotIn('temperaturmodellen används', result['message'].lower())

    def test_cached_forecast_advances_past_old_hours(self):
        now = datetime.now(timezone.utc)
        forecast = [{'datetime': (now+timedelta(hours=h)).isoformat(), 'temperature': 5}
                    for h in range(-2, 26)]
        day = forecast_day(forecast)
        self.assertEqual(len(day), 24)
        self.assertEqual(day[0], forecast[2])
        blockers = []
        self.assertEqual(forecast_day(forecast[:20], blockers), [])
        self.assertIn('18 aktuella timmar', blockers[0])

    def test_daylight_only_pv_forecast_builds_plan_and_reports_missing_nights(self):
        from app.pv_forecast import hourly, combine
        # Fix a daytime clock so this exercises the same sparse shape as telemetry.
        clock = datetime(2026, 10, 14, 12, 12, tzinfo=timezone.utc)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return clock
        report = self.candidate_report()
        report['mapping'] = {'pv_power': 'sensor.pv'}
        for model in report['models']:
            model['coefficients'].insert(4, 0.0001)
            model['means'].insert(4, 0)
            model['scales'].insert(4, 1)
        report['selected_model'] = 'lag6'
        start = clock.replace(minute=0)
        history = {start-timedelta(hours=i): (21.5, 5, 0, 0) for i in range(1, 7)}
        weather = [{'datetime': (start+timedelta(hours=h)).isoformat(), 'temperature': 5} for h in range(24)]
        entry = 'a'*32
        raw = {entry: {'wh_hours': {(start+timedelta(hours=h)).isoformat(): 100
                                  for h in list(range(5))+list(range(18, 27))}}}
        config = validate({'pv_power': 'sensor.pv', 'pv_forecast': entry})
        output = {'state': 'confirmed', 'min': -15, 'max': 30, 'step': 0.5}
        sparse = {datetime.fromisoformat(t): v for t,v in raw[entry]['wh_hours'].items()}
        with patch('app.auto_model.datetime', Clock):
            blockers = []
            self.assertEqual(live_plan(report, 21.5, 0, combine(weather, sparse), config, output, history, blockers), [])
            self.assertIn('pv_power för 13 av 24 timmar', blockers[0])
            repaired = combine(weather, hourly(raw, entry))
            self.assertEqual(len(live_plan(report, 21.5, 0, repaired, config, output, history)), 24)
            blockers = []
            self.assertEqual(live_plan(report, 21.5, 0, repaired, config, output, {}, blockers), [])
            self.assertIn('Lag6 saknar komplett historiktimme', blockers[0])

            # The API exposes the same concrete blocker rather than a generic wait.
            from app.auto_model import mapping_for
            config.update(mode='shadow', indoor=['sensor.room'], outdoor='sensor.out',
                          applied_signal='number.signal')
            report['mapping'] = mapping_for(config)
            state = {'entity_id': 'number.signal', 'state': '0', 'last_updated': clock.isoformat(),
                     'attributes': {'unit_of_measurement': '°C', 'min': -15, 'max': 30, 'step': 0.5}}
            readings = [{'entity': 'sensor.room', 'quality': 'OK', 'value': 21.5},
                        {'entity': 'number.signal', 'quality': 'OK', 'value': 0}]
            with tempfile.TemporaryDirectory() as directory, patch('app.auto_model.local_points', return_value=(history, {})):
                data = Path(directory)
                model_store.save_auto(data, report, {'state': 'ready', 'message': 'Solcellsmodell validerad.'})
                trainer = AutoModel(data)
                waiting = trainer.result(config, readings, combine(weather, sparse), [state])
                self.assertEqual(waiting['state'], 'ready')
                self.assertIn('pv_power för 13 av 24 timmar', waiting['plan_blockers'][0])
                self.assertIn(waiting['plan_blockers'][0], waiting['message'])
                ready = trainer.result(config, readings, repaired, [state])
                self.assertEqual(len(ready['plan']), 24)
                self.assertEqual(ready['plan_blockers'], [])

    def test_live_plan_requires_full_forecast(self):
        self.assertEqual(live_plan({}, 21, 5, [], validate({})), [])


if __name__ == '__main__':
    unittest.main()

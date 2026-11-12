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


TESTED = {'plus': 60, 'minus': 60}


def add_test_signal(data):
    """Log every sampled hour as sent with the test signal, in alternating eight-hour blocks."""
    with sqlite3.connect(data / 'measurements.sqlite') as db:
        hours = {datetime.fromisoformat(row[0]).replace(minute=0, second=0, microsecond=0)
                 for row in db.execute('SELECT time FROM samples')}
        db.execute('CREATE TABLE IF NOT EXISTS control_samples (time TEXT PRIMARY KEY, result TEXT NOT NULL)')
        for hour in hours:
            sign = 1 if int(hour.timestamp()//(8*3600)) % 2 == 0 else -1
            result = json.dumps({'source': 'pi', 'decision': {'source': 'pi', 'test_signal': sign}})
            db.executemany('INSERT OR REPLACE INTO control_samples VALUES (?,?)',
                           [((hour+timedelta(minutes=m)).isoformat(), result) for m in range(30)])


class AutoModelTests(unittest.TestCase):
    def make_log(self, data, hours=250):
        config = validate({'mode': 'shadow', 'indoor': ['sensor.room'],
                           'outdoor': 'sensor.out', 'applied_signal': 'number.signal'})
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)-timedelta(hours=hours-1)
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
        add_test_signal(data)
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
            add_test_signal(data)
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
            add_test_signal(data)
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
               'means':[0]*4,'scales':[1,1,1,2],'test_hours':TESTED}
        report={'models':[model],'metrics':metrics}
        self.assertAlmostEqual(signal_effect(report),-0.18)
        self.assertFalse(quality(report))
        model['coefficients'][3]=0.04
        self.assertTrue(quality(report))
        model['coefficients'][3]=-2.0
        self.assertTrue(quality(report))
        # A signal that does next to nothing would make the planner push it to its limits.
        model['coefficients'][3]=-0.002
        self.assertAlmostEqual(signal_effect(report),-0.009)
        self.assertTrue(any('signalrespons' in r for r in quality(report)))

    def test_pv_model_requires_plausible_positive_gain(self):
        metrics = [{'hours': h, 'windows': 30, 'mae': 0.2, 'baseline_mae': 1.0} for h in (6, 12, 24)]
        model = {'name': 'linear', 'coefficients': [0, 0, 0, -0.04, 0.04],
                 'means': [0] * 5, 'scales': [1, 1, 1, 2, 1000], 'test_hours': TESTED}
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

    def test_warm_now_cold_night_starts_heating_in_time(self):
        # A warm start rewards less heat first; the plan must still reach the cold night in time.
        model={'name':'linear','coefficients':[0,0.02,0,-0.01],'means':[0,-7.5,0,16],'scales':[1]*4}
        start=datetime.now(timezone.utc)+timedelta(minutes=10)
        outside=[13.5-h for h in range(12)]+[1.5+h for h in range(12)]
        forecast=[{'datetime':(start+timedelta(hours=h)).isoformat(),'temperature':t} for h,t in enumerate(outside)]
        output={'state':'confirmed','min':-15,'max':30,'step':0.5}
        plan=live_plan({'models':[model],'mapping':{}},22.3,13.5,forecast,validate({}),output)
        self.assertEqual(len(plan),24)
        self.assertGreater(min(p['indoor'] for p in plan),20.95)

    def candidate_report(self):
        return {'models': [
            {'name': name, 'coefficients': [0, 0, 0, -0.02] + ([0]*4 if name == 'lag6' else []),
             'means': [0]*width, 'scales': [1]*width, 'test_hours': dict(TESTED)}
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

class IdentificationTests(unittest.TestCase):
    """A model must have seen the test signal, and a model in use is kept until clearly beaten."""

    def report(self, **metrics):
        report = AutoModelTests.candidate_report(None)
        report['metrics'] = [dict({'hours': h, 'windows': 30, 'mae': 0.25, 'lag_mae': 0.24, 'baseline_mae': 0.26}, **metrics)
                             for h in (1, 6, 12, 24)]
        return report

    def test_test_hours_need_enough_samples_of_one_sign(self):
        from app.auto_model import test_hours, count_tests
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        rows = []
        for hour, signs in enumerate(([1]*40, [1]*20, [1]*30+[-1]*30, [-1]*50+[0]*10, [0]*60)):
            rows += [((start+timedelta(hours=hour, minutes=m)).isoformat(),
                      json.dumps({'source': 'pi', 'decision': {'test_signal': sign} if sign else {}})) for m, sign in enumerate(signs)]
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            self.assertEqual(test_hours(data, start), {})
            with sqlite3.connect(data / 'measurements.sqlite') as db:
                db.execute('CREATE TABLE samples (time TEXT PRIMARY KEY, readings TEXT, settings TEXT)')
            self.assertEqual(test_hours(data, start), {})
            with sqlite3.connect(data / 'measurements.sqlite') as db:
                db.execute('CREATE TABLE control_samples (time TEXT PRIMARY KEY, result TEXT NOT NULL)')
                db.executemany('INSERT INTO control_samples VALUES (?,?)', rows)
            tested = test_hours(data, start)
            # Too few samples, mixed signs and hours without test signal do not count.
            self.assertEqual(tested, {start: 1, start+timedelta(hours=3): -1})
            self.assertEqual(test_hours(data, start+timedelta(hours=1)), {start+timedelta(hours=3): -1})
            self.assertEqual(count_tests(tested, start, start+timedelta(hours=3)), {'plus': 1, 'minus': 0})

    def test_models_without_test_signal_are_rejected_by_both_gates(self):
        from app.auto_model import learning_quality, MIN_TEST_HOURS
        report = self.report()
        self.assertFalse(quality(report, 'lag6'))
        self.assertFalse(learning_quality(report, 'lag6'))
        report['models'][1]['test_hours'] = {'plus': 60, 'minus': MIN_TEST_HOURS-1}
        self.assertTrue(any('testsignal' in r for r in quality(report, 'lag6')))
        self.assertTrue(any('testsignal' in r for r in learning_quality(report, 'lag6')))
        del report['models'][0]['test_hours']
        self.assertTrue(any('0 h testsignal' in r for r in quality(report, 'linear')))
        self.assertFalse(select_model(report))
        self.assertIsNone(report['learning_model'])

    def test_accurate_model_is_not_rejected_for_losing_to_a_steady_temperature(self):
        from app.auto_model import learning_quality
        steady = self.report(mae=0.12, lag_mae=0.12, baseline_mae=0.08)
        steady['metrics'][0].update(mae=0.09, lag_mae=0.09, baseline_mae=0.04)
        self.assertTrue(select_model(steady))
        self.assertFalse(learning_quality(steady, 'linear'))
        worse = self.report(mae=0.2, lag_mae=0.2, baseline_mae=0.1)
        self.assertFalse(select_model(worse))
        self.assertTrue(any('persistens' in r for r in quality(worse, 'linear')))
        next_hour = self.report()
        next_hour['metrics'][0].update(mae=0.12, lag_mae=0.12, baseline_mae=0.05)
        self.assertTrue(any('1 h' in r and 'persistens' in r for r in learning_quality(next_hour, 'linear')))

    def test_old_training_is_fine_while_validation_is_fresh(self):
        from app.auto_model import validation_reasons
        now = datetime.now(timezone.utc)
        report = {'validation_method': 'daily_rolling_14d', 'training_end': (now-timedelta(days=40)).isoformat(),
                  'data_end': now.isoformat(), 'validation_end': (now-timedelta(hours=3)).isoformat()}
        self.assertEqual(validation_reasons(report, now), [])

    def test_fixed_models_are_scored_on_the_same_windows(self):
        from app.comparison import compare
        from app.model import solve
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        points, temp = {}, 21.0
        for hour in range(600):
            out, signal = 5+4*math.sin(hour/19), 3+3*math.sin(hour/13)
            points[start+timedelta(hours=hour)] = (temp, out, signal)
            temp += 0.5+0.025*(out-temp)-0.01*signal
        first = compare(points, solve)
        self.assertEqual(first['kept_metrics'], {})
        runaway = dict(first['models'][1], coefficients=[1e6]*8)
        wrong_width = dict(first['models'][0], name='linear', coefficients=[0]*5, means=[0]*5, scales=[1]*5)
        again = compare(points, solve, kept=[first['models'][0], runaway])
        self.assertEqual(list(again['kept_metrics']), ['linear'])
        self.assertEqual(sorted(again['kept_metrics']['linear']), [1, 6, 12, 24])
        self.assertLess(again['kept_metrics']['linear'][24], 1e-5)
        self.assertEqual(compare(points, solve, kept=[wrong_width])['kept_metrics'], {})

    def retained(self, new_mae, old_mae, old=None, new_tested=True):
        from app.auto_model import retain
        report = self.report(mae=new_mae, lag_mae=new_mae)
        if not new_tested:
            for model in report['models']: model['test_hours'] = {'plus': 0, 'minus': 0}
        previous = old or self.report()
        for model in previous['models']: model['trained_at'] = 'earlier'
        report['kept_metrics'] = {name: {h: old_mae for h in (1, 6, 12, 24)} for name in ('linear', 'lag6')}
        retain(report, previous)
        return report

    def test_model_in_use_is_kept_until_clearly_beaten(self):
        kept = self.retained(new_mae=0.19, old_mae=0.2)
        self.assertTrue(all(m.get('kept') and m['trained_at'] == 'earlier' for m in kept['models']))
        self.assertEqual([m['mae'] for m in kept['metrics']], [0.2]*4)
        self.assertNotIn('kept_metrics', kept)
        replaced = self.retained(new_mae=0.15, old_mae=0.2)
        self.assertFalse(any(m.get('kept') for m in replaced['models']))
        self.assertEqual([m['lag_mae'] for m in replaced['metrics']], [0.15]*4)

    def test_model_type_in_use_stays_unless_the_other_is_clearly_better(self):
        from app.auto_model import retain
        def chosen(linear, lag, previous):
            report = self.report(mae=linear, lag_mae=lag)
            retain(report, previous)
            select_model(report)
            self.assertNotIn('in_use', report)
            return report['selected_model'], report['learning_model']
        self.assertEqual(chosen(0.2, 0.21, None), ('linear', 'linear'))
        using_lag = dict(self.report(), selected_model='lag6', learning_model='lag6')
        self.assertEqual(chosen(0.2, 0.21, using_lag), ('lag6', 'lag6'))
        self.assertEqual(chosen(0.15, 0.21, using_lag), ('linear', 'linear'))
        # A type that no longer passes cannot stay.
        failing = self.report(mae=0.2, lag_mae=0.9)
        retain(failing, using_lag)
        select_model(failing)
        self.assertEqual((failing['selected_model'], failing['learning_model']), ('linear', 'linear'))

    def test_kept_model_gives_way_when_it_fails_and_stays_when_the_new_one_fails(self):
        # The earlier model no longer passes on fresh windows: use the new one, whatever it is.
        self.assertFalse(any(m.get('kept') for m in self.retained(new_mae=0.5, old_mae=0.9)['models']))
        wrong_way = self.report()
        for model in wrong_way['models']: model['coefficients'][3] = 0.02
        self.assertFalse(any(m.get('kept') for m in self.retained(new_mae=0.5, old_mae=0.2, old=wrong_way)['models']))
        # A much lower error does not help a new fit that never saw the test signal.
        untested = self.retained(new_mae=0.05, old_mae=0.25, new_tested=False)
        self.assertTrue(all(m.get('kept') for m in untested['models']))
        self.assertEqual(untested['models'][0]['test_hours'], TESTED)
        self.assertTrue(select_model(untested))

    def test_daily_training_keeps_the_approved_model_and_reports_test_signal(self):
        from app.auto_model import mapping_for, MIN_TEST_HOURS
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config, start = AutoModelTests.make_log(None, data)
            clock = start+timedelta(hours=249)
            trainer = AutoModel(data)
            trainer.maybe_train(config, clock)
            first = model_store.load_auto(data)
            self.assertEqual(first['status']['state'], 'ready')
            self.assertEqual(first['status']['test_signal']['required'], MIN_TEST_HOURS)
            self.assertGreaterEqual(first['status']['test_signal']['plus'], MIN_TEST_HOURS)
            model = first['report']['models'][1]
            self.assertEqual(model['trained_at'], clock.isoformat())
            self.assertGreaterEqual(min(model['test_hours'].values()), MIN_TEST_HOURS)
            received = {}
            from app.model import evaluate_points
            def slightly_better(points, mapping, kept=(), **kwargs):
                received['kept'] = kept
                report = evaluate_points(points, mapping, kept=kept, **kwargs)
                for m in report['metrics']: m.update(mae=0.19, lag_mae=0.19, baseline_mae=0.3)
                report['kept_metrics'] = {name: {h: 0.2 for h in (1, 6, 12, 24)} for name in ('linear', 'lag6')}
                return report
            with patch('app.auto_model.evaluate_points', side_effect=slightly_better):
                trainer.maybe_train(config, clock+timedelta(days=1, hours=1))
            second = model_store.load_auto(data)
            self.assertEqual([m['name'] for m in received['kept']], ['linear', 'lag6'])
            self.assertEqual(second['status']['state'], 'ready')
            self.assertEqual(second['status']['trained_at'], (clock+timedelta(days=1, hours=1)).isoformat())
            kept = second['report']['models'][1]
            self.assertTrue(kept['kept'])
            self.assertEqual((kept['trained_at'], kept['coefficients']), (model['trained_at'], model['coefficients']))
            # A day whose data cannot be evaluated keeps the model for the next attempt.
            with patch('app.auto_model.evaluate_points', side_effect=ValueError('Inga kompletta rullande 24-timmars testfönster.')):
                trainer.last_check = None
                trainer.maybe_train(config, clock+timedelta(days=2, hours=2))
            gap = model_store.load_auto(data)
            self.assertEqual(gap['status']['state'], 'needs_data')
            self.assertIn('testfönster', gap['status']['quality_reasons']['temperature']['validation'][0])
            self.assertEqual(gap['status']['evaluations']['temperature']['models'][1]['coefficients'], model['coefficients'])
            with patch('app.auto_model.evaluate_points', side_effect=slightly_better):
                trainer.last_check = None
                trainer.maybe_train(config, clock+timedelta(days=2, hours=4))
            self.assertEqual(model_store.load_auto(data)['report']['models'][1]['coefficients'], model['coefficients'])
            # A model trained for another sensor selection is never carried over.
            other = dict(config, outdoor='sensor.other')
            with sqlite3.connect(data / 'measurements.sqlite') as db:
                for stamp, raw in db.execute('SELECT time, readings FROM samples').fetchall():
                    db.execute('UPDATE samples SET readings=?, settings=? WHERE time=?',
                               (raw.replace('sensor.out', 'sensor.other'), json.dumps(other), stamp))
            with patch('app.auto_model.evaluate_points', side_effect=slightly_better):
                trainer.last_check = None
                trainer.maybe_train(other, clock+timedelta(days=1, hours=2))
            self.assertEqual(received['kept'], ())


class ValidationFreshnessTests(unittest.TestCase):
    def test_old_validation_blocks_both_quality_profiles_and_live_plan(self):
        from app.auto_model import validation_reasons, learning_quality
        now = datetime.now(timezone.utc)
        report = {'validation_method': 'daily_rolling_14d',
                  'training_end': (now-timedelta(hours=1)).isoformat(),
                  'data_end': now.isoformat(),
                  'validation_end': (now-timedelta(days=4)).isoformat()}
        self.assertTrue(validation_reasons(report))
        self.assertTrue(any('Valideringen' in r for r in learning_quality(report, 'linear')))
        blockers = []
        self.assertEqual(live_plan(report, 18, 5, [], validate({}), blockers=blockers), [])
        self.assertTrue(any('Valideringen' in r for r in blockers))

    def test_old_saved_report_cannot_plan_even_if_its_own_data_was_fresh(self):
        from app.auto_model import validation_reasons
        old = datetime.now(timezone.utc)-timedelta(days=4)
        report = {'validation_method': 'daily_rolling_14d',
                  'training_end': old.isoformat(), 'data_end': old.isoformat(),
                  'validation_end': old.isoformat()}
        self.assertEqual(validation_reasons(report), [])
        self.assertTrue(validation_reasons(report, datetime.now(timezone.utc)))

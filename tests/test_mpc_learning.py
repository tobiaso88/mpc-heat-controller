import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app import model_store
from app.auto_model import AutoModel, disturbance_profile, learning_quality, live_plan, select_model, PROFILE_LIMIT
from app.control import Control
from app.core import validate
from app.trends import read as read_trends
import test_auto_model as fixtures


def config(**extra):
    return validate(dict(mode='shadow', indoor=['sensor.in'], outdoor='sensor.out', applied_signal='number.hp',
                         old_automation='automation.old', watchdog_verified=True, exclusive_writer_confirmed=True,
                         watchdog_seconds=7200, **dict({'mpc_learning': True}, **extra)))


def learning(signal, age=timedelta(0)):
    return {'learning': {'state': 'ready', 'signal': signal, 'model': 'lag6',
                         'planned_at': (datetime.now(timezone.utc)-age).isoformat()}}


def learning_report():
    """Fails the strict shadow gate (MAE > 0.35, worse than persistence at 24 h) but is usable for learning."""
    report = fixtures.AutoModelTests.candidate_report(None)
    report['metrics'] = [{'hours': 1, 'windows': 30, 'mae': 0.10, 'lag_mae': 0.09, 'baseline_mae': 0.13}] + [
        {'hours': h, 'windows': 30, 'mae': 0.5, 'lag_mae': 0.45, 'baseline_mae': 0.4} for h in (6, 12, 24)]
    return report


class ChoiceTests(unittest.TestCase):
    pi = {'signal': 5.0, 'error': 0.0}  # inside 21.5, target 21.5, comfort 21–22

    def test_pi_alone_when_learning_is_off_or_no_plan(self):
        signal, decision = Control.choose(dict(config(), mpc_learning=False), self.pi, learning(2.0))
        self.assertEqual((signal, decision['source']), (5.0, 'pi'))
        signal, decision = Control.choose(config(), self.pi, {'learning': {'state': 'blocked', 'reasons': ['Ingen modell.']}})
        self.assertEqual((signal, decision['source']), (5.0, 'pi'))
        self.assertIn('Ingen modell.', decision['reason'])
        self.assertEqual(Control.choose(config(), self.pi, None)[1]['source'], 'pi')

    def test_mpc_is_limited_to_band_around_pi(self):
        for planned, sent in [(4.0, 4.0), (1.0, 3.0), (9.5, 7.0)]:
            signal, decision = Control.choose(config(), self.pi, learning(planned))
            self.assertEqual((signal, decision['source']), (sent, 'mpc'))
            self.assertEqual(decision['limited'], planned != sent)
        signal, _ = Control.choose(config(mpc_max_deviation=1.0), self.pi, learning(1.0))
        self.assertEqual(signal, 4.0)

    def test_stale_or_invalid_plan_falls_back_to_pi(self):
        self.assertEqual(Control.choose(config(), self.pi, learning(4.0, age=timedelta(minutes=16)))[1]['source'], 'pi')
        self.assertEqual(Control.choose(config(), self.pi, learning(float('nan')))[1]['source'], 'pi')
        self.assertEqual(Control.choose(config(), self.pi, {'learning': {'state': 'ready', 'signal': 4.0}})[1]['source'], 'pi')

    def test_cold_inside_means_pi_and_warm_inside_only_allows_less_heat(self):
        cold = {'signal': 5.0, 'error': 0.6}  # inside 20.9 < 21
        signal, decision = Control.choose(config(), cold, learning(6.0))
        self.assertEqual((signal, decision['source']), (5.0, 'pi'))
        self.assertIn('under komfortområdet', decision['reason'])
        warm = {'signal': 5.0, 'error': -0.7}  # inside 22.2 > 22
        signal, decision = Control.choose(config(), warm, learning(3.0))  # more heat than PI
        self.assertEqual((signal, decision['source']), (5.0, 'pi'))
        signal, decision = Control.choose(config(), warm, learning(6.5))  # less heat than PI
        self.assertEqual((signal, decision['source']), (6.5, 'mpc'))


class GuardTests(unittest.TestCase):
    """Comfort 21–22, target 21.5. `error` is target minus the indoor temperature."""

    def choose(self, inside, planned, guard=None):
        signal, decision = Control.choose(config(), {'signal': 5.0, 'error': 21.5-inside}, learning(planned), guard=guard)
        return signal, decision['source'], decision['guard']

    def test_pi_keeps_control_until_clearly_back_inside_after_cold(self):
        self.assertEqual(self.choose(20.9, 6.0), (5.0, 'pi', 'cold'))
        # Back above the limit, but not yet 0.2 °C inside: PI still decides.
        self.assertEqual(self.choose(21.1, 6.0, 'cold'), (5.0, 'pi', 'cold'))
        self.assertEqual(self.choose(21.2, 6.0, 'cold'), (6.0, 'mpc', None))
        # The same temperature without a preceding cold spell is MPC's.
        self.assertEqual(self.choose(21.1, 6.0), (6.0, 'mpc', None))
        _, decision = Control.choose(config(), {'signal': 5.0, 'error': 0.4}, learning(6.0), guard='cold')
        self.assertIn('tills den nått 21,2 °C', decision['reason'])

    def test_warm_guard_holds_until_clearly_back_inside(self):
        self.assertEqual(self.choose(22.1, 3.0), (5.0, 'pi', 'warm'))
        self.assertEqual(self.choose(21.9, 3.0, 'warm'), (5.0, 'pi', 'warm'))
        self.assertEqual(self.choose(21.9, 6.5, 'warm'), (6.5, 'mpc', 'warm'))  # less heat is still allowed
        self.assertEqual(self.choose(21.8, 3.0, 'warm'), (3.0, 'mpc', None))
        self.assertEqual(self.choose(21.9, 3.0), (3.0, 'mpc', None))

    def test_narrow_comfort_range_releases_at_the_target(self):
        c = config(comfort_min=21.4, comfort_max=21.6)
        self.assertEqual(Control.guard_state(c, 21.45, 'cold'), 'cold')
        self.assertIsNone(Control.guard_state(c, 21.5, 'cold'))
        self.assertIsNone(Control.guard_state(c, 21.5, 'warm'))

    def test_guard_follows_temperature_without_a_plan_and_is_cleared_on_start(self):
        signal, decision = Control.choose(config(), {'signal': 5.0, 'error': 0.6}, {'learning': {'state': 'blocked'}})
        self.assertEqual((decision['source'], decision['guard']), ('pi', 'cold'))
        self.assertNotIn('guard', Control.choose(dict(config(), mpc_learning=False), {'signal': 5.0, 'error': 0.6}, None)[1])

    def test_send_remembers_the_guard_between_cycles(self):
        c, states, request, control = SendTests.setup_control(self)
        t = control.changed_at
        sources = []
        for minute, inside in enumerate((20.9, 21.05, 21.1, 21.25, 21.05), 1):
            sources.append(control.send(c, states, {'signal': 5.0, 'error': 21.5-inside}, clock=t+minute*60, mpc=learning(5.0))['source'])
        self.assertEqual(sources, ['pi', 'pi', 'pi', 'mpc', 'mpc'])
        control.stop()
        control.arm(c, states, [{'entity': e, 'value': 20, 'quality': 'OK'} for e in ['sensor.in', 'sensor.out']])
        self.assertIsNone(control.guard)


class SendTests(unittest.TestCase):
    def setup_control(self, **extra):
        c = config(**extra)
        states = [{'entity_id': 'number.hp', 'state': '5', 'attributes': {'unit_of_measurement': '°C', 'min': -20, 'max': 40, 'step': 0.5}},
                  {'entity_id': 'automation.old', 'state': 'off'}]
        items = [{'entity': e, 'value': 20, 'quality': 'OK'} for e in ['sensor.in', 'sensor.out']]
        request = Mock()
        control = Control(request)
        control.arm(c, states, items)
        return c, states, request, control

    def test_sends_mpc_value_within_rate_limit_and_records_source(self):
        c, states, request, control = self.setup_control()
        t = control.changed_at
        decision = control.send(c, states, {'signal': 5.0, 'error': 0.0}, clock=t+60, mpc=learning(1.0))
        # Within ±2 of PI gives 3.0, but the 2 °C/h rate allows nothing yet after one minute.
        self.assertEqual(decision['source'], 'mpc')
        self.assertEqual(request.call_args.args[1]['value'], 5.0)
        # After a steady hour the first step is still one half degree; the rest follows at 2 °C/h.
        for minutes, sent in ((60, 4.5), (75, 4.0), (90, 3.5), (105, 3.0), (120, 3.0)):
            control.send(c, states, {'signal': 5.0, 'error': 0.0}, clock=t+minutes*60, mpc=learning(1.0))
            self.assertEqual(request.call_args.args[1]['value'], sent)
            states[0]['state'] = str(sent)
        self.assertEqual((control.get()['source'], control.get()['decision']['source']), ('mpc', 'mpc'))
        self.assertTrue(control.get()['active'])

    def test_missing_plan_keeps_control_active_with_pi(self):
        c, states, request, control = self.setup_control()
        control.send(c, states, {'signal': 5.0, 'error': 0.0}, clock=control.changed_at, mpc={'state': 'error', 'plan': []})
        self.assertEqual(request.call_args.args[1]['value'], 5.0)
        self.assertEqual(control.get()['source'], 'pi')
        self.assertTrue(control.get()['active'])

    def test_resume_settings_from_earlier_version_get_new_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            old = {k: v for k, v in config(mpc_learning=False).items() if k not in ('mpc_learning', 'mpc_max_deviation')}
            Control(Mock(), data).persist(old)
            self.assertEqual(Control(Mock(), data).resume_settings, config(mpc_learning=False))


class LearningModelTests(unittest.TestCase):
    def test_learning_gate_is_looser_than_shadow_gate_but_keeps_safety_checks(self):
        report = learning_report()
        self.assertFalse(select_model(report))
        self.assertIsNone(report['selected_model'])
        self.assertEqual(report['learning_model'], 'lag6')
        self.assertFalse(learning_quality(report, 'linear'))
        worse = learning_report()
        worse['metrics'][0]['lag_mae'] = 0.2
        self.assertTrue(any('persistens' in r for r in learning_quality(worse, 'lag6')))
        ceiling = learning_report()
        ceiling['metrics'][-1]['mae'] = 0.9
        self.assertTrue(learning_quality(ceiling, 'linear'))
        wrong_way = learning_report()
        wrong_way['models'][0]['coefficients'][3] = 0.02
        self.assertTrue(any('signalrespons' in r for r in learning_quality(wrong_way, 'linear')))
        missing = learning_report()
        missing['metrics'] = missing['metrics'][1:]
        self.assertTrue(learning_quality(missing, 'linear'))

    def test_profile_learns_evening_heat_and_is_limited(self):
        report = learning_report()
        clock = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        points, temperature = {}, 21.0
        for i in range(10*24, 0, -1):
            t = clock-timedelta(hours=i)
            points[t] = (temperature, 5.0, 0.0)
            # The linear model predicts no change; evening hours warm, morning hours cool.
            temperature += 0.2 if t.hour == 18 else 0.6 if t.hour == 19 else -0.1 if t.hour == 6 else 0.0
        profile = disturbance_profile(report, 'linear', points, clock)
        self.assertAlmostEqual(profile[18], 0.2)
        self.assertEqual(profile[19], PROFILE_LIMIT)
        self.assertAlmostEqual(profile[6], -0.1)
        self.assertAlmostEqual(profile[12], 0.0)
        # Only the last seven days are used.
        self.assertEqual(disturbance_profile(report, 'linear', points, clock+timedelta(days=8)), {})

    def test_profile_moves_planned_temperatures(self):
        report = dict(learning_report(), selected_model='linear')
        start = datetime.now(timezone.utc)+timedelta(minutes=10)
        forecast = [{'datetime': (start+timedelta(hours=h)).isoformat(), 'temperature': 5} for h in range(24)]
        output = {'state': 'confirmed', 'min': -15, 'max': 30, 'step': 0.5}
        plain = live_plan(report, 21.5, 0, forecast, validate({}), output)
        warm = live_plan(report, 21.5, 0, forecast, validate({}), output, profile={h: 0.3 for h in range(24)})
        self.assertEqual(len(warm), 24)
        # Expected extra warmth lets the plan give less heat (higher signal) than without it.
        self.assertGreater(sum(p['signal'] for p in warm), sum(p['signal'] for p in plain))

    def test_result_plans_with_learning_model_when_shadow_gate_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            c, start = fixtures.AutoModelTests.make_log(None, data)
            c = dict(c, mpc_learning=True)

            def evaluated(points, mapping, **kwargs):
                return dict(learning_report(), mapping=mapping)
            trainer = AutoModel(data)
            with patch('app.auto_model.evaluate_points', side_effect=evaluated):
                trainer.maybe_train(c, start+timedelta(days=30))
            self.assertEqual(model_store.load_auto(data)['status']['state'], 'needs_data')
            forecast = [{'datetime': (datetime.now(timezone.utc)+timedelta(hours=h+1)).isoformat(), 'temperature': 3} for h in range(24)]
            readings = [{'entity': 'sensor.room', 'value': 21.0, 'quality': 'OK'}, {'entity': 'number.signal', 'value': 4.0, 'quality': 'OK'}]
            states = [{'entity_id': 'number.signal', 'state': '4', 'last_updated': datetime.now(timezone.utc).isoformat(),
                       'attributes': {'unit_of_measurement': '°C', 'min': -15, 'max': 30, 'step': 0.5}}]
            result = trainer.result(c, readings, forecast, states)
            # With recent logged history, the lowest-error lag6 candidate can plan.
            self.assertEqual((result['learning']['state'], result['learning']['model']), ('ready', 'lag6'))
            self.assertEqual(result['plan'], result['learning']['plan'])
            self.assertEqual(result['learning']['signal'], result['plan'][0]['signal'])
            self.assertIn('MPC-inlärning', result['message'])
            # Still fall back to the usable linear candidate if recent lag history is absent.
            with patch.object(trainer, 'hourly_points', return_value={}):
                fallback = trainer.result(c, readings, forecast, states)
            self.assertEqual((fallback['learning']['state'], fallback['learning']['model']), ('ready', 'linear'))
            shadow_only = trainer.result(dict(c, mpc_learning=False), readings, forecast, states)
            self.assertNotIn('learning', shadow_only)
            self.assertEqual(shadow_only['plan'], [])


class HistoryTests(unittest.TestCase):
    def test_trends_show_which_regulator_controlled(self):
        from app.telemetry import Collector
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            c = config()
            collector = Collector(lambda: c, data)
            now = datetime.now(timezone.utc)
            for minutes, source in ((40, 'pi'), (20, 'mpc'), (5, None)):
                stamp = (now-timedelta(minutes=minutes)).isoformat()
                collector.store(stamp, [{'entity': 'sensor.in', 'value': 21.0, 'quality': 'OK'}], c)
                if source:
                    collector.store_control(stamp, source, {'source': source, 'reason': ''})
            points = read_trends(data)['points']
            self.assertEqual([p['source'] for p in points], ['pi', 'mpc', None])
            with sqlite3.connect(data/'measurements.sqlite') as db:
                self.assertEqual(json.loads(db.execute('SELECT result FROM control_samples ORDER BY time DESC').fetchone()[0])['source'], 'mpc')


class SettingsTests(unittest.TestCase):
    def test_defaults_and_limits(self):
        self.assertEqual((validate({})['mpc_learning'], validate({})['mpc_max_deviation']), (False, 2.0))
        for value in (0.4, 3.5, True, 'x', float('nan')):
            with self.assertRaises(ValueError):
                validate({'mpc_max_deviation': value})
        with self.assertRaises(ValueError):
            validate({'mpc_learning': 1})


if __name__ == '__main__':
    unittest.main()

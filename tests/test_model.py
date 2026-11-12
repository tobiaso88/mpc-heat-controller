import math
import unittest
from datetime import datetime, timezone, timedelta
from app.model import evaluate, solve

class ModelTests(unittest.TestCase):
    def dataset(self):
        lines=['entity_id,state,last_changed'];temp=21
        start=datetime(2025,1,1,tzinfo=timezone.utc)
        for h in range(600):
            outside=5+4*math.sin(h/19);signal=3+3*math.sin(h/13)
            stamp=(start+timedelta(hours=h)).isoformat()
            for entity,value in [('sensor.room',temp),('sensor.out',outside),('sensor.signal',signal)]:
                lines.append(f'{entity},{value},{stamp}')
            temp+=0.5+0.025*(outside-temp)-0.01*signal
        return '\n'.join(lines)

    def test_known_model_holdout(self):
        result=evaluate(self.dataset(),{'indoor':['sensor.room'],'outdoor':'sensor.out','signal':'sensor.signal','start':'2025-01-01','end':'2025-01-26'})
        self.assertEqual(result['complete_hours'],600)
        self.assertEqual(result['training_pairs'], 335)
        self.assertEqual(result['validation_method'], 'daily_rolling_14d')
        self.assertEqual(len(set(m['windows'] for m in result['metrics'])),1)
        self.assertTrue(all(math.isfinite(m['lag_mae']) for m in result['metrics']))
        for m in result['metrics']:
            self.assertGreater(m['windows'],0)
            self.assertLess(m['mae'],0.00001)
        self.assertEqual(result['status'],'offline_candidate')

    def test_insufficient_and_duplicate_inputs(self):
        with self.assertRaises(ValueError):evaluate('entity_id,state,last_changed\n',{'indoor':['sensor.x'],'outdoor':'sensor.y','signal':'sensor.z','start':'2025-01-01','end':'2025-02-01'})
        with self.assertRaises(ValueError):evaluate('',{'indoor':['sensor.x'],'outdoor':'sensor.x','signal':'sensor.z'})

    def test_singular(self):
        with self.assertRaises(ValueError):solve([[1,1],[1,1]],[2,2])

    def test_rolling_predictions_do_not_learn_their_future_outcomes(self):
        from app.comparison import compare
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        points, temp = {}, 21.0
        for h in range(600):
            out, signal = 5+4*math.sin(h/19), 3+3*math.sin(h/13)
            points[start+timedelta(hours=h)] = (temp, out, signal)
            temp += 0.5+0.025*(out-temp)-0.01*signal
        original = compare(points, solve)
        # Change only indoor outcomes after the last test start; no future
        # temperature may enter that test's fitted model or recursive inputs.
        changed = dict(points)
        for h in range(580, 600):
            t = start+timedelta(hours=h)
            changed[t] = (points[t][0]+1.5, *points[t][1:])
        updated = compare(changed, solve)
        self.assertEqual([r['predicted'] for r in original['example']],
                         [r['predicted'] for r in updated['example']])
        self.assertNotEqual(original['example'][-1]['actual'], updated['example'][-1]['actual'])
        # The separately fitted live model does consume the newer observations.
        self.assertNotEqual(original['models'][0]['coefficients'], updated['models'][0]['coefficients'])
        for fold in updated['validation_folds']:
            self.assertLess(fold['training_end'], fold['cutoff'])
        self.assertGreater(updated['training_end'], updated['validation_folds'][-1]['training_end'])
        self.assertGreaterEqual(datetime.fromisoformat(updated['training_start']),
                                max(points)-timedelta(days=14))

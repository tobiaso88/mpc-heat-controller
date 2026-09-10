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
        self.assertEqual(result['training_pairs'],419)
        for m in result['metrics']:
            self.assertGreater(m['windows'],0)
            self.assertLess(m['mae'],0.00001)
        self.assertEqual(result['status'],'offline_candidate')

    def test_insufficient_and_duplicate_inputs(self):
        with self.assertRaises(ValueError):evaluate('entity_id,state,last_changed\n',{'indoor':['sensor.x'],'outdoor':'sensor.y','signal':'sensor.z','start':'2025-01-01','end':'2025-02-01'})
        with self.assertRaises(ValueError):evaluate('',{'indoor':['sensor.x'],'outdoor':'sensor.x','signal':'sensor.z'})

    def test_singular(self):
        with self.assertRaises(ValueError):solve([[1,1],[1,1]],[2,2])

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core import validate
from app.shadow_evaluation import read
from app.telemetry import Collector


class ShadowEvaluationTests(unittest.TestCase):
    def test_archives_decision_weather_and_compares_later_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            config=validate({'mode':'shadow','indoor':['sensor.room'],'outdoor':'sensor.out',
                             'applied_signal':'number.signal','weather':'weather.x','solar':'sensor.sun'})
            collector=Collector(lambda:config,data)
            decision=datetime.now(timezone.utc)-timedelta(hours=40)
            stamp=decision.isoformat()
            points=[];plan=[]
            for h in range(24):
                weather_time=decision+timedelta(hours=h+1)
                indoor_time=weather_time+timedelta(hours=1)
                points.append({'datetime':weather_time.isoformat(),'temperature':5,'solar_irradiance':700,'extra':'discard'})
                plan.append({'datetime':indoor_time.isoformat(),'indoor':21.4,'signal':4.5})
                readings=[{'entity':entity,'value':value,'quality':'OK'} for entity,value in
                          [('sensor.room',21.5),('sensor.out',6),('sensor.sun',650),('number.signal',4.0)]]
                collector.store(weather_time.isoformat(),readings,config)
                collector.store(indoor_time.isoformat(),readings,config)
            collector.store_mpc(stamp,{'state':'ready','plan':plan},
                                {'entity':'weather.x','fetched_at':stamp,'points':points},config)
            with sqlite3.connect(data/'measurements.sqlite') as db:
                row=db.execute('SELECT entity,fetched_at,fields,forecast,plan FROM mpc_forecasts').fetchone()
            self.assertEqual(row[0],'weather.x')
            self.assertEqual(row[1],stamp)
            self.assertNotIn('extra',json.loads(row[3])[0])
            self.assertIn('solar_irradiance',json.loads(row[2]))
            result=read(data)
            self.assertEqual(result['kind'],'shadow_proposal_evaluation')
            proposal=result['proposals'][0]
            self.assertEqual(len(proposal['points']),24)
            self.assertAlmostEqual(proposal['outdoor_mae'],1.0)
            self.assertAlmostEqual(proposal['indoor_mae'],0.1)
            self.assertEqual(proposal['points'][0]['actual_solar_irradiance'],650)
            self.assertIn('inte effekten av alternativa MPC-kommandon',result['message'])

    def test_retention_removes_old_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            config=validate({'mode':'shadow','indoor':['sensor.room'],'outdoor':'sensor.out','applied_signal':'number.signal'})
            collector=Collector(lambda:config,data)
            old=(datetime.now(timezone.utc)-timedelta(days=91)).isoformat()
            plan=[{'datetime':old,'indoor':21,'signal':4}]
            forecast={'entity':'weather.x','fetched_at':old,'points':[{'datetime':old,'temperature':5}]}
            collector.store_mpc(old,{'state':'ready','plan':plan},forecast,config)
            with sqlite3.connect(data/'measurements.sqlite') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM mpc_forecasts').fetchone()[0],0)


if __name__=='__main__': unittest.main()

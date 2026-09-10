import tempfile
import unittest
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from app.core import validate
from app.telemetry import normalize_forecast, readings, Collector

class TelemetryTests(unittest.TestCase):
    def test_forecast_units_and_order(self):
        t=datetime.now(timezone.utc)
        rows=[{'datetime':(t+timedelta(hours=h)).isoformat(),'temperature':50} for h in [2,0,1]]
        result=normalize_forecast({'service_response':{'weather.x':{'forecast':rows}}},'weather.x','°F',t)
        self.assertEqual(len(result),3);self.assertEqual(result[0]['temperature'],10)

    def test_stale_or_gapped_forecast_rejected(self):
        t=datetime.now(timezone.utc)
        for hours in ([-4,-3],[0,5]):
            response={'service_response':{'weather.x':{'forecast':[{'datetime':(t+timedelta(hours=h)).isoformat(),'temperature':10} for h in hours]}}}
            with self.assertRaises(ValueError):normalize_forecast(response,'weather.x','°C',t)

    def test_observe_and_missing(self):
        c=validate({'observe':['sensor.a'],'outdoor':'sensor.b'})
        result=readings(c,[{'entity_id':'sensor.a','state':'21','last_reported':datetime.now(timezone.utc).isoformat(),'attributes':{'unit_of_measurement':'°C'}}])
        self.assertEqual(result[0]['roles'],['Uppföljning']);self.assertEqual(result[0]['quality'],'OK');self.assertIsNone(result[1]['value'])

    def test_background_logging_and_demo_exclusion(self):
        c=validate({'mode':'shadow','indoor':['sensor.a'],'outdoor':'sensor.b'})
        with tempfile.TemporaryDirectory() as d, patch('app.telemetry.request',return_value=[]):
            collector=Collector(lambda:c,Path(d));collector.cycle()
            with sqlite3.connect(Path(d)/'measurements.sqlite') as db:self.assertEqual(db.execute('select count(*) from samples').fetchone()[0],1)
            c['mode']='demo';collector.cycle()
            with sqlite3.connect(Path(d)/'measurements.sqlite') as db:self.assertEqual(db.execute('select count(*) from samples').fetchone()[0],1)
            self.assertFalse(collector.get()['logging'])

    def test_weather_service_and_cache(self):
        t=datetime.now(timezone.utc)
        c=validate({'weather':'weather.x'})
        states=[{'entity_id':'weather.x','attributes':{'temperature_unit':'°C'}}]
        response={'service_response':{'weather.x':{'forecast':[{'datetime':(t+timedelta(hours=h)).isoformat(),'temperature':5} for h in [1,2,3]]}}}
        with tempfile.TemporaryDirectory() as d, patch('app.telemetry.request', side_effect=[states,response,states]) as req:
            collector=Collector(lambda:c,Path(d));collector.cycle();collector.cycle()
            self.assertEqual(len(collector.get()['forecast']['points']),3)
            self.assertEqual(req.call_count,3)
            self.assertEqual(req.call_args_list[1].args,('services/weather/get_forecasts?return_response',{'entity_id':'weather.x','type':'hourly'}))

    def test_ha_failure_clears_old_readings(self):
        with tempfile.TemporaryDirectory() as d, patch('app.telemetry.request', side_effect=OSError('offline')):
            collector=Collector(lambda:validate({}),Path(d));collector.cycle()
            self.assertTrue(collector.get()['error']);self.assertEqual(collector.get()['forecast']['points'],[])

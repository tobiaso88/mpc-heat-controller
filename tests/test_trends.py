import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from app.trends import read

class TrendTests(unittest.TestCase):
    def test_empty_log(self):
        with tempfile.TemporaryDirectory() as d:self.assertEqual(read(Path(d))['points'],[])

    def test_reads_roles_without_partial_average(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            c={'indoor':['sensor.a','sensor.b'],'outdoor':'sensor.out','applied_signal':'number.hp','target':21.5}
            items=[{'entity':'sensor.a','value':21,'quality':'OK'},{'entity':'sensor.b','value':None,'quality':'Saknas'},{'entity':'number.hp','value':5,'quality':'OK'}]
            with sqlite3.connect(p/'measurements.sqlite') as db:
                db.execute('CREATE TABLE samples (time TEXT,readings TEXT,settings TEXT)')
                stamp=datetime.now(timezone.utc).isoformat()
                db.execute('INSERT INTO samples VALUES (?,?,?)',(stamp,json.dumps(items),json.dumps(c)))
                db.execute('CREATE TABLE mpc_samples (time TEXT,result TEXT)')
                db.execute('INSERT INTO mpc_samples VALUES (?,?)',(stamp,json.dumps({'state':'ready','signal':4.5})))
            point=read(p)['points'][0]
            self.assertIsNone(point['indoor']);self.assertEqual(point['applied'],5);self.assertEqual(point['target'],21.5)
            self.assertEqual(point['mpc_proposal'],4.5)

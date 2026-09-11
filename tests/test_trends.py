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
                db.execute('INSERT INTO samples VALUES (?,?,?)',(datetime.now(timezone.utc).isoformat(),json.dumps(items),json.dumps(c)))
            point=read(p)['points'][0]
            self.assertIsNone(point['indoor']);self.assertEqual(point['applied'],5);self.assertEqual(point['target'],21.5)

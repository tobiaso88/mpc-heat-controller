import tempfile
import unittest
from pathlib import Path
from app.core import validate, DEFAULT, simulate
from app import server

class Tests(unittest.TestCase):
    def test_no_active_mode(self):
        with self.assertRaises(ValueError): validate({"mode": "active"})

    def test_invalid_comfort(self):
        for change in ({"target": 24}, {"target": float("nan")}, {"comfort_min": 23}, {"signal_min": 40}, {"max_step": 0}):
            with self.assertRaises(ValueError): validate(change)

    def test_invalid_sensor_shapes(self):
        for value in ([["sensor.x"]], ["sensor.x", "sensor.x"], [None]):
            with self.assertRaises(ValueError): validate({"indoor": value})

    def test_plan_limits(self):
        c=validate(DEFAULT); plan=simulate(c)
        self.assertEqual(len(plan),24)
        previous=5
        for p in plan:
            self.assertLessEqual(abs(p["signal"]-previous),c["max_step"])
            self.assertTrue(c["signal_min"]<=p["signal"]<=c["signal_max"])
            previous=p["signal"]

    def test_persistence(self):
        original=server.DATA
        with tempfile.TemporaryDirectory() as d:
            try:
                server.DATA=Path(d); server.save({"target":21.7}); self.assertEqual(server.config()["target"],21.7)
            finally: server.DATA=original

    def test_missing_live_data_never_plans(self):
        c=validate({"mode":"shadow","indoor":["sensor.room"],"outdoor":"sensor.outside"})
        s=server.status(c,{"entities":[]})
        self.assertIsNone(s["temperature"]); self.assertEqual(s["plan"],[])

    def test_csv_invalid_and_gaps(self):
        r=server.inspect_csv(b"entity_id,state,last_changed\nsensor.x,21,2026-01-01T00:00:00Z\nsensor.x,unknown,2026-01-01T01:00:00Z\nsensor.x,22,2026-01-01T03:00:00Z\n")
        e=r["entities"][0];self.assertEqual(e["invalid"],1); self.assertEqual(e["max_gap_hours"],3)
        with self.assertRaises(ValueError):server.inspect_csv(b"x,y\n1,2")

if __name__ == "__main__": unittest.main()

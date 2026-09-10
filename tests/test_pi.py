import unittest
from app.pi import PI
from app.core import validate

class PITests(unittest.TestCase):
    def config(self, **kw):
        return validate(dict(mode='shadow',indoor=['sensor.room'],outdoor='sensor.out',**kw))
    def items(self, room=20, outside=5):
        return [dict(entity=e,value=v,quality='OK') for e,v in [('sensor.room',room),('sensor.out',outside)]]
    def test_direction_and_rate(self):
        pi=PI();c=self.config()
        pi.step(c,self.items(),0)
        r=pi.step(c,self.items(),300)
        self.assertLess(r['signal'],5)
        self.assertGreaterEqual(r['signal'],round(5-2/12,3))
        self.assertTrue(r['integrator_frozen'])
        hot=PI();hot.step(c,self.items(room=23),0)
        self.assertGreater(hot.step(c,self.items(room=23),300)['signal'],5)
    def test_antiwindup_and_recovery(self):
        pi=PI();c=self.config(pi_limit=1)
        for t in range(0,10000,300):r=pi.step(c,self.items(room=10),t)
        self.assertEqual(r['i'],0)
        self.assertGreaterEqual(r['signal'],4)
        self.assertGreater(pi.step(c,self.items(room=23),10000)['signal'],r['signal'])
    def test_elapsed_time_integral_and_reset(self):
        pi=PI();c=self.config(pi_kp=0,pi_ki=0.1)
        pi.step(c,self.items(room=21),0)
        r=pi.step(c,self.items(room=21),600)
        self.assertAlmostEqual(r['i'],0.008,places=3)
        self.assertEqual(pi.step(c,self.items(),2000)['i'],0)
        self.assertIsNone(pi.step(c,[],2300)['signal'])
        self.assertEqual(pi.integral,0)
    def test_config_change_and_demo(self):
        pi=PI();c=self.config()
        pi.step(c,self.items(),0)
        c['target']=21.8
        self.assertEqual(pi.step(c,self.items(),300)['i'],0)
        c['mode']='demo'
        self.assertIsNone(pi.step(c,self.items(),600)['signal'])
    def test_defaults_and_validation(self):
        self.assertEqual(validate({})['pi_ki'],0.1)
        for change in ({'pi_kp':-1},{'pi_ki':float('nan')},{'pi_rate':0}):
            with self.assertRaises(ValueError):validate(change)

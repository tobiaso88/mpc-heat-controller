import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from datetime import datetime,timezone
from app.entities import identity,discovery,payload,EntityPublisher,SENSORS,DEFAULT_ENTITY_IDS
from app.core import validate

class EntityTests(unittest.TestCase):
    def test_identity_stable_and_scoped(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            self.assertEqual(identity(Path(a)),identity(Path(a)))
            self.assertNotEqual(identity(Path(a)),identity(Path(b)))

    def test_discovery_readonly_and_expiring(self):
        configs=discovery('test')
        self.assertEqual(len(configs),16)
        for topic,c in configs.items():
            self.assertTrue(topic.startswith('homeassistant/sensor/mpc_test/'))
            self.assertEqual(c['default_entity_id'],'sensor.'+DEFAULT_ENTITY_IDS[topic.rsplit('/',2)[-2]])
            self.assertNotIn('command_topic',c)
            self.assertEqual(c['expire_after'],180)
            self.assertIn('availability_template',c)
        self.assertNotIn('device_class',configs['homeassistant/sensor/mpc_test/error/config'])
        dashboard=(Path(__file__).parent.parent/'home_assistant'/'dashboard.yaml').read_text()
        for entity_id in DEFAULT_ENTITY_IDS.values():self.assertIn('sensor.'+entity_id,dashboard)

    def test_values_missing_stale_and_demo(self):
        c=validate({'mode':'shadow','indoor':['sensor.a'],'outdoor':'sensor.o'})
        snapshot={'sampled_at':datetime.fromtimestamp(1000,timezone.utc).isoformat(),'config_used':c,'readings':[{'entity':'sensor.a','value':21,'quality':'OK'},{'entity':'sensor.o','value':4,'quality':'OK'}],
                  'pi':{'signal':4,'p':2,'i':0.5,'error':1,'compensation':-2.5},'control':{'active':False,'last_command':{'value':5,'sent_at':900}}}
        result=payload(c,snapshot,1001)
        self.assertEqual(result['indoor'],21);self.assertEqual(result['proposal'],4)
        self.assertEqual(result['outdoor'],4)
        self.assertEqual(result['last_sent'],5)
        self.assertIsNone(payload(c,snapshot,1500)['proposal'])
        self.assertIsNone(payload(dict(c,target=21.7),snapshot,1001)['indoor'])
        c['mode']='demo'
        self.assertIsNone(payload(c,snapshot,1001)['proposal'])

    def test_publish_scoped_retained_config_only(self):
        with tempfile.TemporaryDirectory() as d:
            req=Mock();pub=EntityPublisher(req,lambda:validate({}),lambda:{},Path(d))
            pub.cycle()
            self.assertTrue(pub.get()['ok'])
            self.assertEqual(req.call_count,len(SENSORS)+1)
            for call in req.call_args_list:
                self.assertEqual(call.args[0],'services/mqtt/publish')
            self.assertFalse(req.call_args.args[1]['retain'])
            req.reset_mock();pub.cycle();self.assertEqual(req.call_count,1)
            req.side_effect=OSError();pub.cycle();self.assertFalse(pub.get()['ok'])

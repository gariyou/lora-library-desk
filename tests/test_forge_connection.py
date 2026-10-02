import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('forge_server_test', APP / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
f = s.forge_connector
BRIDGE = APP / 'forge_bridge' / 'bridge_core.py'
core_spec = importlib.util.spec_from_file_location('forge_core_test', BRIDGE)
core = importlib.util.module_from_spec(core_spec)
core_spec.loader.exec_module(core)
PROFILE = {'steps':18,'sampler':'DPM++ 2M SDE','scheduler':'Normal','width':1152,'height':896,
           'cfg_scale':2,'seed':-1,'prompt':'original','negative_prompt':'negative',
           'widgets':{'id:txt2img_hr-checkbox':{'type':'checkbox','label':'Hires','value':True},
                      'path:Dynamic Prompts/slider:Max generations#1':{'type':'slider','value':0}},
           'modules':[{'name':'vae','path':'C:/models/vae.safetensors'}, {'name':'encoder','path':'C:/models/encoder.safetensors'}],
           'options':{'randn_source':'CPU','CLIP_stop_at_last_layers':2},'preset':'anima','dtype':'Automatic'}

class ForgeProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        s.DATA_ROOT = self.root
        s.LORA_DB_PATH = self.root/'test.sqlite3'
        s.LORA_CONFIG_PATH = self.root/'config.json'
        s.LEGACY_LORA_STORE_PATH = self.root/'legacy.json'
        s.SCAN_STATE.update(status='idle',revision=0,result=None,config={'watch_dirs':[]},error='')
        self.path = str(self.root/'checkpoint.safetensors')

    def test_full_profile_survives_reload_and_unrelated_metadata_edit(self):
        s.merge_metadata_changes([self.path],s.sanitize_metadata_input({'generation_settings':PROFILE,'notes':'keep'}))
        s.merge_metadata_changes([self.path],s.sanitize_metadata_input({'rating':5}))
        item = s.load_lora_store()[self.path]
        self.assertEqual(item['generation_settings'],PROFILE)
        self.assertEqual(item['notes'],'keep')

    def test_model_profiles_are_separate_and_empty_modules_remain_empty(self):
        second = str(self.root/'second.safetensors')
        s.merge_metadata_changes([self.path],s.sanitize_metadata_input({'generation_settings':PROFILE}))
        other = dict(PROFILE,steps=40,modules=[])
        s.merge_metadata_changes([second],s.sanitize_metadata_input({'generation_settings':other}))
        loaded=s.load_lora_store()
        self.assertEqual(loaded[self.path]['generation_settings']['steps'],18)
        self.assertEqual(loaded[second]['generation_settings']['steps'],40)
        self.assertEqual(loaded[second]['generation_settings']['modules'],[])

    def test_invalid_settings_are_rejected(self):
        for changes in ({'width':1111},{'steps':True},{'cfg_scale':float('nan')},{'modules':'bad'},{'widgets':[]},{'options':[]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                f.normalize_generation_settings(dict(PROFILE,**changes))

    def test_reverse_save_checks_model_before_writing(self):
        Path(self.path).write_bytes(b'')
        snapshot={'checkpoint_path':self.path,'settings':PROFILE}
        with patch.object(f,'forge_request',return_value={'connected':True,'snapshot':snapshot}),patch.object(f,'fresh_snapshot',return_value=snapshot),patch.object(s,'load_lora_config',return_value={'watch_dirs':[str(self.root)]}):
            with self.assertRaisesRegex(ValueError,'異なります'):
                s.save_current_forge_settings(str(self.root/'other.safetensors'))
            self.assertEqual(s.load_lora_store(),{})
            saved=s.save_current_forge_settings(self.path)
        self.assertTrue(saved['ok'])
        self.assertEqual(s.load_lora_store()[self.path]['generation_settings'],PROFILE)

    def test_exact_path_matching_and_zero_lora_weight(self):
        catalog={'loras':[{'name':'test','path':self.path}]}
        def request(route,data=None):
            if route.endswith('status'):return {'connected':True,'busy':False}
            if route.endswith('catalog'):return catalog
            return data
        with patch.object(f,'forge_request',side_effect=request):
            result=f.queue_library_item({'model_family':'lora','path':self.path,'strength_max':0,'triggers':[]},{})
        self.assertEqual(result['weight'],0)
        with self.assertRaises(ValueError):f.matching_resource(str(self.root/'unknown'),catalog['loras'])

class BridgeStateTests(unittest.TestCase):
    def setUp(self):
        self.now=100
        self.state=core.BridgeState(clock=lambda:self.now)
        self.state.capture({'settings':PROFILE},'owner')

    def test_owner_and_busy_prevent_wrong_browser_or_generation_changes(self):
        command=self.state.queue({'mode':'checkpoint'})
        self.state.capture({'settings':{}},'other')
        self.assertEqual(self.state.status()['snapshot']['session'],'owner')
        self.assertIsNone(self.state.take('other'))
        self.assertIsNone(self.state.take('owner',busy=True))
        self.assertEqual(self.state.status()['last_result']['id'],command['id'])
        self.assertFalse(self.state.status()['last_result']['ok'])

    def test_expiry_and_fresh_capture(self):
        self.state.request_capture()
        self.state.capture({'settings':{}},'other')
        self.assertTrue(self.state.status()['capture_requested'])
        self.state.capture({'settings':PROFILE},'owner')
        self.assertFalse(self.state.status()['capture_requested'])
        self.state.queue({'mode':'lora'})
        self.now+=31
        self.assertFalse(self.state.status()['pending'])
        self.assertFalse(self.state.status()['last_result']['ok'])
        with self.assertRaises(ValueError):self.state.queue({'mode':'lora'})

    def test_preset_defer_preserves_command_id(self):
        queued=self.state.queue({'mode':'checkpoint'})
        command=self.state.take('owner')
        self.state.defer(command)
        resumed=self.state.take('owner')
        self.state.finish(resumed,True,'applied')
        self.assertEqual(self.state.status()['last_result']['id'],queued['id'])

    def test_script_selection_indexes_are_saved_as_choices(self):
        choices=[('None','None'),('X/Y/Z Plot','X/Y/Z Plot')]
        self.assertEqual(core.capture_selection(0,choices),'None')
        self.assertEqual(core.capture_selection([1,0],choices),['X/Y/Z Plot','None'])
        self.assertIsNone(core.capture_selection(None,choices))
        with self.assertRaises(ValueError):core.capture_selection(2,choices)

    def test_browser_json_preserves_option_values_and_rejects_type_changes(self):
        self.assertEqual(core.compatible_option(0,0.0),0.0)
        self.assertIsInstance(core.compatible_option(0,0.0),float)
        self.assertEqual(core.compatible_option(2.0,2),2)
        for value,current in [(True,1),(1.5,1),('0',0),(float('nan'),0.0)]:
            with self.subTest(value=value),self.assertRaises(ValueError):core.compatible_option(value,current)

if __name__=='__main__':unittest.main()

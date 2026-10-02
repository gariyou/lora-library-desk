import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server as s
f = s.forge_connector

CATALOG = {'samplers': ['Euler a', 'DPM++ 2M SDE'], 'schedulers': ['Normal', 'Karras']}


class ReferenceSettingsTests(unittest.TestCase):
    def test_prompts_and_recorded_settings_with_combined_sampler(self):
        settings, warnings = f.reference_settings({
            'prompt': 'a blue house', 'negative_prompt': 'blurry', 'steps': '24',
            'sampler': 'DPM++ 2M SDE Karras', 'cfg_scale': '4.5',
            'seed': '0', 'width': '1024', 'height': '768',
        }, CATALOG)
        self.assertEqual(settings, {'prompt': 'a blue house', 'negative_prompt': 'blurry',
            'steps': 24, 'sampler': 'DPM++ 2M SDE', 'scheduler': 'Karras',
            'cfg_scale': 4.5, 'seed': 0, 'width': 1024, 'height': 768})
        self.assertEqual(warnings, [])

    def test_missing_fields_do_not_reset_other_settings(self):
        settings, warnings = f.reference_settings({'prompt': 'a blue house'}, CATALOG)
        self.assertEqual(settings, {'prompt': 'a blue house', 'negative_prompt': ''})
        self.assertEqual(warnings, [])
        self.assertNotIn('modules', settings)
        self.assertNotIn('widgets', settings)
        self.assertNotIn('options', settings)

    def test_invalid_and_unavailable_settings_are_reported(self):
        settings, warnings = f.reference_settings({'prompt': 'a blue house',
            'width': '999999', 'height': '777', 'steps': True, 'cfg_scale': 'nan',
            'sampler': 'unknown', 'scheduler': 'unknown'}, CATALOG)
        self.assertEqual(settings, {'prompt': 'a blue house', 'negative_prompt': ''})
        self.assertEqual(len(warnings), 6)
        with self.assertRaises(ValueError):
            f.reference_settings({'width': 'invalid'}, CATALOG)

    def test_current_checkpoint_and_partial_settings_only_are_queued(self):
        calls = []
        path = str(ROOT / 'current.safetensors')
        def request(route, data=None):
            calls.append((route, data))
            if route.endswith('status'):
                return {'connected': True, 'busy': False}
            if route.endswith('catalog'):
                return dict(CATALOG, checkpoints=[{'path': path, 'title': 'Current checkpoint'}])
            return {'ok': True, 'id': 'command-test'}
        with patch.object(f, 'forge_request', side_effect=request), patch.object(f, 'fresh_snapshot', return_value={'checkpoint_path': path}):
            result = f.queue_reference_settings({'prompt': 'a blue house'})
        self.assertEqual(result['applied_fields'], ['prompt', 'negative_prompt'])
        self.assertEqual(calls[-1], ('/library-desk/command', {
            'mode': 'checkpoint', 'checkpoint': 'Current checkpoint',
            'settings': {'prompt': 'a blue house', 'negative_prompt': ''}}))

    def test_busy_and_disconnected_prevent_commands(self):
        for status in ({'busy': True, 'connected': True}, {'connected': False}):
            with self.subTest(status=status), patch.object(f, 'forge_request', return_value=status) as request:
                with self.assertRaises(ValueError):
                    f.queue_reference_settings({'prompt': 'a blue house'})
                self.assertEqual(request.call_count, 1)

    def test_a1111_size_and_scheduler_survive_saved_metadata(self):
        raw = 'a blue house\nNegative prompt: blurry\nSteps: 24, Sampler: Euler a, Schedule type: Karras, CFG scale: 4.5, Seed: 0, Size: 1024x768'
        metadata = s.normalize_reference_prompt_metadata({'raw_parameters': raw})
        self.assertEqual((metadata['scheduler'], metadata['width'], metadata['height']), ('Karras', '1024', '768'))
        self.assertEqual(metadata['seed'], '0')
        imported = s.build_browser_import_prompt_metadata({'prompt': 'a blue house', 'scheduler': 'Normal', 'width': 896, 'height': 1152})
        self.assertEqual((imported['scheduler'], imported['width'], imported['height']), ('Normal', '896', '1152'))
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'reference.png'
            image.write_bytes(b'test-only image')
            s.save_reference_prompt_metadata(image, imported)
            restored = s.load_reference_prompt_metadata(image)
            self.assertEqual((restored['scheduler'], restored['width'], restored['height']), ('Normal', '896', '1152'))


class ReferenceSendHTTPTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.model = self.root / 'model.safetensors'
        self.model.write_bytes(b'test-only model')
        folder = s.reference_folder_for_model(self.model)
        folder.mkdir()
        self.image = folder / 'reference.png'
        self.image.write_bytes(b'test-only reference')
        self.reference = {'path': str(self.image.resolve()), 'prompt': 'a blue house'}
        patches = [patch.object(s, 'load_lora_config', return_value={'watch_dirs': [str(self.root)]}),
                   patch.object(s, 'build_reference_image_items', return_value=[self.reference]),
                   patch.object(f, 'queue_reference_settings', return_value={'ok': True, 'id': 'test'})]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        self.queue = patches[-1].target.queue_reference_settings
        self.http = s.ThreadingHTTPServer(('127.0.0.1', 0), s.build_handler(ROOT / 'web'))
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def request(self, file=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        body = {'path': str(self.model), 'file': str(file or self.image), 'settings': {'prompt': 'client text must be ignored'}}
        request = urllib.request.Request(f'http://127.0.0.1:{self.http.server_port}/api/lora/forge/send-reference',
            data=json.dumps(body).encode(), headers=headers)
        return json.load(urllib.request.urlopen(request, timeout=5))

    def test_endpoint_uses_saved_reference_and_ignores_client_settings(self):
        self.assertEqual(self.request()['id'], 'test')
        self.queue.assert_called_once_with(self.reference)

    def test_external_origin_and_other_folder_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request(origin='https://unrelated.test')
        self.assertEqual(e.exception.code, 403)
        other = self.root / 'other.png'
        other.write_bytes(b'unrelated')
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request(file=other)
        self.assertEqual(e.exception.code, 404)
        self.queue.assert_not_called()


if __name__ == '__main__':
    unittest.main()

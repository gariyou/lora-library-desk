import json
import unittest
import zipfile
from pathlib import Path
import test_server as fixtures
server = fixtures.server

GRAPH = {"version": 0.4, "nodes": [{"id": 1, "type": "KSampler"}], "links": []}
API_GRAPH = {"1": {"class_type": "KSampler", "inputs": {"seed": 1}}}

class WorkflowImportTests(unittest.TestCase):
    setUp = fixtures.LoRAManagerServerTests.setUp
    restore_globals = fixtures.LoRAManagerServerTests.restore_globals
    create_model = fixtures.LoRAManagerServerTests.create_model

    def configure(self):
        self.models = self.workspace / 'StableDiffusion'
        self.workflows = self.workspace / 'ComfyUI' / 'user' / 'default' / 'workflows'
        self.downloads = self.workspace / 'Downloads'
        for folder in [self.models, self.workflows, self.downloads]: folder.mkdir(parents=True)
        server.save_lora_config({'watch_dirs': [str(self.models), str(self.workflows)]})

    def write_json(self, path, data=GRAPH):
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def write_zip(self, name='workflows.zip', entries=None):
        path = self.downloads / name
        with zipfile.ZipFile(path, 'w') as archive:
            for entry, value in (entries or {'Basic.json': GRAPH, 'Advanced.json': API_GRAPH}).items():
                archive.writestr(entry, value if isinstance(value, (bytes, str)) else json.dumps(value))
        return path

    def test_workflow_json_import_routes_and_preserves_metadata(self):
        self.configure()
        source = self.write_json(self.downloads / 'Anima.json')
        result = server.import_downloaded_lora(str(source), {'title': 'Anima Workflow', 'source_url': 'https://civitai.com/models/2426853/anima-workflows', 'description': 'Workflow notes'})
        self.assertFalse(source.exists())
        self.assertEqual(Path(result['path']).parent, self.workflows)
        item = server.build_lora_item_snapshot(Path(result['path']))
        self.assertEqual(item['model_family'], 'workflow')
        self.assertEqual(item['prompt_snippet'], '')
        self.assertIn('Workflow notes', item['notes'])
        self.assertIn('2426853', item['source_url'])
        self.assertTrue(server.is_valid_model_path(Path(result['path']), [str(self.workflows)]))

    def test_workflow_zip_extracts_only_graphs_preserves_archive_and_duplicates(self):
        self.configure()
        existing = self.write_json(self.workflows / 'Basic.json')
        before = existing.read_bytes()
        source = self.write_zip(entries={'Basic.json': GRAPH, 'nested/Basic.json': API_GRAPH, 'settings.json': {'setting': 1}, 'README.md': 'instructions', 'install.py': 'raise RuntimeError()'})
        result = server.import_downloaded_lora(str(source), {'source_url': 'https://civitai.com/models/2426853'})
        self.assertEqual(result['imported_count'], 2)
        self.assertEqual(existing.read_bytes(), before)
        self.assertTrue((self.workflows / 'Basic (2).json').exists())
        self.assertTrue((self.workflows / 'nested - Basic.json').exists())
        self.assertFalse((self.workflows / 'settings.json').exists())
        self.assertFalse((self.workflows / 'install.py').exists())
        self.assertTrue(Path(result['archive_path']).exists())
        self.assertFalse(source.exists())
        self.assertEqual(len(server.scan_lora_library()['items']), 3)

    def test_workflow_scan_excludes_metadata_and_invalid_json(self):
        self.configure()
        self.write_json(self.workflows / 'api.json', API_GRAPH)
        self.write_json(self.workflows / 'settings.json', {'nodes': [], 'other': True})
        self.write_json(self.models / 'model.info.json', {'model': {'type': 'LORA'}})
        (self.workflows / 'broken.json').write_text('{', encoding='utf-8')
        items = server.scan_lora_library()['items']
        self.assertEqual([item['filename'] for item in items], ['api.json'])
        self.assertEqual(items[0]['model_family_label'], 'Workflow')

    def test_workflow_invalid_package_and_unsafe_paths_preserve_source(self):
        self.configure()
        for entries in [{'../escape.json': GRAPH}, {'/absolute.json': GRAPH}, {'C:/drive.json': GRAPH}, {'config.json': {'other': 1}}]:
            source = self.write_zip(entries=entries)
            with self.assertRaises(ValueError): server.import_downloaded_lora(str(source))
            self.assertTrue(source.exists())
            self.assertEqual(list(self.workflows.iterdir()), [])

    def test_workflow_json_invalid_and_size_limit_preserve_source(self):
        self.configure()
        source = self.write_json(self.downloads / 'config.json', {'setting': True})
        with self.assertRaises(ValueError): server.import_downloaded_lora(str(source))
        self.assertTrue(source.exists())
        self.write_json(source)
        original = server.MAX_WORKFLOW_JSON_BYTES
        server.MAX_WORKFLOW_JSON_BYTES = 10
        self.addCleanup(setattr, server, 'MAX_WORKFLOW_JSON_BYTES', original)
        with self.assertRaises(ValueError): server.import_downloaded_lora(str(source))
        self.assertTrue(source.exists())

    def test_workflow_wrong_target_and_model_wrong_target_are_rejected(self):
        self.configure()
        source = self.write_json(self.downloads / 'Anima.json')
        with self.assertRaises(ValueError): server.import_downloaded_lora(str(source), {}, str(self.models))
        model = self.create_model(self.downloads, 'model.safetensors', {})
        with self.assertRaises(ValueError): server.import_downloaded_lora(str(model), {}, str(self.workflows))
        self.assertTrue(source.exists())
        self.assertTrue(model.exists())

    def test_workflow_pending_requires_filename_and_ignores_unrelated_download(self):
        self.configure()
        with self.assertRaises(ValueError): server.arm_pending_download_import('', {'model_family_hint': 'workflow'}, '', str(self.downloads))
        server.arm_pending_download_import('', {'model_family_hint': 'workflow'}, 'Anima ꑭ.zip', str(self.downloads))
        self.write_zip('unrelated.zip')
        self.assertIsNone(server.find_pending_download_candidate(server.get_pending_download_import_payload()['pending']))
        expected = self.write_zip('Anima ꑭ.zip')
        found = server.find_pending_download_candidate(server.get_pending_download_import_payload()['pending'])
        self.assertEqual(found['path'], expected)
        old = server.PENDING_IMPORT_SETTLE_SECONDS
        server.PENDING_IMPORT_SETTLE_SECONDS = 0
        self.addCleanup(setattr, server, 'PENDING_IMPORT_SETTLE_SECONDS', old)
        server.process_pending_download_import()
        status = server.get_pending_download_import_payload()
        self.assertIsNone(status['pending'])
        self.assertEqual(status['last_result']['status'], 'complete')
        self.assertEqual(status['last_result']['imported_count'], 2)
        self.assertEqual(status['last_result']['item']['model_family'], 'workflow')

    def test_workflow_broken_zip_returns_clear_error_without_moving_file(self):
        self.configure()
        source = self.downloads / 'broken.zip'
        source.write_bytes(b'not a zip file')
        with self.assertRaisesRegex(ValueError, 'Workflow ZIP'):
            server.import_downloaded_lora(str(source))
        self.assertTrue(source.exists())
        self.assertEqual(list(self.workflows.iterdir()), [])

    def test_workflow_model_download_still_routes_to_models(self):
        self.configure()
        model = self.create_model(self.downloads, 'model.safetensors', {})
        result = server.import_downloaded_lora(str(model), {'model_family_hint': 'checkpoint'})
        self.assertEqual(Path(result['path']).parent, self.models)
        self.assertEqual(result['model_family'], 'checkpoint')
        self.assertEqual(server.sanitize_download_filename('Anima.zip'), 'Anima.zip')
        self.assertEqual(server.sanitize_download_filename('Anima.json'), 'Anima.json')

if __name__ == '__main__': unittest.main()
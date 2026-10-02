import time
import unittest
from pathlib import Path
from unittest.mock import patch
import test_server as base

s = base.server


class DownloadRecoveryTests(unittest.TestCase):
    create_model = base.LoRAManagerServerTests.create_model
    restore_globals = base.LoRAManagerServerTests.restore_globals

    def setUp(self):
        base.LoRAManagerServerTests.setUp(self)
        self.downloads = self.workspace / 'Downloads'
        self.downloads.mkdir()
        self.lora = self.workspace / 'Models' / 'Lora'
        self.checkpoint = self.workspace / 'Models' / 'StableDiffusion'
        self.workflow = self.workspace / 'ComfyUI' / 'workflows'
        s.save_lora_config({'watch_dirs': [str(self.lora), str(self.checkpoint), str(self.workflow)]})
        s.init_lora_db()
        for name, value in [('get_default_downloads_dir', lambda: self.downloads),
                            ('start_background_scan', lambda **kw: None),
                            ('PENDING_IMPORT_SETTLE_SECONDS', 0),
                            ('download_preview_image_for_model', lambda *a: None)]:
            p = patch.object(s, name, value)
            p.start()
            self.addCleanup(p.stop)
        s.DOWNLOAD_RECOVERY_JOB.clear()
        s.DOWNLOAD_RECOVERY_JOB.update(status='idle', results=[])

    def model(self, name='fixture.safetensors', folder=None):
        return self.create_model(folder or self.downloads, name, {'ss_network_module': 'networks.lora'})

    def run_job(self, downloads=None, metadata=None):
        job = s.start_download_recovery({'downloads': downloads or [], 'metadata': metadata or {}})
        deadline = time.monotonic() + 5
        while job['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
            job = s.get_download_recovery_status()
        self.assertEqual(job['status'], 'complete', job)
        return job

    def test_no_wait_scan_moves_models_registers_metadata_and_second_run_is_idempotent(self):
        source = self.model()
        before = source.read_bytes()
        result = self.run_job()
        self.assertEqual(result['imported'], 1, result)
        self.assertFalse(source.exists())
        target = Path(result['results'][0]['path'])
        self.assertEqual(target.read_bytes(), before)
        self.assertIn(str(target), s.load_lora_store())
        self.assertEqual(self.run_job()['imported'], 0)

    def test_custom_download_path_exact_identity_gets_metadata_not_unrelated_page(self):
        a = self.model('renamed.safetensors', self.workspace / 'CustomDownloads')
        b = self.model('other.safetensors')
        url = 'https://civitai.red/api/download/models/123?format=SafeTensor'
        rows = [{'source_path': str(a), 'download_state': 'complete', 'download_urls': [url]}]
        result = self.run_job(rows, {'download_url': url, 'title': 'Correct Name', 'triggers': ['correct-trigger'], 'model_family_hint': 'lora'})
        self.assertEqual(result['imported'], 2)
        store = s.load_lora_store()
        self.assertEqual(store[str(self.lora / a.name)]['display_name'], 'Correct Name')
        self.assertEqual(store[str(self.lora / b.name)]['display_name'], 'other')

    def test_active_download_managed_path_bad_json_zip_and_unknown_bin_are_not_moved(self):
        active = self.model('active.safetensors')
        managed = self.model('managed.safetensors', self.lora)
        (self.downloads / 'config.json').write_text('{"setting": 1}')
        (self.downloads / 'archive.zip').write_bytes(b'not a workflow')
        (self.downloads / 'weights.bin').write_bytes(b'unknown')
        (self.downloads / 'partial.safetensors').write_bytes(b'incomplete')
        rows = [{'source_path': str(active), 'download_state': 'in_progress'},
                {'source_path': str(managed), 'download_state': 'complete'}]
        result = self.run_job(rows)
        self.assertEqual(result['imported'], 0, result)
        self.assertTrue(active.exists())
        self.assertTrue(managed.exists())
        self.assertGreaterEqual(result['skipped'], 6)

    def test_existing_destination_is_preserved(self):
        source = self.model()
        existing = self.model(folder=self.lora)
        before = existing.read_bytes()
        result = self.run_job()
        self.assertEqual(result['imported'], 1)
        self.assertEqual(existing.read_bytes(), before)
        self.assertNotEqual(result['results'][0]['path'], str(existing))
        self.assertFalse(source.exists())

    def test_wrong_family_target_is_skipped(self):
        source = self.model()
        s.save_lora_config({'watch_dirs': [str(self.checkpoint)]})
        result = self.run_job()
        self.assertEqual(result['imported'], 0)
        self.assertTrue(source.exists())
        self.assertIn('保存先', result['results'][0]['reason'])

    def test_workflow_and_checkpoint_go_to_own_folders(self):
        workflow = self.downloads / 'workflow.json'
        workflow.write_text('{"1":{"class_type":"KSampler","inputs":{}}}')
        checkpoint = self.downloads / 'fixture.ckpt'
        checkpoint.write_bytes(b'checkpoint-fixture')
        result = self.run_job()
        self.assertEqual(result['imported'], 2, result)
        self.assertTrue((self.workflow / workflow.name).is_file())
        self.assertTrue((self.checkpoint / checkpoint.name).is_file())

    def test_db_failure_rolls_back_source_and_retry_works(self):
        source = self.model()
        with patch.object(s, 'merge_metadata_changes', side_effect=OSError('fixture DB failure')):
            result = self.run_job()
        self.assertEqual(result['failed'], 1, result)
        self.assertTrue(source.exists())
        self.assertEqual(self.run_job()['imported'], 1)

    def test_matching_wait_is_cleared_but_unrelated_wait_is_preserved(self):
        source = self.model()
        url = 'https://civitai.red/api/download/models/123'
        s.arm_pending_download_import(metadata_payload={'download_url': url, 'model_family_hint': 'lora'}, downloads_dir_text=str(self.downloads))
        self.run_job()
        self.assertIsNotNone(s.PENDING_DOWNLOAD_IMPORT)
        source = self.model('second.safetensors')
        result = self.run_job([{'source_path': str(source), 'download_state': 'complete', 'download_urls': [url]}])
        self.assertEqual(result['imported'], 1)
        self.assertIsNone(s.PENDING_DOWNLOAD_IMPORT)

    def test_running_job_rejects_duplicate_and_completes_once(self):
        source = self.model()
        from threading import Event
        entered, release = Event(), Event()
        real = s.recover_download_file
        def slow(*args):
            entered.set()
            release.wait(3)
            return real(*args)
        with patch.object(s, 'recover_download_file', slow):
            first = s.start_download_recovery({})
            self.assertTrue(entered.wait(2))
            second = s.start_download_recovery({})
            self.assertTrue(second['already_running'])
            self.assertEqual(first['id'], second['id'])
            release.set()
            deadline = time.monotonic() + 3
            while s.get_download_recovery_status()['status'] == 'running' and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertFalse(source.exists())
        self.assertEqual(s.get_download_recovery_status()['imported'], 1)

    def test_changed_file_and_incomplete_tensor_data_are_skipped(self):
        source = self.model()
        st = source.stat()
        source.write_bytes(source.read_bytes() + b'changed')
        result = s.recover_download_file(source, {}, {}, (st.st_size, st.st_mtime_ns))
        self.assertEqual(result['status'], 'skipped')
        import json, struct
        header = json.dumps({'lora_down.weight': {'data_offsets': [0, 1000]}}).encode()
        source.write_bytes(struct.pack('<Q', len(header)) + header)
        result = self.run_job()
        self.assertEqual(result['imported'], 0)
        self.assertTrue(source.exists())

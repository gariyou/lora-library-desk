import hashlib
import unittest
from unittest.mock import patch
import test_download_recovery as recovery

s = recovery.s


class RecoveryIdentityTests(unittest.TestCase):
    def setUp(self):
        recovery.DownloadRecoveryTests.setUp(self)
        preview = patch.object(s, 'resolve_preview_media_url', return_value='')
        preview.start()
        self.addCleanup(preview.stop)
    restore_globals = recovery.DownloadRecoveryTests.restore_globals
    create_model = recovery.DownloadRecoveryTests.create_model
    run_job = recovery.DownloadRecoveryTests.run_job

    def fixture(self):
        source = self.create_model(self.downloads, 'renamed (1).safetensors', {'modelspec.title': 'Unclassified Anima'})
        digest = hashlib.sha256(source.read_bytes()).hexdigest().upper()
        version = {'id': 3292162, 'modelId': 2560956, 'name': 'Nexus Prism IV', 'model': {'name': 'Model Suite', 'type': 'Checkpoint'},
                   'baseModel': 'Anima', 'trainedWords': ['fixture-trigger'],
                   'files': [{'name': 'completely-different-published-name.safetensors', 'hashes': {'SHA256': digest}, 'sizeKB': source.stat().st_size / 1024}]}
        record = {'source_path': str(source), 'download_state': 'complete', 'download_urls': ['https://civitai.red/api/download/models/3292162', 'https://civitai-delivery-worker-prod.fixture.r2.cloudflarestorage.com/model/5039222/unrelated-cdn-name.safetensors']}
        return source, digest, version, record

    def test_history_version_id_and_local_hash_recover_unclassified_renamed_checkpoint(self):
        source, digest, version, record = self.fixture()
        calls = []
        def fetch(url):
            calls.append(url)
            return version
        with patch.object(s, 'fetch_json_url', fetch):
            result = self.run_job([record])
        self.assertEqual(result['imported'], 1, result)
        target = self.checkpoint / source.name
        self.assertTrue(target.is_file())
        self.assertFalse(source.exists())
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest().upper(), digest)
        meta = s.load_lora_store()[str(target)]
        self.assertEqual(meta['display_name'], 'Model Suite / Nexus Prism IV')
        self.assertEqual(meta['triggers'], ['fixture-trigger'])
        self.assertIn('modelVersionId=3292162', meta['source_url'])
        self.assertEqual(calls, ['https://civitai.red/api/v1/model-versions/3292162'])
        self.assertFalse(any(digest.lower() in url.lower() or 'by-hash' in url for url in calls))

    def test_current_model_page_can_find_matching_older_variant_by_hash(self):
        source, digest, version, record = self.fixture()
        wrong = dict(version, id=999, files=[{'name': source.name, 'hashes': {'SHA256': 'A'*64}}])
        model = {'id': 2560956, 'name': 'Model Suite', 'type': 'Checkpoint', 'modelVersions': [wrong, version]}
        with patch.object(s, 'fetch_json_url', return_value=model) as fetch:
            result = self.run_job(metadata={'source_url': 'https://civitai.red/models/2560956/anima-element-7-model-suite'})
        self.assertEqual(result['imported'], 1, result)
        fetch.assert_called_once_with('https://civitai.red/api/v1/models/2560956')
        self.assertIn('modelVersionId=3292162', s.load_lora_store()[str(self.checkpoint / source.name)]['source_url'])

    def test_same_name_wrong_hash_never_moves_or_applies_metadata(self):
        source, digest, version, record = self.fixture()
        version['files'][0].update(name=source.name, hashes={'SHA256': 'A'*64})
        with patch.object(s, 'fetch_json_url', return_value=version):
            result = self.run_job([record])
        self.assertEqual(result['imported'], 0)
        self.assertTrue(source.exists())
        self.assertIn('SHA-256', result['results'][0]['reason'])

    def test_fake_host_and_missing_hash_never_import(self):
        source, digest, version, record = self.fixture()
        record['download_urls'] = ['https://civitai.red.evil.test/api/download/models/3292162']
        with patch.object(s, 'fetch_json_url') as fetch:
            self.assertEqual(self.run_job([record])['imported'], 0)
            fetch.assert_not_called()
        record['download_urls'] = ['https://civitai.red/api/download/models/3292162']
        version['files'][0]['hashes'] = {}
        with patch.object(s, 'fetch_json_url', return_value=version):
            self.assertEqual(self.run_job([record])['imported'], 0)
        self.assertTrue(source.exists())

    def test_lookup_failure_preserves_file_and_retry_succeeds(self):
        source, digest, version, record = self.fixture()
        with patch.object(s, 'fetch_json_url', side_effect=OSError('offline fixture')):
            result = self.run_job([record])
        self.assertEqual(result['imported'], 0)
        self.assertTrue(source.exists())
        with patch.object(s, 'fetch_json_url', return_value=version):
            self.assertEqual(self.run_job([record])['imported'], 1)

    def test_targeted_repair_does_not_scan_other_downloads(self):
        source, digest, version, record = self.fixture()
        other = self.create_model(self.downloads, 'leave-this.safetensors', {'ss_network_module': 'networks.lora'})
        import time
        with patch.object(s, 'fetch_json_url', return_value=version):
            result = s.start_download_recovery({'downloads': [record], 'scan_downloads_dir': False})
            deadline = time.monotonic() + 4
            while result['status'] == 'running' and time.monotonic() < deadline:
                time.sleep(.01)
                result = s.get_download_recovery_status()
        self.assertEqual(result['imported'], 1, result)
        self.assertTrue(other.exists())

import unittest
from pathlib import Path
import test_download_recovery as base

s = base.s

class AutomaticRecoveryTargetTests(unittest.TestCase):
    setUp = base.DownloadRecoveryTests.setUp
    create_model = base.DownloadRecoveryTests.create_model
    restore_globals = base.DownloadRecoveryTests.restore_globals
    model = base.DownloadRecoveryTests.model
    run_job = base.DownloadRecoveryTests.run_job
    def test_matching_wait_keeps_the_explicit_target_directory(self):
        source = self.model()
        alternate = self.workspace / 'Alternate' / 'Lora'
        s.save_lora_config({'watch_dirs': [str(self.lora), str(alternate), str(self.checkpoint)]})
        url = 'https://civitai.red/api/download/models/123'
        s.arm_pending_download_import(metadata_payload={'download_url': url, 'model_family_hint': 'lora',
            'source_url': 'https://civitai.red/models/123', 'title': 'Selected model'},
            target_dir_text=str(alternate), expected_filename=source.name)
        result = self.run_job([{'source_path': str(source), 'download_state': 'complete', 'download_urls': [url]}])
        self.assertEqual(result['imported'], 1)
        self.assertEqual(Path(result['results'][0]['path']).parent, alternate)
        self.assertFalse((self.lora / source.name).exists())
        self.assertIsNone(s.PENDING_DOWNLOAD_IMPORT)


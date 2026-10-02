import unittest
from unittest.mock import patch
from pathlib import Path
import test_server as base
server = base.server

class DownloadCompletionTests(unittest.TestCase):
    setUp = base.LoRAManagerServerTests.setUp
    restore_globals = base.LoRAManagerServerTests.restore_globals
    create_model = base.LoRAManagerServerTests.create_model

    def arm(self):
        self.source = self.create_model(self.workspace / "Downloads", "demix_v1.safetensors", {})
        self.target = self.workspace / "StableDiffusion"
        server.save_lora_config({"watch_dirs": [str(self.target)]})
        self.metadata = {"download_url": "https://s3.us-west-004.backblazeb2.com/civitai-modelfiles/model/11814572/demix.4Htp.safetensors", "source_url": "https://civitai.red/models/2945343/demix", "model_family_hint": "checkpoint"}
        pending = server.arm_pending_download_import(metadata_payload=self.metadata, expected_filename="DeMix.safetensors", downloads_dir_text=str(self.source.parent))["pending"]
        return {"created_epoch": pending["created_epoch"], "source_path": str(self.source), "download_urls": ["https://civitai-delivery-worker-prod.example.r2.cloudflarestorage.com/model/11814572/demix.4Htp.safetensors?signature=fixture"]}

    def test_completed_before_arm_renamed_file_moves_and_registers_once(self):
        payload = self.arm()
        before = self.source.read_bytes()
        self.assertIsNone(server.find_pending_download_candidate(server.PENDING_DOWNLOAD_IMPORT))
        with patch.object(server, "start_background_scan"):
            result = server.complete_browser_pending_download(payload)
            again = server.complete_browser_pending_download(payload)
        self.assertTrue(result["matched"])
        self.assertFalse(again["matched"])
        self.assertFalse(self.source.exists())
        self.assertEqual(Path(result["path"]).read_bytes(), before)
        self.assertEqual(server.scan_lora_library()["items"][0]["source_url"], self.metadata["source_url"])
        self.assertEqual(server.LAST_DOWNLOAD_IMPORT_RESULT["status"], "complete")

    def test_wrong_resource_and_stale_pending_do_not_move(self):
        payload = self.arm()
        wrong = dict(payload, download_urls=[payload["download_urls"][0].replace("demix.4Htp", "another")])
        self.assertFalse(server.complete_browser_pending_download(wrong)["matched"])
        self.assertFalse(server.complete_browser_pending_download(dict(payload, created_epoch=0))["matched"])
        self.assertTrue(self.source.exists())

    def test_untrusted_host_and_file_selection_do_not_match(self):
        self.assertEqual(server.download_resource_key("https://evil.test/model/11814572/demix.4Htp.safetensors"), "")
        a = "https://civitai.red/api/download/models/123?type=Model&format=SafeTensor"
        b = a.replace("civitai.red", "civitai.com") + "&token=fixture"
        self.assertEqual(server.download_resource_key(a), server.download_resource_key(b))
        self.assertNotEqual(server.download_resource_key(a), server.download_resource_key(a.replace("SafeTensor", "PickleTensor")))

    def test_duplicate_completion_threads_only_import_once(self):
        from concurrent.futures import ThreadPoolExecutor
        payload = self.arm()
        with patch.object(server, "start_background_scan"), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(server.complete_browser_pending_download, [payload, payload]))
        self.assertEqual(sum(r["matched"] for r in results), 1)
        self.assertEqual(len(list(self.target.glob("*.safetensors"))), 1)

    def test_large_download_wait_survives_23_minutes(self):
        payload = self.arm()
        server.PENDING_DOWNLOAD_IMPORT["armed_monotonic"] -= 23 * 60
        server.process_pending_download_import()
        self.assertIsNotNone(server.PENDING_DOWNLOAD_IMPORT)
        with patch.object(server, "start_background_scan"):
            self.assertTrue(server.complete_browser_pending_download(payload)["matched"])

    def test_page_only_and_unidentified_automatic_waits_are_rejected(self):
        with self.assertRaises(ValueError):
            server.arm_pending_download_import(metadata_payload={"source_url": "https://civitai.red/models/2935378/elysian", "download_url": "https://civitai.red/models/2935378/elysian"})
        self.assertIsNone(server.PENDING_DOWNLOAD_IMPORT)
        with self.assertRaises(ValueError):
            server.arm_pending_download_import(auto_triggered=True)
        self.assertIsNone(server.PENDING_DOWNLOAD_IMPORT)

    def test_automatic_wait_requires_concrete_url_and_preserves_existing_wait(self):
        self.arm()
        server.clear_pending_download_import()
        result = server.arm_pending_download_import(metadata_payload=self.metadata,
            expected_filename="DeMix.safetensors", auto_triggered=True)
        self.assertTrue(result["pending"]["auto_triggered"])
        epoch = result["pending"]["created_epoch"]
        with self.assertRaises(ValueError):
            server.arm_pending_download_import(metadata_payload=dict(self.metadata,
                download_url="https://civitai.red/api/download/models/999"), auto_triggered=True)
        self.assertEqual(server.PENDING_DOWNLOAD_IMPORT["created_epoch"], epoch)

    def test_same_filename_is_not_enough_without_download_identity(self):
        self.arm()
        server.PENDING_DOWNLOAD_IMPORT["expected_filename"] = "unrelated.safetensors"
        self.create_model(self.source.parent, "unrelated.safetensors", {})
        with patch.object(server, "import_downloaded_lora") as move:
            server.process_pending_download_import()
            move.assert_not_called()
        self.assertIsNotNone(server.PENDING_DOWNLOAD_IMPORT)

    def test_b2_redirect_matches_selected_s3_asset_and_imports(self):
        payload = self.arm()
        payload["download_urls"] = ["https://civitai.red/api/download/models/3335061", "https://b2.civitai.com/file/civitai-modelfiles/model/11814572/demix.4Htp.safetensors"]
        with patch.object(server, "start_background_scan"):
            result = server.complete_browser_pending_download(payload)
        self.assertTrue(result["matched"])
        self.assertFalse(self.source.exists())

    def test_delivery_aliases_share_only_exact_asset_identity(self):
        urls = [
            "https://s3.us-west-004.backblazeb2.com/civitai-modelfiles/model/1533708/bodyWritingAnimaV1.KklA.safetensors",
            "https://b2.civitai.com/file/civitai-modelfiles/model/1533708/bodyWritingAnimaV1.KklA.safetensors",
            "https://civitai-delivery-worker-prod.example.r2.cloudflarestorage.com/model/1533708/bodyWritingAnimaV1.KklA.safetensors?signature=fixture",
        ]
        expected = server.download_resource_key(urls[0])
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(server.download_resource_key(url), expected)
                self.assertNotEqual(server.download_resource_key(url.replace("KklA", "different")), expected)
        for url in [urls[1].replace("b2.civitai.com", "b2.civitai.com.evil.test"), urls[1].replace("civitai-modelfiles", "another-bucket")]:
            self.assertEqual(server.download_resource_key(url), "")

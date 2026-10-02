import json
import sqlite3
import threading
import unittest
import zipfile
import http.client
from unittest.mock import patch
from pathlib import Path
import test_server as base
import test_download_completion as completion
server = base.server

class AuditTests(unittest.TestCase):
    setUp = base.LoRAManagerServerTests.setUp
    restore_globals = base.LoRAManagerServerTests.restore_globals
    create_model = base.LoRAManagerServerTests.create_model
    arm = completion.DownloadCompletionTests.arm

    def test_wait_survives_server_restart_and_completes(self):
        payload = self.arm()
        server.PENDING_DOWNLOAD_IMPORT = None
        server.LAST_DOWNLOAD_IMPORT_RESULT.clear()
        server.restore_download_state()
        self.assertEqual(server.PENDING_DOWNLOAD_IMPORT["created_epoch"], payload["created_epoch"])
        with patch.object(server, "start_background_scan"):
            self.assertTrue(server.complete_browser_pending_download(payload)["matched"])
        server.PENDING_DOWNLOAD_IMPORT = None
        server.LAST_DOWNLOAD_IMPORT_RESULT.clear()
        server.restore_download_state()
        self.assertIsNone(server.PENDING_DOWNLOAD_IMPORT)
        self.assertEqual(server.LAST_DOWNLOAD_IMPORT_RESULT["status"], "complete")

    def test_repeated_arm_is_idempotent_and_other_wait_is_rejected(self):
        payload = self.arm()
        same = server.arm_pending_download_import(metadata_payload=self.metadata, expected_filename="DeMix.safetensors", downloads_dir_text=str(self.source.parent))
        self.assertEqual(same["pending"]["created_epoch"], payload["created_epoch"])
        with self.assertRaisesRegex(ValueError, "待機中"):
            server.arm_pending_download_import(metadata_payload=self.metadata, expected_filename="Another.safetensors")
        self.assertEqual(server.PENDING_DOWNLOAD_IMPORT["created_epoch"], payload["created_epoch"])

    def test_cancel_requires_current_id_and_preserves_source(self):
        payload = self.arm()
        self.assertFalse(server.cancel_pending_download(0)["cancelled"])
        self.assertTrue(server.cancel_pending_download(payload["created_epoch"])["cancelled"])
        server.restore_download_state()
        self.assertIsNone(server.PENDING_DOWNLOAD_IMPORT)
        self.assertEqual(server.LAST_DOWNLOAD_IMPORT_RESULT["status"], "cancelled")
        self.assertTrue(self.source.exists())

    def test_expired_wait_cannot_import_even_before_watcher_polls(self):
        payload = self.arm()
        server.PENDING_DOWNLOAD_IMPORT["armed_monotonic"] -= 25 * 60 * 60
        self.assertFalse(server.complete_browser_pending_download(payload)["matched"])
        self.assertEqual(server.LAST_DOWNLOAD_IMPORT_RESULT["status"], "error")
        self.assertTrue(self.source.exists())

    def test_interrupted_download_unlocks_without_moving(self):
        payload = self.arm()
        result = server.complete_browser_pending_download(dict(payload, download_state="interrupted"))
        self.assertEqual(result["status"], "error")
        self.assertIsNone(server.PENDING_DOWNLOAD_IMPORT)
        self.assertTrue(self.source.exists())

    def test_db_failure_rolls_model_back_and_surfaces_error(self):
        payload = self.arm()
        before = self.source.read_bytes()
        with patch.object(server, "merge_metadata_changes", side_effect=sqlite3.OperationalError("fixture DB locked")):
            result = server.complete_browser_pending_download(payload)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(server.PENDING_DOWNLOAD_IMPORT)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(self.target.glob("*.safetensors")), [])

    def test_page_title_is_saved_without_resetting_user_favorite(self):
        self.arm()
        source = self.create_model(self.target, "existing.safetensors", {})
        server.merge_metadata_changes([str(source.resolve())], {"favorite": True})
        result = server.import_downloaded_lora(str(source), {"title": "Readable model name", "author": "Creator"})
        self.assertEqual(result["metadata"]["display_name"], "Readable model name")
        self.assertTrue(result["metadata"]["favorite"])

    def workflow_source(self):
        workflows = self.workspace / "workflows"
        server.save_lora_config({"watch_dirs": [str(workflows)]})
        source = self.workspace / "pack.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("graph.json", json.dumps({"1": {"class_type": "Node", "inputs": {}}}))
        return source, workflows

    def test_workflow_archive_failure_leaves_no_partial_registration(self):
        source, target = self.workflow_source()
        before = source.read_bytes()
        with patch.object(server.shutil, "move", side_effect=OSError("fixture archive error")):
            with self.assertRaises(OSError): server.import_downloaded_lora(str(source), {})
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(list(target.glob("*.json")), [])
        self.assertEqual(server.load_lora_store(), {})

    def test_workflow_db_failure_restores_original_archive(self):
        source, target = self.workflow_source()
        before = source.read_bytes()
        with patch.object(server, "merge_metadata_changes", side_effect=sqlite3.OperationalError("fixture DB failure")):
            with self.assertRaises(sqlite3.Error): server.import_downloaded_lora(str(source), {})
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(list(target.glob("*.json")), [])
        self.assertEqual(list((server.DATA_ROOT / "workflow-archives").iterdir()), [])

    def test_settings_failure_keeps_previous_json(self):
        path = self.workspace / "settings.json"
        server.write_json_file(path, {"old": True})
        with patch.object(server.os, "replace", side_effect=OSError("fixture file locked")):
            with self.assertRaises(OSError): server.write_json_file(path, {"new": True})
        self.assertEqual(json.loads(path.read_text()), {"old": True})
        self.assertEqual(list(self.workspace.glob("*.tmp")), [])

    def test_api_returns_errors_for_invalid_requests_without_mutation(self):
        server.init_lora_db()
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.build_handler(self.workspace))
        worker = threading.Thread(target=httpd.serve_forever, daemon=True);worker.start()
        self.addCleanup(httpd.server_close);self.addCleanup(httpd.shutdown)
        def request(body, origin):
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
            conn.request("POST", "/api/lora/await-download-import", body=body, headers={"Content-Type":"application/json", "Origin":origin})
            response=conn.getresponse();status=response.status;result=json.loads(response.read());conn.close();return status,result
        self.assertEqual(request(b"{}", "https://unrelated.test")[0],403)
        for method in ("GET", "OPTIONS"):
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
            conn.request(method, "/api/lora/pending-download", headers={"Origin":"https://unrelated.test"})
            response = conn.getresponse(); self.assertEqual(response.status, 403)
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin")); response.read(); conn.close()
        self.assertEqual(request(b"\xff", f"chrome-extension://{server.LIBRARY_DESK_EXTENSION_ID}")[0],400)
        body=json.dumps({"metadata":{"source_url":"https://civitai.red/models/1","download_url":"https://civitai.red/models/1"}}).encode()
        self.assertEqual(request(body, f"chrome-extension://{server.LIBRARY_DESK_EXTENSION_ID}")[0],400)
        self.assertIsNone(server.PENDING_DOWNLOAD_IMPORT)

    def test_failed_preview_replacement_preserves_old_image(self):
        model = self.create_model(self.workspace, "preview.safetensors", {})
        old = model.with_suffix(".png"); old.write_bytes(b"original preview")
        with patch.object(server, "fetch_preview_image_bytes", side_effect=OSError("fixture network failure")):
            with self.assertRaises(OSError): server.replace_remote_preview_for_model(model, "https://example.test/new.jpg")
        self.assertEqual(old.read_bytes(), b"original preview")
        with patch.object(server.os, "replace", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError): server.replace_preview_bytes_for_model(model, b"new preview", ".jpg")
        self.assertEqual(old.read_bytes(), b"original preview")
        self.assertFalse(model.with_suffix(".jpg").exists())
        self.assertEqual(list(self.workspace.glob("*.tmp")), [])
        with patch.object(server, "fetch_preview_image_bytes", return_value=(b"new preview", "image/jpeg")):
            updated = server.replace_remote_preview_for_model(model, "https://example.test/new.jpg")
        self.assertEqual(updated.read_bytes(), b"new preview")
        self.assertFalse(old.exists())

    def test_concurrent_metadata_updates_preserve_independent_fields(self):
        from concurrent.futures import ThreadPoolExecutor
        import time
        model = self.create_model(self.workspace, "metadata.safetensors", {})
        key = str(model.resolve())
        server.merge_metadata_changes([key], {"notes": "original", "favorite": False})
        original = server.load_lora_store
        def slow_read(keys=None):
            result = original(keys); time.sleep(0.05); return result
        gate = threading.Barrier(2)
        def update(changes):
            gate.wait(); return server.merge_metadata_changes([key], changes)
        with patch.object(server, "load_lora_store", side_effect=slow_read), ThreadPoolExecutor(2) as pool:
            a = pool.submit(update, {"notes": "changed"}); b = pool.submit(update, {"favorite": True})
            a.result(); b.result()
        saved = server.load_lora_store([key])[key]
        self.assertEqual(saved["notes"], "changed"); self.assertTrue(saved["favorite"])

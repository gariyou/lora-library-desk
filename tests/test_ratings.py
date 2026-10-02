import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ratings_under_test", APP / "server.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)

class RatingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lora-rating-unit-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        s.DATA_ROOT = root
        s.LORA_DB_PATH = root / "test.sqlite3"
        s.LORA_CONFIG_PATH = root / "config.json"
        s.LEGACY_LORA_STORE_PATH = root / "legacy.json"
        s.SCAN_STATE.update(status="idle", revision=0, result=None, config={"watch_dirs": []}, error="")
        self.path = str(root / "Alpha.safetensors")

    def test_existing_database_migrates_without_changing_metadata(self):
        with closing(sqlite3.connect(s.LORA_DB_PATH)) as db:
            db.execute("""CREATE TABLE lora_metadata (
                path TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '',
                favorite INTEGER NOT NULL DEFAULT 0, category TEXT NOT NULL DEFAULT '',
                triggers_json TEXT NOT NULL DEFAULT '[]', tags_json TEXT NOT NULL DEFAULT '[]',
                notes TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
                base_model TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
                strength_min REAL, strength_max REAL, updated_at TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("INSERT INTO lora_metadata(path, display_name, favorite, notes, triggers_json) VALUES(?, ?, ?, ?, ?)",
                       (self.path, "既存の名前", 1, "消してはいけないメモ", '["original trigger"]'))
            db.commit()
        s.init_lora_db()
        s.init_lora_db()
        item = s.load_lora_store()[self.path]
        self.assertEqual(item["rating"], 0)
        self.assertTrue(item["favorite"])
        self.assertEqual(item["display_name"], "既存の名前")
        self.assertEqual(item["notes"], "消してはいけないメモ")
        self.assertEqual(item["triggers"], ["original trigger"])

    def test_rating_range_and_partial_metadata_preservation(self):
        s.init_lora_db()
        s.merge_metadata_changes([self.path], s.sanitize_metadata_input({"favorite": True, "notes": "keep"}))
        for rating in range(6):
            s.merge_metadata_changes([self.path], s.sanitize_metadata_input({"rating": rating}))
            item = s.load_lora_store()[self.path]
            self.assertEqual(item["rating"], rating)
            self.assertTrue(item["favorite"])
            self.assertEqual(item["notes"], "keep")
        s.merge_metadata_changes([self.path], s.sanitize_metadata_input({"notes": "edited"}))
        self.assertEqual(s.load_lora_store()[self.path]["rating"], 5)
        for invalid in [-1, 6, 1.5, True, False, "3", None, [], {}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                s.sanitize_metadata_input({"rating": invalid})
        with closing(sqlite3.connect(s.LORA_DB_PATH)) as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE lora_metadata SET rating=6 WHERE path=?", (self.path,))

    def test_cache_only_changes_after_successful_save(self):
        s.init_lora_db()
        s.SCAN_STATE.update(status="ready", revision=3, result={"items": [{"path": self.path, "rating": 0}]})
        s.merge_metadata_changes([self.path], s.sanitize_metadata_input({"rating": 4}))
        self.assertEqual(s.get_scan_state_payload()["items"][0]["rating"], 4)
        self.assertEqual(s.SCAN_STATE["revision"], 3)
        with patch.object(s, "upsert_metadata_records", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(sqlite3.OperationalError):
                s.merge_metadata_changes([self.path], s.sanitize_metadata_input({"rating": 1}))
        self.assertEqual(s.get_scan_state_payload()["items"][0]["rating"], 4)
        self.assertEqual(s.load_lora_store()[self.path]["rating"], 4)

    def test_rating_saved_during_scan_is_not_replaced_by_stale_result(self):
        s.init_lora_db()
        scanned = threading.Event()
        finish = threading.Event()
        config = {"watch_dirs": []}
        s.SCAN_STATE.update(status="scanning", revision=7, result={"items": []}, config=config)
        def stale_scan(_config):
            scanned.set()
            if not finish.wait(5):
                raise RuntimeError("test scan timed out")
            return {"items": [{"path": self.path, "rating": 0}]}
        with patch.object(s, "scan_lora_library", side_effect=stale_scan):
            worker = threading.Thread(target=s._complete_background_scan, args=(config, 7))
            worker.start()
            try:
                self.assertTrue(scanned.wait(5))
                s.merge_metadata_changes([self.path], s.sanitize_metadata_input({"rating": 5}))
            finally:
                finish.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(s.SCAN_STATE["status"], "ready")
        self.assertEqual(s.get_scan_state_payload()["items"][0]["rating"], 5)


    def test_scan_metadata_read_failure_is_reported(self):
        s.init_lora_db()
        config = {"watch_dirs": []}
        s.SCAN_STATE.update(status="scanning", revision=8, result={"items": []}, config=config)
        with patch.object(s, "scan_lora_library", return_value={"items": []}), \
             patch.object(s, "load_lora_store", side_effect=sqlite3.OperationalError("fixture locked")):
            s._complete_background_scan(config, 8)
        self.assertEqual(s.SCAN_STATE["status"], "error")
        self.assertIn("fixture locked", s.SCAN_STATE["error"])

if __name__ == "__main__":
    unittest.main()


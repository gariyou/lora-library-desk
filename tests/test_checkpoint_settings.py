import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("checkpoint_settings_under_test", APP / "server.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
KEYS = ("recommended_steps", "recommended_sampler", "recommended_scheduler")
SETTINGS = dict(zip(KEYS, ("20〜30 / 高品質なら40", "DPM++ 2M", "Karras / 通常用途")))


class CheckpointSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="checkpoint-settings-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        s.DATA_ROOT = root
        s.LORA_DB_PATH = root / "test.sqlite3"
        s.LORA_CONFIG_PATH = root / "config.json"
        s.LEGACY_LORA_STORE_PATH = root / "legacy.json"
        s.SCAN_STATE.update(status="idle", revision=0, result=None, config={"watch_dirs": []}, error="")
        self.path = str(root / "Checkpoint.safetensors")

    def test_old_database_adds_fields_and_preserves_all_existing_columns(self):
        with closing(sqlite3.connect(s.LORA_DB_PATH)) as db:
            db.execute("""CREATE TABLE lora_metadata (
                path TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '',
                favorite INTEGER NOT NULL DEFAULT 0, rating INTEGER NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT '', triggers_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '', base_model TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '', strength_min REAL, strength_max REAL,
                updated_at TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("INSERT INTO lora_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (self.path, "既存モデル", 1, 5, "style", '["trigger"]', '["tag"]', "原本メモ",
                        "作者", "Anima", "https://example.com/model", 0.5, 1.0, "2026-09-22"))
            before = db.execute("SELECT * FROM lora_metadata").fetchone()
            db.commit()
        s.init_lora_db()
        s.init_lora_db()
        with closing(sqlite3.connect(s.LORA_DB_PATH)) as db:
            after = db.execute("SELECT * FROM lora_metadata").fetchone()
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(after[len(before):], ("", "", "", "{}"))

    def test_save_reload_partial_edit_and_clear(self):
        original = {"notes": "keep", "author": "作者", "base_model": "Anima",
                    "strength_min": 0.5, "strength_max": 1.0, "rating": 4}
        s.merge_metadata_changes([self.path], s.sanitize_metadata_input(original))
        s.merge_metadata_changes([self.path], s.sanitize_metadata_input(SETTINGS))
        loaded = s.load_lora_store()[self.path]
        self.assertEqual({k: loaded[k] for k in KEYS}, SETTINGS)
        self.assertEqual({k: loaded[k] for k in original}, original)
        s.merge_metadata_changes([self.path], s.sanitize_metadata_input({"notes": "changed"}))
        self.assertEqual({k: s.load_lora_store()[self.path][k] for k in KEYS}, SETTINGS)
        s.merge_metadata_changes([self.path], s.sanitize_metadata_input(dict.fromkeys(KEYS, "")))
        self.assertEqual({k: s.load_lora_store()[self.path][k] for k in KEYS}, dict.fromkeys(KEYS, ""))

    def test_edit_during_scan_survives_stale_scan_result_and_failed_save(self):
        s.init_lora_db()
        scanned, finish = threading.Event(), threading.Event()
        config = {"watch_dirs": []}
        s.SCAN_STATE.update(status="scanning", revision=7, result={"items": []}, config=config)
        def stale_scan(_config):
            scanned.set()
            if not finish.wait(5):
                raise RuntimeError("test scan timed out")
            return {"items": [{"path": self.path, **dict.fromkeys(KEYS, "")}]}
        with patch.object(s, "scan_lora_library", side_effect=stale_scan):
            worker = threading.Thread(target=s._complete_background_scan, args=(config, 7))
            worker.start()
            try:
                self.assertTrue(scanned.wait(5))
                s.merge_metadata_changes([self.path], s.sanitize_metadata_input(SETTINGS))
            finally:
                finish.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        item = s.get_scan_state_payload()["items"][0]
        self.assertEqual({k: item[k] for k in KEYS}, SETTINGS)
        self.assertEqual(s.SCAN_STATE["revision"], 7)
        with patch.object(s, "upsert_metadata_records", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(sqlite3.OperationalError):
                s.merge_metadata_changes([self.path], s.sanitize_metadata_input({"recommended_steps": "99"}))
        self.assertEqual(s.get_scan_state_payload()["items"][0]["recommended_steps"], SETTINGS["recommended_steps"])


if __name__ == "__main__":
    unittest.main()

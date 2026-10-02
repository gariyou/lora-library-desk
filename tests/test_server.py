import base64
import importlib.util
import io
import json
import struct
import tempfile
import threading
import time
import unittest
import urllib.request
import zlib
from pathlib import Path
from urllib.parse import quote


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("lora_manager_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


def write_safetensors(path: Path, metadata: dict) -> None:
    header = {"__metadata__": metadata}
    payload = json.dumps(header, ensure_ascii=False).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(payload)) + payload + b"\x00" * 16)


def make_png_with_text_chunks(text_chunks: dict[str, str]) -> bytes:
    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    payload = bytearray(b"\x89PNG\r\n\x1a\n")
    payload.extend(chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)))
    for key, value in text_chunks.items():
        data = key.encode("latin-1") + b"\x00" + value.encode("utf-8")
        payload.extend(chunk(b"tEXt", data))
    payload.extend(chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")))
    payload.extend(chunk(b"IEND", b""))
    return bytes(payload)


def make_exif_user_comment_payload(comment_text: str) -> bytes:
    comment = b"ASCII\x00\x00\x00" + comment_text.encode("utf-8")
    exif_ifd_offset = 8 + 2 + 12 + 4
    comment_offset = exif_ifd_offset + 2 + 12 + 4

    tiff = bytearray()
    tiff.extend(b"II*\x00")
    tiff.extend(struct.pack("<I", 8))
    tiff.extend(struct.pack("<H", 1))
    tiff.extend(struct.pack("<HHI", 0x8769, 4, 1))
    tiff.extend(struct.pack("<I", exif_ifd_offset))
    tiff.extend(struct.pack("<I", 0))
    tiff.extend(struct.pack("<H", 1))
    tiff.extend(struct.pack("<HHI", 0x9286, 7, len(comment)))
    tiff.extend(struct.pack("<I", comment_offset))
    tiff.extend(struct.pack("<I", 0))
    tiff.extend(comment)
    return b"Exif\x00\x00" + bytes(tiff)


def make_jpeg_with_exif(comment_text: str) -> bytes:
    exif_payload = make_exif_user_comment_payload(comment_text)
    segment = b"\xFF\xE1" + struct.pack(">H", len(exif_payload) + 2) + exif_payload
    return b"\xFF\xD8" + segment + b"\xFF\xD9"


def make_webp_with_exif(comment_text: str) -> bytes:
    exif_payload = make_exif_user_comment_payload(comment_text)
    exif_chunk = b"EXIF" + struct.pack("<I", len(exif_payload)) + exif_payload
    if len(exif_payload) % 2:
        exif_chunk += b"\x00"
    riff_size = 4 + len(exif_chunk)
    return b"RIFF" + struct.pack("<I", riff_size) + b"WEBP" + exif_chunk


class LoRAManagerServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        self.workspace = Path(self.tempdir.name) / "LoRA管理"
        self.data_root = self.workspace / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)

        self.original_paths = {
            "APP_ROOT": server.APP_ROOT,
            "DATA_ROOT": server.DATA_ROOT,
            "LORA_CONFIG_PATH": server.LORA_CONFIG_PATH,
            "LORA_DB_PATH": server.LORA_DB_PATH,
            "LEGACY_LORA_STORE_PATH": server.LEGACY_LORA_STORE_PATH,
        }

        server.APP_ROOT = self.workspace
        server.DATA_ROOT = self.data_root
        server.LORA_CONFIG_PATH = self.data_root / "lora-manager-config.json"
        server.LORA_DB_PATH = self.data_root / "lora-manager.sqlite3"
        server.LEGACY_LORA_STORE_PATH = self.data_root / "lora-manager-db.json"

        server.SCAN_STATE = {
            "status": "idle",
            "started_at": "",
            "finished_at": "",
            "error": "",
            "config": {"watch_dirs": []},
            "result": None,
            "revision": 0,
        }
        server.PENDING_DOWNLOAD_IMPORT = None
        server.LAST_DOWNLOAD_IMPORT_RESULT = {"status": "idle", "message": "", "updated_at": ""}
        with server.ENCYCLOPEDIA_EXPORT_QUEUE_LOCK:
            server.ENCYCLOPEDIA_EXPORT_QUEUE.clear()

        self.addCleanup(self.restore_globals)

    def restore_globals(self) -> None:
        for name, value in self.original_paths.items():
            setattr(server, name, value)

    def create_model(self, folder: Path, filename: str, metadata: dict) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        model_path = folder / filename
        write_safetensors(model_path, metadata)
        return model_path

    def test_reads_safetensors_header_metadata(self) -> None:
        model_path = self.create_model(
            self.workspace / "models",
            "hero_style.safetensors",
            {
                "modelspec.title": "Hero Style",
                "modelspec.author": "Alice",
                "ss_base_model_version": "SDXL 1.0",
                "ss_tag_frequency": json.dumps(
                    {
                        "set-a": {"hero pose": 8, "sharp eyes": 4},
                        "set-b": {"hero pose": 3, "warm rimlight": 6},
                    }
                ),
            },
        )

        embedded = server.load_embedded_safetensors_metadata(model_path)

        self.assertEqual(embedded["source_name"], "Hero Style")
        self.assertEqual(embedded["author"], "Alice")
        self.assertEqual(embedded["base_model"], "SDXL 1.0")
        self.assertEqual(embedded["triggers"][:3], ["hero pose", "warm rimlight", "sharp eyes"])

    def test_init_lora_db_migrates_legacy_json_store(self) -> None:
        legacy_path = self.data_root / "lora-manager-db.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "C:/models/legacy.safetensors": {
                        "display_name": "Legacy Entry",
                        "favorite": True,
                        "category": "style",
                        "tags": ["legacy", "imported"],
                        "updated_at": "2026-03-19T00:00:00Z",
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        server.init_lora_db()
        store = server.load_lora_store()

        self.assertIn("C:/models/legacy.safetensors", store)
        self.assertTrue(store["C:/models/legacy.safetensors"]["favorite"])
        self.assertEqual(store["C:/models/legacy.safetensors"]["category"], "style")
        self.assertEqual(store["C:/models/legacy.safetensors"]["tags"], ["legacy", "imported"])

    def test_scan_uses_sqlite_edits_and_original_preview(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "batch_edit.safetensors",
            {
                "modelspec.title": "Batch Edit",
                "modelspec.author": "Header Author",
                "ss_base_model_version": "Pony",
                "ss_tag_frequency": json.dumps({"main": {"silver hair": 5, "blue coat": 2}}),
            },
        )
        (models_dir / "batch_edit.png").write_bytes(b"not-a-real-image-but-enough")

        server.save_lora_config({"watch_dirs": [str(models_dir)]})

        changes = server.sanitize_metadata_input(
            {
                "display_name": "Pinned Name",
                "favorite": True,
                "category": "character",
                "author": "Override Author",
            }
        )
        server.merge_metadata_changes([str(model_path.resolve())], changes)

        payload = server.scan_lora_library()
        self.assertEqual(payload["stats"]["total"], 1)

        item = payload["items"][0]
        self.assertEqual(item["display_name"], "Pinned Name")
        self.assertEqual(item["category"], "character")
        self.assertTrue(item["favorite"])
        self.assertEqual(item["author"], "Override Author")
        self.assertTrue(item["preview_available"])
        self.assertTrue(item["created_at"])
        self.assertIn("header", item["metadata_sources"])
        self.assertIn("library", item["metadata_sources"])
        self.assertIn("/api/lora/preview", item["preview_url"])

    def test_delete_lora_file_removes_file_and_db_row(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "delete_me.safetensors",
            {"modelspec.title": "Delete Me"},
        )

        changes = server.sanitize_metadata_input({"favorite": True, "category": "style"})
        server.merge_metadata_changes([str(model_path.resolve())], changes)

        deleted_path = server.delete_lora_file(model_path)

        self.assertEqual(deleted_path, str(model_path.resolve()))
        self.assertFalse(model_path.exists())
        self.assertNotIn(str(model_path.resolve()), server.load_lora_store())

    def test_scan_reads_metadata_from_same_name_json_sidecar(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "json_meta.safetensors",
            {"modelspec.title": "Header Name"},
        )
        model_path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "modelName": "JSON Name",
                    "baseModel": "Pony",
                    "trainedWords": ["trigger one", "trigger two"],
                    "tags": ["clean", "anime"],
                    "description": "json description",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        server.save_lora_config({"watch_dirs": [str(models_dir)]})
        payload = server.scan_lora_library()
        item = payload["items"][0]

        self.assertEqual(item["display_name"], "json_meta")
        self.assertEqual(item["source_name"], "JSON Name")
        self.assertEqual(item["base_model"], "Pony")
        self.assertEqual(item["triggers"], ["trigger one", "trigger two"])
        self.assertEqual(item["tags"], ["clean", "anime"])
        self.assertIn("json", item["metadata_sources"])

    def test_scan_prefers_filename_for_default_display_name(self) -> None:
        models_dir = self.workspace / "models"
        self.create_model(
            models_dir,
            "preferred_filename.safetensors",
            {"modelspec.title": "Fancy Header Title"},
        )

        server.save_lora_config({"watch_dirs": [str(models_dir)]})
        payload = server.scan_lora_library()
        item = payload["items"][0]

        self.assertEqual(item["display_name"], "preferred_filename")
        self.assertEqual(item["source_name"], "Fancy Header Title")

    def test_replace_preview_image_for_model_overwrites_existing_preview(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "swap_preview.safetensors",
            {"modelspec.title": "Swap Preview"},
        )
        old_preview = model_path.with_suffix(".png")
        old_preview.write_bytes(b"old-preview")

        payload = base64.b64encode(b"new-preview").decode("ascii")
        preview_path = server.replace_preview_image_for_model(
            model_path,
            {"name": "custom-preview.webp", "data_url": f"data:image/webp;base64,{payload}"},
        )

        expected_path = model_path.with_suffix(".webp").resolve()
        self.assertEqual(preview_path, expected_path)
        self.assertFalse(old_preview.exists())
        self.assertTrue(expected_path.exists())
        self.assertEqual(expected_path.read_bytes(), b"new-preview")

    def test_replace_preview_image_for_model_accepts_video_payload(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "swap_preview_video.safetensors",
            {"modelspec.title": "Swap Preview Video"},
        )
        old_preview = model_path.with_suffix(".png")
        old_preview.write_bytes(b"old-preview")

        payload = base64.b64encode(b"video-preview").decode("ascii")
        preview_path = server.replace_preview_image_for_model(
            model_path,
            {"name": "custom-preview.mp4", "data_url": f"data:video/mp4;base64,{payload}"},
        )

        expected_path = model_path.with_suffix(".mp4").resolve()
        self.assertEqual(preview_path, expected_path)
        self.assertFalse(old_preview.exists())
        self.assertTrue(expected_path.exists())
        self.assertEqual(expected_path.read_bytes(), b"video-preview")

    def test_delete_preview_images_for_model_removes_all_preview_variants(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "delete_preview.safetensors",
            {"modelspec.title": "Delete Preview"},
        )
        primary_preview = model_path.with_suffix(".png")
        fallback_preview = model_path.with_name(f"{model_path.stem}.preview.webp")
        primary_preview.write_bytes(b"preview-a")
        fallback_preview.write_bytes(b"preview-b")

        deleted_paths = server.delete_preview_images_for_model(model_path)

        self.assertEqual(
            set(deleted_paths),
            {str(primary_preview.resolve()), str(fallback_preview.resolve())},
        )
        self.assertFalse(primary_preview.exists())
        self.assertFalse(fallback_preview.exists())

    def test_build_lora_item_snapshot_adds_cache_bust_to_preview_url(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "snapshot_preview.safetensors",
            {"modelspec.title": "Snapshot Preview"},
        )
        preview_path = model_path.with_suffix(".png")
        preview_path.write_bytes(b"preview-bytes")
        watch_dirs = [str(models_dir)]
        server.save_lora_config({"watch_dirs": watch_dirs})

        item = server.build_lora_item_snapshot(model_path, watch_dirs, cache_bust="preview-123")

        self.assertTrue(item["preview_available"])
        self.assertEqual(
            item["preview_url"],
            f"/api/lora/preview?path={quote(str(model_path.resolve()), safe='')}&preview={quote(str(preview_path.resolve()), safe='')}&v=preview-123",
        )

    def test_build_lora_item_snapshot_marks_video_preview_kind(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "snapshot_preview_video.safetensors",
            {"modelspec.title": "Snapshot Preview Video"},
        )
        preview_path = model_path.with_suffix(".mp4")
        preview_path.write_bytes(b"preview-video")
        watch_dirs = [str(models_dir)]
        server.save_lora_config({"watch_dirs": watch_dirs})

        item = server.build_lora_item_snapshot(model_path, watch_dirs, cache_bust="preview-video-123")

        self.assertTrue(item["preview_available"])
        self.assertEqual(item["preview_media_kind"], "video")
        self.assertEqual(
            item["preview_url"],
            f"/api/lora/preview?path={quote(str(model_path.resolve()), safe='')}&preview={quote(str(preview_path.resolve()), safe='')}&v=preview-video-123",
        )

    def test_preview_upload_route_returns_item_snapshot(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "preview_route_target.safetensors",
            {"modelspec.title": "Preview Route Target"},
        )
        server.save_lora_config({"watch_dirs": [str(models_dir)]})

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.build_handler(self.workspace / "web"))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join, 1)

        payload = {
            "path": str(model_path.resolve()),
            "file": {
                "name": "thumb.png",
                "data_url": f"data:image/png;base64,{base64.b64encode(b'preview-bytes').decode('ascii')}",
            },
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/lora/preview/upload",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 201)
        finally:
            httpd.shutdown()
            thread.join(timeout=1)

        self.assertTrue(body["ok"])
        self.assertEqual(body["path"], str(model_path.resolve()))
        self.assertTrue(Path(body["preview_path"]).exists())
        self.assertTrue(body["item"]["preview_available"])
        self.assertIn("&v=", body["item"]["preview_url"])

    def test_preview_upload_route_accepts_video_payload(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "preview_route_video_target.safetensors",
            {"modelspec.title": "Preview Route Video Target"},
        )
        server.save_lora_config({"watch_dirs": [str(models_dir)]})

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.build_handler(self.workspace / "web"))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join, 1)

        payload = {
            "path": str(model_path.resolve()),
            "file": {
                "name": "thumb.mp4",
                "data_url": f"data:video/mp4;base64,{base64.b64encode(b'preview-video-bytes').decode('ascii')}",
            },
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/lora/preview/upload",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 201)
        finally:
            httpd.shutdown()

        self.assertTrue(body["ok"])
        self.assertEqual(body["path"], str(model_path.resolve()))
        self.assertTrue(Path(body["preview_path"]).exists())
        self.assertEqual(Path(body["preview_path"]).suffix.lower(), ".mp4")
        self.assertTrue(body["item"]["preview_available"])
        self.assertEqual(body["item"]["preview_media_kind"], "video")
        self.assertIn("&v=", body["item"]["preview_url"])

    def test_preview_upload_route_accepts_remote_video_url(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "preview_route_video_url_target.safetensors",
            {"modelspec.title": "Preview Route Video URL Target"},
        )
        server.save_lora_config({"watch_dirs": [str(models_dir)]})

        original_downloader = server.replace_remote_preview_for_model

        def fake_downloader(model_path_arg: Path, preview_url: str, referer_url: str = "") -> Path:
            self.assertEqual(model_path_arg, model_path.resolve())
            self.assertEqual(preview_url, "https://example.com/videos/preview-url.mp4")
            self.assertEqual(referer_url, "")
            preview_path = model_path_arg.with_suffix(".mp4")
            preview_path.write_bytes(b"preview-video-url-bytes")
            return preview_path

        server.replace_remote_preview_for_model = fake_downloader
        self.addCleanup(setattr, server, "replace_remote_preview_for_model", original_downloader)

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.build_handler(self.workspace / "web"))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join, 1)

        payload = {
            "path": str(model_path.resolve()),
            "url": "https://example.com/videos/preview-url.mp4",
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/lora/preview/upload",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 201)
        finally:
            httpd.shutdown()

        self.assertTrue(body["ok"])
        self.assertEqual(body["path"], str(model_path.resolve()))
        self.assertTrue(Path(body["preview_path"]).exists())
        self.assertEqual(Path(body["preview_path"]).suffix.lower(), ".mp4")
        self.assertEqual(body["item"]["preview_media_kind"], "video")

    def test_save_reference_images_and_scan_count(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "with_references.safetensors",
            {"modelspec.title": "With References"},
        )

        image_data = base64.b64encode(b"fake-image-bytes").decode("ascii")
        items = server.save_reference_images_for_model(
            model_path,
            [
                {"name": "sample-a.png", "data_url": f"data:image/png;base64,{image_data}"},
                {"name": "sample-b.webp", "data_url": f"data:image/webp;base64,{image_data}"},
            ],
        )

        self.assertEqual(len(items), 2)
        self.assertTrue((models_dir / "with_references.references" / "sample-a.png").exists())
        self.assertTrue((models_dir / "with_references.references" / "sample-b.webp").exists())
        self.assertIn("/api/lora/reference-image", items[0]["url"])

        server.save_lora_config({"watch_dirs": [str(models_dir)]})
        payload = server.scan_lora_library()
        self.assertEqual(payload["items"][0]["reference_count"], 2)

    def test_save_reference_video_and_scan_count(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "with_video_reference.safetensors",
            {"modelspec.title": "With Video Reference"},
        )

        video_data = base64.b64encode(b"fake-video-bytes").decode("ascii")
        items = server.save_reference_images_for_model(
            model_path,
            [
                {
                    "name": "sample-loop.mp4",
                    "data_url": f"data:video/mp4;base64,{video_data}",
                }
            ],
        )

        self.assertEqual(len(items), 1)
        self.assertTrue((models_dir / "with_video_reference.references" / "sample-loop.mp4").exists())
        self.assertEqual(items[0]["media_kind"], "video")
        self.assertTrue(items[0]["is_video"])
        self.assertFalse(items[0]["has_prompt"])

        server.save_lora_config({"watch_dirs": [str(models_dir)]})
        payload = server.scan_lora_library()
        self.assertEqual(payload["items"][0]["reference_count"], 1)

    def test_save_reference_images_can_return_summary_only(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "with_reference_summary.safetensors",
            {"modelspec.title": "With Reference Summary"},
        )

        image_data = base64.b64encode(b"fake-image-bytes").decode("ascii")
        result = server.save_reference_images_for_model(
            model_path,
            [
                {"name": "sample-a.png", "data_url": f"data:image/png;base64,{image_data}"},
                {"name": "sample-b.webp", "data_url": f"data:image/webp;base64,{image_data}"},
            ],
            include_items=False,
        )

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["items"], [])
        self.assertTrue((models_dir / "with_reference_summary.references" / "sample-a.png").exists())
        self.assertTrue((models_dir / "with_reference_summary.references" / "sample-b.webp").exists())

    def test_reference_upload_extracts_png_prompt_metadata(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "with_prompt_reference.safetensors",
            {"modelspec.title": "With Prompt Reference"},
        )

        png_bytes = make_png_with_text_chunks(
            {
                "parameters": "masterpiece, 1girl, blue eyes\nNegative prompt: lowres, blurry\nSteps: 28, Sampler: Euler a, CFG scale: 7, Seed: 1234"
            }
        )
        payload = base64.b64encode(png_bytes).decode("ascii")

        items = server.save_reference_images_for_model(
            model_path,
            [
                {
                    "name": "prompt_image.png",
                    "data_url": f"data:image/png;base64,{payload}",
                }
            ],
        )

        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["has_prompt"])
        self.assertEqual(items[0]["prompt"], "masterpiece, 1girl, blue eyes")
        self.assertEqual(items[0]["negative_prompt"], "lowres, blurry")
        self.assertEqual(items[0]["steps"], "28")
        self.assertEqual(items[0]["sampler"], "Euler a")
        self.assertEqual(items[0]["cfg_scale"], "7")
        self.assertEqual(items[0]["seed"], "1234")
        self.assertIn("masterpiece", items[0]["prompt_preview"])

        sidecar = models_dir / "with_prompt_reference.references" / "prompt_image.png.refmeta.json"
        self.assertTrue(sidecar.exists())
        sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(sidecar_payload["prompt"], "masterpiece, 1girl, blue eyes")
        self.assertEqual(sidecar_payload["steps"], "28")
        self.assertEqual(sidecar_payload["sampler"], "Euler a")
        self.assertEqual(sidecar_payload["cfg_scale"], "7")
        self.assertEqual(sidecar_payload["seed"], "1234")

    def test_reference_upload_auto_queues_prompt_encyclopedia_export(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "with_auto_export_reference.safetensors",
            {"modelspec.title": "With Auto Export Reference"},
        )

        png_bytes = make_png_with_text_chunks(
            {
                "parameters": "masterpiece, auto export\nNegative prompt: lowres\nSteps: 22, Sampler: Euler"
            }
        )
        payload = base64.b64encode(png_bytes).decode("ascii")

        server.save_reference_images_for_model(
            model_path,
            [
                {
                    "name": "auto_export.png",
                    "data_url": f"data:image/png;base64,{payload}",
                }
            ],
        )

        with server.ENCYCLOPEDIA_EXPORT_QUEUE_LOCK:
            queued = list(server.ENCYCLOPEDIA_EXPORT_QUEUE)

        self.assertEqual(len(queued), 1)
        self.assertTrue(queued[0]["path"].endswith("auto_export.png"))
        self.assertEqual(queued[0]["prompt"], "masterpiece, auto export")
        self.assertEqual(queued[0]["negative_prompt"], "lowres")

    def test_extract_reference_prompt_metadata_from_jpeg_exif(self) -> None:
        jpeg_bytes = make_jpeg_with_exif(
            "masterpiece, 1girl, sunlight\nNegative prompt: lowres, blurry\nSteps: 30, Sampler: Euler, CFG scale: 7"
        )

        metadata = server.extract_reference_prompt_metadata_from_bytes(jpeg_bytes, "sample.jpg", "image/jpeg")

        self.assertTrue(metadata["has_prompt"])
        self.assertEqual(metadata["metadata_source"], "exif.user_comment")
        self.assertEqual(metadata["prompt"], "masterpiece, 1girl, sunlight")
        self.assertEqual(metadata["negative_prompt"], "lowres, blurry")
        self.assertEqual(metadata["steps"], "30")
        self.assertEqual(metadata["sampler"], "Euler")
        self.assertEqual(metadata["cfg_scale"], "7")

    def test_reference_upload_extracts_webp_exif_prompt_metadata(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "with_webp_prompt_reference.safetensors",
            {"modelspec.title": "With WebP Prompt Reference"},
        )

        webp_bytes = make_webp_with_exif(
            "best quality, 1girl, night city\nNegative prompt: extra fingers\nSteps: 24, Sampler: DPM++ 2M"
        )
        payload = base64.b64encode(webp_bytes).decode("ascii")

        items = server.save_reference_images_for_model(
            model_path,
            [
                {
                    "name": "prompt_image.webp",
                    "data_url": f"data:image/webp;base64,{payload}",
                }
            ],
        )

        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["has_prompt"])
        self.assertEqual(items[0]["metadata_source"], "exif.user_comment")
        self.assertEqual(items[0]["prompt"], "best quality, 1girl, night city")
        self.assertEqual(items[0]["negative_prompt"], "extra fingers")
        self.assertEqual(items[0]["steps"], "24")
        self.assertEqual(items[0]["sampler"], "DPM++ 2M")

        sidecar = models_dir / "with_webp_prompt_reference.references" / "prompt_image.webp.refmeta.json"
        self.assertTrue(sidecar.exists())

    def test_extract_reference_prompt_metadata_repairs_resource_stack_mojibake(self) -> None:
        resource_stack_json = json.dumps(
            {
                "resource-stack": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {
                        "ckpt_name": "urn:air:sdxl:checkpoint:civitai:1307857@17238998",
                    },
                },
                "resource-stack-1": {
                    "class_type": "LoraLoader",
                    "inputs": {
                        "lora_name": "urn:air:sdxl:lora:civitai:2433186@2735833",
                    },
                },
            },
            ensure_ascii=False,
        )
        mojibake_text = resource_stack_json.encode("utf-16-le").decode("utf-16-be")

        metadata = server.extract_reference_prompt_metadata_from_text(mojibake_text, "xmp.packet")

        self.assertFalse(metadata["has_prompt"])
        self.assertEqual(len(metadata["resources_used"]), 2)
        self.assertEqual(metadata["resources_used"][0]["model_id"], "1307857")
        self.assertEqual(metadata["resources_used"][0]["model_version_id"], "17238998")
        self.assertEqual(metadata["resources_used"][1]["model_id"], "2433186")
        self.assertEqual(metadata["resources_used"][1]["model_version_id"], "2735833")
        self.assertEqual(metadata["prompt_json"], "")
        self.assertIn("Resources", metadata["prompt_preview"])

    def test_extract_reference_prompt_metadata_from_utf16_xmp_resource_stack(self) -> None:
        resource_stack_json = json.dumps(
            {
                "resource-stack": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {
                        "ckpt_name": "urn:air:sdxl:checkpoint:civitai:1307857@17238998",
                    },
                }
            },
            ensure_ascii=False,
        )
        xmp_bytes = resource_stack_json.encode("utf-16-le")

        metadata = server.extract_reference_prompt_metadata_from_xmp_bytes(xmp_bytes, "xmp.packet")

        self.assertFalse(metadata["has_prompt"])
        self.assertEqual(len(metadata["resources_used"]), 1)
        self.assertEqual(metadata["resources_used"][0]["model_id"], "1307857")
        self.assertEqual(metadata["resources_used"][0]["type"], "Checkpoint")
        self.assertEqual(metadata["resources_used"][0]["base_model"], "SDXL")

    def test_normalize_reference_prompt_metadata_extracts_workflow_prompt_and_resource_strengths(self) -> None:
        workflow_json = json.dumps(
            {
                "resource-stack": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {
                        "ckpt_name": "urn:air:sdxl:checkpoint:civitai:1307857@1723898",
                    },
                },
                "resource-stack-1": {
                    "class_type": "LoraLoader",
                    "inputs": {
                        "lora_name": "urn:air:sdxl:lora:civitai:660117@1139834",
                    },
                },
                "20": {
                    "class_type": "UpscaleModelLoader",
                    "inputs": {
                        "model_name": "urn:air:other:upscaler:civitai:147759@164821",
                    },
                },
                "extraMetadata": json.dumps(
                    {
                        "prompt": "best quality, 1girl",
                        "negativePrompt": "lowres, blurry",
                        "steps": 23,
                        "cfgScale": 4,
                        "sampler": "euler_ancestral",
                        "seed": 1718570207,
                        "resources": [
                            {"modelVersionId": 1723898, "strength": 1},
                            {"modelVersionId": 1139834, "strength": 0.85},
                        ],
                    }
                ),
            },
            ensure_ascii=False,
        )

        metadata = server.normalize_reference_prompt_metadata({"workflow_json": workflow_json})

        self.assertTrue(metadata["has_prompt"])
        self.assertEqual(metadata["prompt"], "best quality, 1girl")
        self.assertEqual(metadata["negative_prompt"], "lowres, blurry")
        self.assertEqual(metadata["steps"], "23")
        self.assertEqual(metadata["cfg_scale"], "4")
        self.assertEqual(metadata["sampler"], "euler_ancestral")
        self.assertEqual(metadata["seed"], "1718570207")
        self.assertEqual(len(metadata["resources_used"]), 3)
        self.assertEqual(metadata["resources_used"][0]["name"], "Checkpoint 1307857")
        self.assertEqual(metadata["resources_used"][0]["strength"], "1")
        self.assertEqual(metadata["resources_used"][1]["name"], "Lora 660117")
        self.assertEqual(metadata["resources_used"][1]["strength"], "0.85")
        self.assertEqual(metadata["resources_used"][2]["type"], "Upscaler")
        self.assertEqual(metadata["resources_used"][2]["name"], "Upscaler 147759")

    def test_delete_reference_image_removes_image_and_sidecar(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "delete_reference.safetensors",
            {"modelspec.title": "Delete Reference"},
        )

        png_bytes = make_png_with_text_chunks({"parameters": "1girl\nSteps: 20"})
        payload = base64.b64encode(png_bytes).decode("ascii")
        items = server.save_reference_images_for_model(
            model_path,
            [
                {
                    "name": "delete_me.png",
                    "data_url": f"data:image/png;base64,{payload}",
                }
            ],
        )

        reference_path = Path(items[0]["path"])
        sidecar_path = reference_path.with_name(f"{reference_path.name}{server.REFERENCE_METADATA_SUFFIX}")

        deleted_path = server.delete_reference_image_for_model(model_path, reference_path)

        self.assertEqual(deleted_path, str(reference_path.resolve()))
        self.assertFalse(reference_path.exists())
        self.assertFalse(sidecar_path.exists())

    def test_browser_import_can_be_stored_and_applied(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "import_target.safetensors",
            {"modelspec.title": "Import Target"},
        )

        record = server.store_browser_import(
            {
                "source_url": "https://example.com/models/123",
                "title": "Example Model",
                "description": "Recommended strength: 0.8",
                "author": "Browser Author",
                "base_model": "SDXL",
                "triggers": ["alpha", "beta"],
                "tags": ["clean", "stylized"],
            }
        )

        imports = server.load_browser_imports()
        self.assertEqual(imports[0]["id"], record["id"])

        updated = server.apply_browser_import_to_path(record["id"], str(model_path.resolve()))
        self.assertEqual(updated["author"], "Browser Author")
        self.assertEqual(updated["base_model"], "SDXL")
        self.assertEqual(updated["source_url"], "https://example.com/models/123")
        self.assertEqual(updated["triggers"], ["alpha", "beta"])
        self.assertEqual(updated["tags"], ["clean", "stylized"])
        self.assertEqual(updated["notes"], "取得メモ\nRecommended strength: 0.8")

    def test_append_import_description_to_notes_uses_civitai_label_once(self) -> None:
        notes = server.append_import_description_to_notes(
            "",
            "Trigger word: m1ur4",
            "https://civitai.com/models/2433186/miura-takehiro-style",
        )

        self.assertEqual(notes, "Civitaiメモ\nTrigger word: m1ur4")
        self.assertEqual(
            server.append_import_description_to_notes(
                "",
                "Trigger word: m1ur4",
                "https://civitai.red/models/2433186/miura-takehiro-style",
            ),
            "Civitaiメモ\nTrigger word: m1ur4",
        )
        self.assertEqual(
            server.append_import_description_to_notes(
                notes,
                "Trigger word: m1ur4",
                "https://civitai.com/models/2433186/miura-takehiro-style",
            ),
            notes,
        )

    def test_browser_import_apply_saves_preview_when_available(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "import_preview.safetensors",
            {"modelspec.title": "Import Preview"},
        )

        record = server.store_browser_import(
            {
                "source_url": "https://example.com/models/321",
                "title": "Preview Model",
                "preview_image_url": "https://example.com/images/import_preview.webp",
            }
        )

        original_downloader = server.download_preview_image_for_model

        def fake_downloader(model_path: Path, preview_url: str, referer_url: str = "") -> Path:
            preview_path = model_path.with_suffix(".webp")
            preview_path.write_bytes(b"preview-bytes")
            return preview_path

        server.download_preview_image_for_model = fake_downloader
        self.addCleanup(setattr, server, "download_preview_image_for_model", original_downloader)

        server.apply_browser_import_to_path(record["id"], str(model_path.resolve()))

        preview_path = model_path.with_suffix(".webp")
        self.assertTrue(preview_path.exists())

    def test_browser_import_apply_saves_video_preview_when_available(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "import_preview_video.safetensors",
            {"modelspec.title": "Import Preview Video"},
        )

        record = server.store_browser_import(
            {
                "source_url": "https://example.com/models/654",
                "title": "Preview Video Model",
                "preview_media_kind": "video",
                "preview_video_url": "https://example.com/videos/import_preview.mp4",
            }
        )

        original_downloader = server.download_preview_image_for_model

        def fake_downloader(model_path: Path, preview_url: str, referer_url: str = "") -> Path:
            self.assertEqual(preview_url, "https://example.com/videos/import_preview.mp4")
            preview_path = model_path.with_suffix(".mp4")
            preview_path.write_bytes(b"preview-video-bytes")
            return preview_path

        server.download_preview_image_for_model = fake_downloader
        self.addCleanup(setattr, server, "download_preview_image_for_model", original_downloader)

        server.apply_browser_import_to_path(record["id"], str(model_path.resolve()))

        preview_path = model_path.with_suffix(".mp4")
        self.assertTrue(preview_path.exists())

    def test_browser_import_apply_fetches_civitai_preview_from_source_url(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "import_civitai_preview.safetensors",
            {"modelspec.title": "Import Civitai Preview"},
        )

        record = server.store_browser_import(
            {
                "source_url": "https://civitai.com/models/1543289/example-model",
                "title": "Preview Model",
            }
        )

        original_resolver = server.fetch_civitai_preview_image_url
        original_downloader = server.download_preview_image_for_model

        server.fetch_civitai_preview_image_url = lambda source_url: "https://example.com/images/from-civitai.webp"

        def fake_downloader(model_path: Path, preview_url: str, referer_url: str = "") -> Path:
            preview_path = model_path.with_suffix(".webp")
            preview_path.write_bytes(b"preview-bytes")
            return preview_path

        server.download_preview_image_for_model = fake_downloader
        self.addCleanup(setattr, server, "fetch_civitai_preview_image_url", original_resolver)
        self.addCleanup(setattr, server, "download_preview_image_for_model", original_downloader)

        server.apply_browser_import_to_path(record["id"], str(model_path.resolve()))

        preview_path = model_path.with_suffix(".webp")
        self.assertTrue(preview_path.exists())

    def test_browser_import_can_be_consumed_after_metadata_apply(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "import_consume.safetensors",
            {"modelspec.title": "Import Consume"},
        )

        record = server.store_browser_import(
            {
                "source_url": "https://example.com/models/999",
                "title": "Consume Model",
                "author": "Consume Author",
            }
        )

        updated = server.apply_browser_import_to_path(record["id"], str(model_path.resolve()), consume=True)

        self.assertEqual(updated["author"], "Consume Author")
        self.assertIsNone(server.get_browser_import(record["id"]))

    def test_browser_import_reference_download_saves_prompt_metadata(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "browser_reference.safetensors",
            {"modelspec.title": "Browser Reference"},
        )

        record = server.store_browser_import(
            {
                "source_url": "https://civitai.com/images/51061207",
                "title": "Takeda Sample",
                "preview_image_url": "https://example.com/images/51061207.jpg",
                "prompt": "masterpiece, 1girl, detailed eyes",
                "negative_prompt": "lowres, blurry",
                "prompt_preview": "masterpiece, 1girl, detailed eyes",
                "steps": "32",
                "sampler": "Euler a",
                "cfg_scale": "6.5",
                "seed": "987654321",
                "resources_used": [
                    {
                        "model_id": "1543289",
                        "model_version_id": "2782652",
                        "name": "Takeda Hiromitsu - Art Style",
                        "type": "LoRA",
                        "version_name": "v2.0",
                        "strength": "0.8",
                        "base_model": "Illustrious",
                    }
                ],
            }
        )

        original_fetch = server.fetch_reference_media_bytes
        original_civitai_media = server.fetch_civitai_image_media_url

        def fake_fetch(preview_url: str, referer_url: str = "") -> tuple[bytes, str]:
            self.assertEqual(preview_url, "https://example.com/images/51061207.jpg")
            self.assertEqual(referer_url, "https://civitai.com/images/51061207")
            return b"jpeg-like-bytes", "image/jpeg"

        server.fetch_reference_media_bytes = fake_fetch
        server.fetch_civitai_image_media_url = lambda source_url, fallback_url="": ""
        self.addCleanup(setattr, server, "fetch_reference_media_bytes", original_fetch)
        self.addCleanup(setattr, server, "fetch_civitai_image_media_url", original_civitai_media)

        items = server.add_browser_import_reference_to_path(record["id"], str(model_path.resolve()))

        reference_path = models_dir / "browser_reference.references" / "civitai-image-51061207.jpg"
        sidecar_path = reference_path.with_name(f"{reference_path.name}{server.REFERENCE_METADATA_SUFFIX}")

        self.assertTrue(reference_path.exists())
        self.assertTrue(sidecar_path.exists())
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["has_prompt"])
        self.assertEqual(items[0]["prompt"], "masterpiece, 1girl, detailed eyes")
        self.assertEqual(items[0]["negative_prompt"], "lowres, blurry")
        self.assertEqual(items[0]["metadata_source"], "civitai.image.generation")
        self.assertEqual(items[0]["source_url"], "https://civitai.com/images/51061207")
        self.assertEqual(items[0]["source_name"], "Takeda Sample")
        self.assertEqual(items[0]["steps"], "32")
        self.assertEqual(items[0]["sampler"], "Euler a")
        self.assertEqual(items[0]["cfg_scale"], "6.5")
        self.assertEqual(items[0]["seed"], "987654321")
        self.assertEqual(len(items[0]["resources_used"]), 1)
        self.assertEqual(items[0]["resources_used"][0]["name"], "Takeda Hiromitsu - Art Style")
        self.assertEqual(
            items[0]["resources_used"][0]["url"],
            "https://civitai.red/models/1543289?modelVersionId=2782652",
        )

        sidecar_payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(sidecar_payload["prompt"], "masterpiece, 1girl, detailed eyes")
        self.assertEqual(sidecar_payload["negative_prompt"], "lowres, blurry")
        self.assertEqual(sidecar_payload["source_url"], "https://civitai.com/images/51061207")
        self.assertEqual(sidecar_payload["source_name"], "Takeda Sample")
        self.assertEqual(sidecar_payload["steps"], "32")
        self.assertEqual(sidecar_payload["sampler"], "Euler a")
        self.assertEqual(sidecar_payload["cfg_scale"], "6.5")
        self.assertEqual(sidecar_payload["seed"], "987654321")
        self.assertEqual(len(sidecar_payload["resources_used"]), 1)
        self.assertEqual(sidecar_payload["resources_used"][0]["version_name"], "v2.0")

    def test_browser_import_reference_can_be_consumed_after_apply(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "browser_reference_consume.safetensors",
            {"modelspec.title": "Browser Reference Consume"},
        )

        record = server.store_browser_import(
            {
                "source_url": "https://civitai.com/images/51061208",
                "title": "Consume Sample",
                "preview_image_url": "https://example.com/images/51061208.jpg",
                "prompt": "1girl, cinematic",
            }
        )

        original_fetch = server.fetch_reference_media_bytes
        original_civitai_media = server.fetch_civitai_image_media_url

        def fake_fetch(preview_url: str, referer_url: str = "") -> tuple[bytes, str]:
            self.assertEqual(preview_url, "https://example.com/images/51061208.jpg")
            return b"jpeg-like-bytes", "image/jpeg"

        server.fetch_reference_media_bytes = fake_fetch
        server.fetch_civitai_image_media_url = lambda source_url, fallback_url="": ""
        self.addCleanup(setattr, server, "fetch_reference_media_bytes", original_fetch)
        self.addCleanup(setattr, server, "fetch_civitai_image_media_url", original_civitai_media)

        items = server.add_browser_import_reference_to_path(record["id"], str(model_path.resolve()), consume=True)

        self.assertEqual(len(items), 1)
        self.assertIsNone(server.get_browser_import(record["id"]))

    def test_browser_import_reference_download_saves_video_metadata(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "browser_reference_video.safetensors",
            {"modelspec.title": "Browser Reference Video"},
        )

        record = server.store_browser_import(
            {
                "source_url": "https://civitai.com/images/51061209",
                "title": "Animated Sample",
                "preview_media_kind": "video",
                "preview_media_url": "https://example.com/videos/51061209.mp4",
                "preview_video_url": "https://example.com/videos/51061209.mp4",
                "prompt": "masterpiece, animated sample",
                "negative_prompt": "lowres",
                "prompt_preview": "masterpiece, animated sample",
                "steps": "20",
                "sampler": "Euler",
                "cfg_scale": "5.5",
            }
        )

        original_fetch = server.fetch_reference_media_bytes
        original_civitai_media = server.fetch_civitai_image_media_url

        def fake_fetch(preview_url: str, referer_url: str = "") -> tuple[bytes, str]:
            self.assertEqual(preview_url, "https://example.com/videos/51061209.mp4")
            self.assertEqual(referer_url, "https://civitai.com/images/51061209")
            return b"mp4-like-bytes", "video/mp4"

        def fail_civitai_fetch(source_url: str, fallback_url: str = "") -> str:
            raise AssertionError("Explicit preview_video_url should be used before server-side Civitai asset lookup")

        server.fetch_reference_media_bytes = fake_fetch
        server.fetch_civitai_image_media_url = fail_civitai_fetch
        self.addCleanup(setattr, server, "fetch_reference_media_bytes", original_fetch)
        self.addCleanup(setattr, server, "fetch_civitai_image_media_url", original_civitai_media)

        items = server.add_browser_import_reference_to_path(record["id"], str(model_path.resolve()))

        reference_path = models_dir / "browser_reference_video.references" / "civitai-image-51061209.mp4"
        sidecar_path = reference_path.with_name(f"{reference_path.name}{server.REFERENCE_METADATA_SUFFIX}")

        self.assertTrue(reference_path.exists())
        self.assertTrue(sidecar_path.exists())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["media_kind"], "video")
        self.assertTrue(items[0]["is_video"])
        self.assertTrue(items[0]["has_prompt"])
        self.assertEqual(items[0]["prompt"], "masterpiece, animated sample")
        self.assertEqual(items[0]["cfg_scale"], "5.5")

    def test_resolve_preview_media_url_prefers_civitai_image_asset_over_stale_preview_image(self) -> None:
        original_fetch = server.fetch_civitai_image_media_url

        def fake_fetch(source_url: str, fallback_url: str = "") -> str:
            self.assertEqual(source_url, "https://civitai.com/images/123030994")
            self.assertEqual(fallback_url, "https://example.com/poster.jpeg")
            return "https://example.com/original-video.mp4"

        server.fetch_civitai_image_media_url = fake_fetch
        self.addCleanup(setattr, server, "fetch_civitai_image_media_url", original_fetch)

        resolved = server.resolve_preview_media_url(
            {
                "source_url": "https://civitai.com/images/123030994",
                "preview_image_url": "https://example.com/poster.jpeg",
            }
        )

        self.assertEqual(resolved, "https://example.com/original-video.mp4")

    def test_resolve_preview_media_url_reuses_civitai_asset_url(self) -> None:
        original_fetch = server.fetch_civitai_image_media_url

        def fail_fetch(source_url: str, fallback_url: str = "") -> str:
            raise AssertionError("Civitai asset URL should be reused without a second API request")

        server.fetch_civitai_image_media_url = fail_fetch
        self.addCleanup(setattr, server, "fetch_civitai_image_media_url", original_fetch)

        resolved = server.resolve_preview_media_url(
            {
                "source_url": "https://civitai.red/images/106772815",
                "preview_media_url": "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/token/width=450/sample.webp",
            }
        )

        self.assertEqual(resolved, "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/token/width=450/sample.webp")

    def test_resolve_preview_media_url_ignores_misleading_video_hint_when_media_kind_is_image(self) -> None:
        original_fetch = server.fetch_civitai_image_media_url

        def fake_fetch(source_url: str, fallback_url: str = "") -> str:
            self.assertEqual(source_url, "https://civitai.com/images/114728048")
            self.assertEqual(fallback_url, "https://example.com/poster.png")
            return "https://example.com/original-image.png"

        server.fetch_civitai_image_media_url = fake_fetch
        self.addCleanup(setattr, server, "fetch_civitai_image_media_url", original_fetch)

        resolved = server.resolve_preview_media_url(
            {
                "source_url": "https://civitai.com/images/114728048",
                "preview_media_kind": "image",
                "preview_image_url": "https://example.com/poster.png",
                "preview_video_url": "https://example.com/unrelated-video.mp4",
            }
        )

        self.assertEqual(resolved, "https://example.com/original-image.png")

    def test_civitai_red_image_page_uses_red_origin_for_media_fetch(self) -> None:
        seen = {}
        original_fetch_json_url = server.fetch_json_url

        def fake_fetch_json_url(json_url: str, referer_url: str = "") -> dict:
            seen["json_url"] = json_url
            seen["referer_url"] = referer_url
            return {
                "result": {
                    "data": {
                        "json": {
                            "url": "asset-token",
                            "name": "sample.webp",
                            "mimeType": "image/webp",
                        }
                    }
                }
            }

        server.fetch_json_url = fake_fetch_json_url
        self.addCleanup(setattr, server, "fetch_json_url", original_fetch_json_url)

        resolved = server.fetch_civitai_image_media_url(
            "https://civitai.red/images/106772815",
            "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/example",
        )

        self.assertTrue(server.is_civitai_image_page_url("https://civitai.red/images/106772815"))
        self.assertIn("https://civitai.red/api/trpc/image.get", seen["json_url"])
        self.assertEqual(seen["referer_url"], "https://civitai.red/images/106772815")
        self.assertIn("/asset-token/width=450/sample.webp", resolved)

    def test_reference_sidecar_keeps_source_details_without_prompt(self) -> None:
        reference_path = self.workspace / "models" / "source_only.references" / "sample.jpg"
        reference_path.parent.mkdir(parents=True, exist_ok=True)
        reference_path.write_bytes(b"image-bytes")

        server.save_reference_prompt_metadata(
            reference_path,
            {
                "source_url": "https://civitai.com/images/51061207",
                "source_name": "Promptless Sample",
                "source_description": "metadata only",
                "author": "Example Author",
                "base_model": "Illustrious",
            },
        )

        sidecar_path = reference_path.with_name(f"{reference_path.name}{server.REFERENCE_METADATA_SUFFIX}")
        self.assertTrue(sidecar_path.exists())

        metadata = server.load_reference_prompt_metadata(reference_path)
        self.assertFalse(metadata["has_prompt"])
        self.assertEqual(metadata["source_url"], "https://civitai.com/images/51061207")
        self.assertEqual(metadata["source_name"], "Promptless Sample")
        self.assertEqual(metadata["author"], "Example Author")
        self.assertEqual(metadata["base_model"], "Illustrious")

    def test_reference_sidecar_keeps_resources_used_without_prompt(self) -> None:
        reference_path = self.workspace / "models" / "resources_only.references" / "sample.jpg"
        reference_path.parent.mkdir(parents=True, exist_ok=True)
        reference_path.write_bytes(b"image-bytes")

        server.save_reference_prompt_metadata(
            reference_path,
            {
                "resources_used": [
                    {
                        "modelId": "81575",
                        "modelVersionId": "97691",
                        "modelName": "Amazing Embeddings - fcNegative + fcPortrait suite",
                        "modelType": "TextualInversion",
                        "versionName": "fcNeg",
                        "strength": 1,
                        "baseModel": "SD 1.5",
                    }
                ]
            },
        )

        sidecar_path = reference_path.with_name(f"{reference_path.name}{server.REFERENCE_METADATA_SUFFIX}")
        self.assertTrue(sidecar_path.exists())

        metadata = server.load_reference_prompt_metadata(reference_path)
        self.assertFalse(metadata["has_prompt"])
        self.assertEqual(len(metadata["resources_used"]), 1)
        self.assertEqual(metadata["resources_used"][0]["type"], "TextualInversion")
        self.assertEqual(
            metadata["resources_used"][0]["url"],
            "https://civitai.red/models/81575?modelVersionId=97691",
        )

    def test_parse_a1111_parameters_extracts_generation_values(self) -> None:
        metadata = server.parse_a1111_parameters(
            "best quality, 1girl\nNegative prompt: blurry\nSteps: 22, Sampler: Euler a, CFG scale: 6.5, Seed: 424242, Size: 832x1216"
        )

        self.assertEqual(metadata["steps"], "22")
        self.assertEqual(metadata["sampler"], "Euler a")
        self.assertEqual(metadata["cfg_scale"], "6.5")
        self.assertEqual(metadata["seed"], "424242")

    def test_import_downloaded_lora_moves_file_and_applies_metadata(self) -> None:
        downloads_dir = Path(self.tempdir.name) / "downloads"
        watch_dir = self.workspace / "models"
        downloaded_path = self.create_model(
            downloads_dir,
            "fresh_download.safetensors",
            {"modelspec.title": "Fresh Download"},
        )

        server.save_lora_config({"watch_dirs": [str(watch_dir)]})
        result = server.import_downloaded_lora(
            str(downloaded_path),
            {
                "author": "Bridge Author",
                "base_model": "NoobAI",
                "source_url": "https://example.com/models/456",
                "description": "Trigger word: fresh trigger\nRecommended strength: 0.9",
                "triggers": ["fresh trigger"],
                "tags": ["downloaded"],
            },
        )

        imported_path = watch_dir / "fresh_download.safetensors"
        self.assertFalse(downloaded_path.exists())
        self.assertTrue(imported_path.exists())
        self.assertEqual(result["path"], str(imported_path.resolve()))
        self.assertEqual(result["filename"], "fresh_download.safetensors")
        self.assertEqual(result["target_dir"], str(watch_dir.resolve()))
        self.assertEqual(result["metadata"]["author"], "Bridge Author")
        self.assertEqual(result["metadata"]["base_model"], "NoobAI")
        self.assertEqual(result["metadata"]["source_url"], "https://example.com/models/456")
        self.assertEqual(result["metadata"]["triggers"], ["fresh trigger"])
        self.assertEqual(result["metadata"]["tags"], ["downloaded"])
        self.assertEqual(
            result["metadata"]["notes"],
            "取得メモ\nTrigger word: fresh trigger\nRecommended strength: 0.9",
        )

    def test_import_downloaded_lora_waits_for_file_to_appear(self) -> None:
        downloads_dir = Path(self.tempdir.name) / "downloads"
        watch_dir = self.workspace / "models"
        delayed_path = downloads_dir / "delayed_download.safetensors"

        server.save_lora_config({"watch_dirs": [str(watch_dir)]})

        original_wait_seconds = server.DOWNLOAD_IMPORT_WAIT_SECONDS
        original_poll_seconds = server.DOWNLOAD_IMPORT_POLL_SECONDS
        server.DOWNLOAD_IMPORT_WAIT_SECONDS = 1.0
        server.DOWNLOAD_IMPORT_POLL_SECONDS = 0.05
        self.addCleanup(setattr, server, "DOWNLOAD_IMPORT_WAIT_SECONDS", original_wait_seconds)
        self.addCleanup(setattr, server, "DOWNLOAD_IMPORT_POLL_SECONDS", original_poll_seconds)

        def create_file_later() -> None:
            time.sleep(0.15)
            self.create_model(downloads_dir, delayed_path.name, {"modelspec.title": "Delayed Download"})

        worker = threading.Thread(target=create_file_later)
        worker.start()
        self.addCleanup(worker.join)

        result = server.import_downloaded_lora(str(delayed_path), {"title": "Delayed Download"})
        worker.join()

        imported_path = watch_dir / delayed_path.name
        self.assertTrue(imported_path.exists())
        self.assertEqual(result["path"], str(imported_path.resolve()))
        self.assertEqual(result["filename"], delayed_path.name)

    def test_import_downloaded_lora_saves_preview_image_when_available(self) -> None:
        downloads_dir = Path(self.tempdir.name) / "downloads"
        watch_dir = self.workspace / "models"
        downloaded_path = self.create_model(
            downloads_dir,
            "with_preview.safetensors",
            {"modelspec.title": "With Preview"},
        )

        server.save_lora_config({"watch_dirs": [str(watch_dir)]})

        original_downloader = server.download_preview_image_for_model

        def fake_downloader(model_path: Path, preview_url: str, referer_url: str = "") -> Path:
            preview_path = model_path.with_suffix(".webp")
            preview_path.write_bytes(b"preview-bytes")
            return preview_path

        server.download_preview_image_for_model = fake_downloader
        self.addCleanup(setattr, server, "download_preview_image_for_model", original_downloader)

        result = server.import_downloaded_lora(
            str(downloaded_path),
            {
                "source_url": "https://example.com/models/789",
                "preview_image_url": "https://example.com/images/preview.webp",
            },
        )

        preview_path = watch_dir / "with_preview.webp"
        self.assertTrue(preview_path.exists())
        self.assertEqual(result["preview_path"], str(preview_path.resolve()))

    def test_import_downloaded_lora_saves_preview_video_when_available(self) -> None:
        downloads_dir = Path(self.tempdir.name) / "downloads"
        watch_dir = self.workspace / "models"
        downloaded_path = self.create_model(
            downloads_dir,
            "with_preview_video.safetensors",
            {"modelspec.title": "With Preview Video"},
        )

        server.save_lora_config({"watch_dirs": [str(watch_dir)]})

        original_downloader = server.download_preview_image_for_model

        def fake_downloader(model_path: Path, preview_url: str, referer_url: str = "") -> Path:
            self.assertEqual(preview_url, "https://example.com/videos/preview.mp4")
            preview_path = model_path.with_suffix(".mp4")
            preview_path.write_bytes(b"preview-video-bytes")
            return preview_path

        server.download_preview_image_for_model = fake_downloader
        self.addCleanup(setattr, server, "download_preview_image_for_model", original_downloader)

        result = server.import_downloaded_lora(
            str(downloaded_path),
            {
                "source_url": "https://example.com/models/987",
                "preview_media_kind": "video",
                "preview_video_url": "https://example.com/videos/preview.mp4",
            },
        )

        preview_path = watch_dir / "with_preview_video.mp4"
        self.assertTrue(preview_path.exists())
        self.assertEqual(result["preview_path"], str(preview_path.resolve()))

    def test_import_downloaded_lora_fetches_civitai_preview_from_source_url(self) -> None:
        downloads_dir = Path(self.tempdir.name) / "downloads"
        watch_dir = self.workspace / "models"
        downloaded_path = self.create_model(
            downloads_dir,
            "civitai_preview.safetensors",
            {"modelspec.title": "Civitai Preview"},
        )

        server.save_lora_config({"watch_dirs": [str(watch_dir)]})

        original_resolver = server.fetch_civitai_preview_image_url
        original_downloader = server.download_preview_image_for_model

        server.fetch_civitai_preview_image_url = lambda source_url: "https://example.com/images/from-civitai.webp"

        def fake_downloader(model_path: Path, preview_url: str, referer_url: str = "") -> Path:
            preview_path = model_path.with_suffix(".webp")
            preview_path.write_bytes(b"preview-bytes")
            return preview_path

        server.download_preview_image_for_model = fake_downloader
        self.addCleanup(setattr, server, "fetch_civitai_preview_image_url", original_resolver)
        self.addCleanup(setattr, server, "download_preview_image_for_model", original_downloader)

        result = server.import_downloaded_lora(
            str(downloaded_path),
            {
                "source_url": "https://civitai.com/models/1543289/example-model",
            },
        )

        preview_path = watch_dir / "civitai_preview.webp"
        self.assertTrue(preview_path.exists())
        self.assertEqual(result["preview_path"], str(preview_path.resolve()))

    def test_import_downloaded_lora_auto_routes_checkpoint_to_checkpoint_watch_dir(self) -> None:
        downloads_dir = Path(self.tempdir.name) / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        lora_dir = self.workspace / "models" / "Lora"
        checkpoint_dir = self.workspace / "models" / "Checkpoints"
        downloaded_path = downloads_dir / "fresh_checkpoint.ckpt"
        downloaded_path.write_bytes(b"checkpoint-bytes")

        server.save_lora_config({"watch_dirs": [str(lora_dir), str(checkpoint_dir)]})
        result = server.import_downloaded_lora(str(downloaded_path), {"title": "Fresh Checkpoint"})

        imported_path = checkpoint_dir / "fresh_checkpoint.ckpt"
        self.assertFalse(downloaded_path.exists())
        self.assertTrue(imported_path.exists())
        self.assertEqual(result["path"], str(imported_path.resolve()))
        self.assertEqual(result["target_dir"], str(checkpoint_dir.resolve()))
        self.assertEqual(result["model_family"], "checkpoint")

    def test_import_downloaded_lora_auto_routes_checkpoint_to_stable_diffusion_watch_dir(self) -> None:
        downloads_dir = Path(self.tempdir.name) / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        lora_dir = self.workspace / "models" / "Lora"
        checkpoint_dir = self.workspace / "models" / "StableDiffusion"
        downloaded_path = downloads_dir / "pony_model.ckpt"
        downloaded_path.write_bytes(b"checkpoint-bytes")

        server.save_lora_config({"watch_dirs": [str(lora_dir), str(checkpoint_dir)]})
        result = server.import_downloaded_lora(str(downloaded_path), {"title": "Pony Model"})

        imported_path = checkpoint_dir / "pony_model.ckpt"
        self.assertFalse(downloaded_path.exists())
        self.assertTrue(imported_path.exists())
        self.assertEqual(result["path"], str(imported_path.resolve()))
        self.assertEqual(result["target_dir"], str(checkpoint_dir.resolve()))
        self.assertEqual(result["model_family"], "checkpoint")

    def test_import_downloaded_lora_auto_routes_lycoris_to_lora_watch_dir(self) -> None:
        downloads_dir = Path(self.tempdir.name) / "downloads"
        lora_dir = self.workspace / "models" / "Lora"
        lycoris_dir = self.workspace / "models" / "LyCORIS"
        downloaded_path = self.create_model(
            downloads_dir,
            "artist_mix.safetensors",
            {
                "modelspec.title": "Artist Mix",
                "ss_network_module": "lycoris.kohya",
            },
        )

        server.save_lora_config({"watch_dirs": [str(lora_dir), str(lycoris_dir)]})
        result = server.import_downloaded_lora(str(downloaded_path), {"title": "Artist Mix"})

        imported_path = lora_dir / "artist_mix.safetensors"
        self.assertFalse(downloaded_path.exists())
        self.assertTrue(imported_path.exists())
        self.assertEqual(result["path"], str(imported_path.resolve()))
        self.assertEqual(result["target_dir"], str(lora_dir.resolve()))
        self.assertEqual(result["model_family"], "lora")

    def test_download_remote_model_and_import_uses_cookie_header(self) -> None:
        watch_dir = self.workspace / "models"
        server.save_lora_config({"watch_dirs": [str(watch_dir)]})

        seen_headers = {}
        original_urlopen = server.urllib.request.urlopen

        class FakeHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class FakeResponse:
            def __init__(self):
                self.headers = FakeHeaders({"Content-Disposition": 'attachment; filename="remote_model.safetensors"'})
                self._stream = io.BytesIO(b"remote-model-bytes")

            def read(self, size=-1):
                return self._stream.read(size)

            def geturl(self):
                return "https://files.civitai.com/download/remote_model.safetensors"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout=0):
            seen_headers["Cookie"] = request.headers.get("Cookie")
            seen_headers["Referer"] = request.headers.get("Referer")
            return FakeResponse()

        original_import = server.import_downloaded_lora

        def fake_import(path_text: str, metadata_payload=None, target_dir_text=""):
            imported_path = Path(path_text)
            self.assertTrue(imported_path.exists())
            self.assertEqual(imported_path.name, "remote_model.safetensors")
            return {
                "path": str((watch_dir / "remote_model.safetensors").resolve()),
                "filename": "remote_model.safetensors",
                "target_dir": str(watch_dir.resolve()),
                "model_family": "lora",
                "model_family_label": "LoRA",
                "preview_path": "",
                "metadata": metadata_payload or {},
            }

        server.urllib.request.urlopen = fake_urlopen
        server.import_downloaded_lora = fake_import
        self.addCleanup(setattr, server.urllib.request, "urlopen", original_urlopen)
        self.addCleanup(setattr, server, "import_downloaded_lora", original_import)

        result = server.download_remote_model_and_import(
            "https://civitai.com/api/download/models/2542544",
            {"title": "Remote Model"},
            "",
            suggested_filename="remote_model.safetensors",
            cookie_header="session=abc123; other=value",
            referer_url="https://civitai.com/models/270767?modelVersionId=2542544",
        )

        self.assertEqual(result["filename"], "remote_model.safetensors")
        self.assertEqual(seen_headers["Cookie"], "session=abc123; other=value")
        self.assertEqual(seen_headers["Referer"], "https://civitai.com/models/270767?modelVersionId=2542544")

    def test_download_remote_model_to_temp_file_rejects_login_html(self) -> None:
        original_urlopen = server.urllib.request.urlopen

        class FakeHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class FakeResponse:
            def __init__(self):
                self.headers = FakeHeaders({"Content-Type": "text/html; charset=utf-8"})
                self._stream = io.BytesIO(b"<!doctype html><html><body>login</body></html>")

            def read(self, size=-1):
                return self._stream.read(size)

            def geturl(self):
                return "https://civitai.com/login?returnUrl=%2Fmodel-versions%2F2542544&reason=download-auth"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        server.urllib.request.urlopen = lambda request, timeout=0: FakeResponse()
        self.addCleanup(setattr, server.urllib.request, "urlopen", original_urlopen)

        with self.assertRaises(PermissionError):
            server.download_remote_model_to_temp_file(
                "https://civitai.com/api/download/models/2542544",
                suggested_filename="remote_model.safetensors",
                cookie_header="session=abc123",
                referer_url="https://civitai.com/models/270767?modelVersionId=2542544",
            )

    def test_process_pending_download_import_imports_new_download(self) -> None:
        downloads_dir = self.workspace / "downloads"
        watch_dir = self.workspace / "models"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        server.save_lora_config({"watch_dirs": [str(watch_dir)]})

        server.arm_pending_download_import(
            "",
            {"title": "Queued Model", "author": "Queue Author"},
            "queued_model.safetensors",
            str(downloads_dir),
        )

        original_settle = server.PENDING_IMPORT_SETTLE_SECONDS
        original_scan = server.start_background_scan
        server.PENDING_IMPORT_SETTLE_SECONDS = 0
        self.addCleanup(setattr, server, "PENDING_IMPORT_SETTLE_SECONDS", original_settle)
        self.addCleanup(setattr, server, "start_background_scan", original_scan)
        server.start_background_scan = lambda *args, **kwargs: None

        downloaded_path = self.create_model(downloads_dir, "queued_model.safetensors", {"modelspec.title": "Queued Model"})
        server.process_pending_download_import()
        server.process_pending_download_import()

        imported_path = watch_dir / "queued_model.safetensors"
        self.assertFalse(downloaded_path.exists())
        self.assertTrue(imported_path.exists())
        status_payload = server.get_pending_download_import_payload()
        self.assertIsNone(status_payload["pending"])
        self.assertEqual(status_payload["last_result"]["status"], "complete")
        self.assertEqual(status_payload["last_result"]["filename"], "queued_model.safetensors")
        self.assertEqual(status_payload["last_result"]["item"]["path"], str(imported_path.resolve()))

    def test_watch_lora_library_changes_survives_snapshot_exception(self) -> None:
        stop_event = threading.Event()
        original_load_config = server.load_lora_config
        original_build_snapshot = server.build_watch_dir_snapshot
        original_poll_seconds = server.WATCH_POLL_SECONDS

        self.addCleanup(setattr, server, "load_lora_config", original_load_config)
        self.addCleanup(setattr, server, "build_watch_dir_snapshot", original_build_snapshot)
        self.addCleanup(setattr, server, "WATCH_POLL_SECONDS", original_poll_seconds)

        calls = {"count": 0}

        server.WATCH_POLL_SECONDS = 0
        server.load_lora_config = lambda: {"watch_dirs": []}

        def flaky_snapshot(_config=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("temporary snapshot failure")
            stop_event.set()
            return ()

        server.build_watch_dir_snapshot = flaky_snapshot

        server.watch_lora_library_changes(stop_event)

        self.assertGreaterEqual(calls["count"], 2)

    def test_find_pending_download_candidate_ignores_unrelated_file_when_expected_name_exists(self) -> None:
        downloads_dir = self.workspace / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)

        server.arm_pending_download_import(
            "",
            {"title": "Queued Model"},
            "queued_model.safetensors",
            str(downloads_dir),
        )
        self.create_model(downloads_dir, "other_model.safetensors", {"modelspec.title": "Other Model"})

        pending = server.get_pending_download_import_payload()["pending"]
        candidate = server.find_pending_download_candidate(pending)

        self.assertIsNone(candidate)

    def test_find_pending_download_candidate_accepts_chrome_uniquified_filename(self) -> None:
        downloads_dir = self.workspace / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)

        server.arm_pending_download_import(
            "",
            {"title": "Queued Model"},
            "queued_model.safetensors",
            str(downloads_dir),
        )
        self.create_model(downloads_dir, "queued_model (1).safetensors", {"modelspec.title": "Queued Model"})

        pending = server.get_pending_download_import_payload()["pending"]
        candidate = server.find_pending_download_candidate(pending)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["name"], "queued_model (1).safetensors")

    def test_find_pending_download_candidate_ignores_preexisting_exact_filename(self) -> None:
        downloads_dir = self.workspace / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)

        self.create_model(downloads_dir, "queued_model.safetensors", {"modelspec.title": "Old Queued Model"})
        server.arm_pending_download_import(
            "",
            {"title": "Queued Model"},
            "queued_model.safetensors",
            str(downloads_dir),
        )

        pending = server.get_pending_download_import_payload()["pending"]
        candidate = server.find_pending_download_candidate(pending)

        self.assertIsNone(candidate)

    def test_find_pending_download_candidate_prefers_new_uniquified_file_over_preexisting_exact_filename(self) -> None:
        downloads_dir = self.workspace / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)

        self.create_model(downloads_dir, "queued_model.safetensors", {"modelspec.title": "Old Queued Model"})
        server.arm_pending_download_import(
            "",
            {"title": "Queued Model"},
            "queued_model.safetensors",
            str(downloads_dir),
        )
        self.create_model(downloads_dir, "queued_model (2).safetensors", {"modelspec.title": "New Queued Model"})

        pending = server.get_pending_download_import_payload()["pending"]
        candidate = server.find_pending_download_candidate(pending)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["name"], "queued_model (2).safetensors")

    def test_add_browser_import_reference_to_path_can_return_summary_only(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(
            models_dir,
            "summary_target.safetensors",
            {"modelspec.title": "Summary Target"},
        )

        browser_import = server.store_browser_import(
            {
                "source_url": "https://civitai.com/images/42",
                "title": "Queued Ref",
                "preview_image_url": "https://example.com/queued-ref.png",
            }
        )

        original_save_reference = server.save_reference_image_from_url

        def fake_save_reference(model_path_arg, _preview_url, prompt_metadata, _source_url, preferred_filename):
            reference_dir = server.reference_folder_for_model(model_path_arg)
            reference_dir.mkdir(parents=True, exist_ok=True)
            target_path = reference_dir / preferred_filename
            target_path.write_bytes(b"queued-reference")
            server.save_reference_prompt_metadata(target_path, prompt_metadata)
            return target_path

        server.save_reference_image_from_url = fake_save_reference
        self.addCleanup(setattr, server, "save_reference_image_from_url", original_save_reference)

        result = server.add_browser_import_reference_to_path(
            browser_import["id"],
            str(model_path.resolve()),
            consume=True,
            include_items=False,
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"], [])
        self.assertIsNone(server.get_browser_import(browser_import["id"]))

    def test_build_watch_dir_snapshot_tracks_model_changes_only(self) -> None:
        models_dir = self.workspace / "models"
        self.create_model(models_dir, "first.safetensors", {"modelspec.title": "First"})
        (models_dir / "note.txt").write_text("ignore", encoding="utf-8")

        config = {"watch_dirs": [str(models_dir)]}
        snapshot_before = server.build_watch_dir_snapshot(config)
        self.assertEqual(len(snapshot_before), 1)
        self.assertIn("first.safetensors", snapshot_before[0][0])

        self.create_model(models_dir, "second.safetensors", {"modelspec.title": "Second"})
        snapshot_after = server.build_watch_dir_snapshot(config)

        self.assertEqual(len(snapshot_after), 2)
        self.assertNotEqual(snapshot_before, snapshot_after)

    def test_start_background_scan_reuses_ready_result_when_config_is_unchanged(self) -> None:
        models_dir = self.workspace / "models"
        config = {"watch_dirs": [str(models_dir)]}
        cached_payload = {
            "config": config,
            "warnings": [],
            "scanned_at": "2026-03-22T00:00:00Z",
            "scan_duration_ms": 12,
            "stats": {"total": 1, "favorites": 0, "with_preview": 0, "categories": {}, "families": {"lora": 1}},
            "items": [{"path": "cached-item"}],
        }
        server.SCAN_STATE = {
            "status": "ready",
            "started_at": "2026-03-22T00:00:00Z",
            "finished_at": "2026-03-22T00:00:01Z",
            "error": "",
            "config": config,
            "result": cached_payload,
            "revision": 7,
        }

        original_scan = server.scan_lora_library

        def fail_scan(_config=None):
            raise AssertionError("scan_lora_library should not be called for unchanged ready config")

        server.scan_lora_library = fail_scan
        self.addCleanup(setattr, server, "scan_lora_library", original_scan)

        payload = server.start_background_scan(config=config, force=False)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["revision"], 7)
        self.assertEqual(payload["items"], [{"path": "cached-item"}])

    def test_start_background_scan_runs_scan_in_background(self) -> None:
        models_dir = self.workspace / "models"
        config = {"watch_dirs": [str(models_dir)]}
        scan_started = threading.Event()
        allow_finish = threading.Event()

        original_scan = server.scan_lora_library

        def fake_scan(_config=None):
            scan_started.set()
            self.assertTrue(allow_finish.wait(timeout=2), "background scan did not receive release signal")
            return {
                "config": config,
                "warnings": [],
                "scanned_at": "2026-03-22T00:00:00Z",
                "scan_duration_ms": 25,
                "stats": {"total": 1, "favorites": 0, "with_preview": 0, "categories": {}, "families": {"lora": 1}},
                "items": [{"path": "background-item"}],
            }

        server.scan_lora_library = fake_scan
        self.addCleanup(setattr, server, "scan_lora_library", original_scan)

        payload = server.start_background_scan(config=config, force=True)

        self.assertTrue(scan_started.wait(timeout=2), "background scan thread did not start")
        self.assertEqual(payload["status"], "scanning")
        self.assertEqual(payload["items"], [])

        allow_finish.set()
        deadline = time.time() + 2
        ready_payload = payload
        while time.time() < deadline:
            ready_payload = server.get_scan_state_payload()
            if ready_payload["status"] == "ready":
                break
            time.sleep(0.05)

        self.assertEqual(ready_payload["status"], "ready")
        self.assertEqual(ready_payload["items"], [{"path": "background-item"}])

    def test_reveal_in_file_browser_opens_parent_folder_on_windows(self) -> None:
        models_dir = self.workspace / "models"
        model_path = self.create_model(models_dir, "reveal_target.safetensors", {"modelspec.title": "Reveal Target"})

        original_popen = server.subprocess.Popen
        original_os_name = server.os.name
        seen_calls = []

        def fake_popen(args, *extra_args, **extra_kwargs):
            seen_calls.append((args, extra_args, extra_kwargs))

            class DummyProcess:
                pass

            return DummyProcess()

        server.subprocess.Popen = fake_popen
        server.os.name = "nt"
        self.addCleanup(setattr, server.subprocess, "Popen", original_popen)
        self.addCleanup(setattr, server.os, "name", original_os_name)

        server.reveal_in_file_browser(model_path)

        self.assertEqual(len(seen_calls), 1)
        self.assertEqual(seen_calls[0][0], ["explorer.exe", str(model_path.resolve().parent)])

    def test_handler_send_json_ignores_client_disconnect_during_write(self) -> None:
        class DisconnectingWriter:
            def write(self, _data):
                raise ConnectionAbortedError(10053, "connection aborted")

        class DummyHandler:
            path = "/api/lora/library"

            def __init__(self) -> None:
                self.status = None
                self.headers = []
                self.wfile = DisconnectingWriter()

            def send_response(self, status):
                self.status = status

            def send_header(self, name, value):
                self.headers.append((name, value))

            def end_headers(self):
                return None

            def _is_browser_import_endpoint(self, _path: str) -> bool:
                return False

            def _finish_response(self, data: bytes = b""):
                return server.Handler._finish_response(self, data)

        dummy = DummyHandler()

        server.Handler._send_json(dummy, {"ok": True})

        self.assertEqual(dummy.status, server.HTTPStatus.OK)
        self.assertTrue(any(name == "Content-Type" for name, _value in dummy.headers))

    def test_scan_merges_lycoris_into_lora_family(self) -> None:
        models_dir = self.workspace / "models"
        self.create_model(
            models_dir / "loras",
            "hero_style.safetensors",
            {
                "modelspec.title": "Hero Style",
                "trainedWords": "hero pose",
            },
        )
        self.create_model(
            models_dir / "LyCORIS",
            "artist_mix.safetensors",
            {
                "modelspec.title": "Artist Mix",
                "ss_network_module": "lycoris.kohya",
                "trainedWords": "soft shading",
            },
        )
        self.create_model(
            models_dir / "checkpoints",
            "base_model.safetensors",
            {
                "modelspec.title": "Base Model",
            },
        )

        server.save_lora_config({"watch_dirs": [str(models_dir)]})
        payload = server.scan_lora_library()
        items = {item["filename"]: item for item in payload["items"]}

        self.assertEqual(items["hero_style.safetensors"]["model_family"], "lora")
        self.assertEqual(items["artist_mix.safetensors"]["model_family"], "lora")
        self.assertEqual(items["artist_mix.safetensors"]["prompt_snippet"], "<lora:artist_mix:0.8>, soft shading")
        self.assertEqual(items["base_model.safetensors"]["model_family"], "checkpoint")
        self.assertEqual(items["base_model.safetensors"]["prompt_snippet"], "")

        self.assertEqual(payload["stats"]["families"]["lora"], 2)
        self.assertNotIn("lycoris", payload["stats"]["families"])
        self.assertEqual(payload["stats"]["families"]["checkpoint"], 1)


if __name__ == "__main__":
    unittest.main()

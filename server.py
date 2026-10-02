import argparse
import base64
from collections import Counter
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import html
import hashlib
import importlib.util
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sqlite3
import struct
import subprocess
import threading
import urllib.request
import urllib.error
import webbrowser
import zlib
import zipfile
from functools import lru_cache
from pathlib import PurePosixPath
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic, perf_counter, sleep, time_ns
from urllib.parse import parse_qs, quote, unquote, urlparse

mimetypes.add_type("application/manifest+json", ".webmanifest")

APP_ROOT = Path(__file__).parent
_forge_spec = importlib.util.spec_from_file_location("lora_forge_connector", APP_ROOT / "forge_connector.py")
forge_connector = importlib.util.module_from_spec(_forge_spec)
_forge_spec.loader.exec_module(forge_connector)
DATA_ROOT = APP_ROOT / "data"
LORA_CONFIG_PATH = DATA_ROOT / "lora-manager-config.json"
LAN_TOKEN_PATH = DATA_ROOT / "lan-token.txt"
LORA_DB_PATH = DATA_ROOT / "lora-manager.sqlite3"
LEGACY_LORA_STORE_PATH = DATA_ROOT / "lora-manager-db.json"

MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin"}
WORKFLOW_EXTENSIONS = {".json", ".zip"}
DOWNLOAD_EXTENSIONS = MODEL_EXTENSIONS | WORKFLOW_EXTENSIONS
MAX_WORKFLOW_JSON_BYTES = 32 * 1024 * 1024
MAX_WORKFLOW_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_WORKFLOW_ARCHIVE_FILES = 256
MAX_WORKFLOW_EXPANDED_BYTES = 128 * 1024 * 1024
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".avif"}
REFERENCE_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
REFERENCE_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | REFERENCE_VIDEO_EXTENSIONS
PREVIEW_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | REFERENCE_VIDEO_EXTENSIONS
PREVIEW_TOKENS = {"preview", "thumb", "thumbnail", "sample"}
MAX_SAFETENSORS_HEADER_BYTES = 16 * 1024 * 1024
MAX_PREVIEW_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_REFERENCE_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_REFERENCE_UPLOAD_FILES = 24
REFERENCE_METADATA_SUFFIX = ".refmeta.json"
CIVITAI_ASSET_BASE_URL = "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA"
CIVITAI_WEB_BASE_URL = "https://civitai.red"
CIVITAI_PAGE_HOSTS = {"civitai.com", "www.civitai.com", "civitai.red", "www.civitai.red"}
CHECKPOINT_SIZE_THRESHOLD_BYTES = 512 * 1024 * 1024
DOWNLOAD_IMPORT_WAIT_SECONDS = 12.0
DOWNLOAD_IMPORT_POLL_SECONDS = 0.2
PENDING_IMPORT_POLL_SECONDS = 0.35
PENDING_IMPORT_SETTLE_SECONDS = 0.8
REMOTE_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
PENDING_IMPORT_TTL_MS = 24 * 60 * 60 * 1000
ENCYCLOPEDIA_EXPORT_QUEUE_LOCK = threading.Lock()
ENCYCLOPEDIA_EXPORT_QUEUE: list[dict] = []
CATEGORY_KEYWORDS = {
    "character": {"character", "characters", "char", "girl", "boy", "person"},
    "style": {"style", "artist", "artstyle", "render", "painting"},
    "pose": {"pose", "gesture", "expression", "hand"},
    "clothes": {"clothes", "clothing", "outfit", "uniform", "fashion"},
    "concept": {"concept", "object", "prop", "scene", "background"},
    "nsfw": {"nsfw", "ero", "lewd", "adult", "18plus"},
}
CIVITAI_RESOURCE_URN_RE = re.compile(
    r"^urn:air:(?P<base_model>[^:]+):(?P<resource_type>[^:]+):civitai:(?P<model_id>\d+)(?:@(?P<model_version_id>\d+))?$",
    re.IGNORECASE,
)
MODEL_FAMILY_LABELS = {
    "lora": "LoRA",
    "lycoris": "LyCORIS",
    "checkpoint": "Checkpoint",
    "embedding": "Embedding",
    "workflow": "Workflow",
    "other": "Other",
}
WATCH_DIR_FAMILY_ALIASES = {
    "workflow": {"workflow", "workflows"},
    "lora": {"lora", "loras"},
    "lycoris": {"lycoris", "locon", "loha", "lokr"},
    "checkpoint": {"checkpoint", "checkpoints", "stablediffusion", "stable-diffusion", "stable_diffusion"},
    "embedding": {"embedding", "embeddings", "textualinversion", "textual-inversion"},
}
EDITABLE_METADATA_KEYS = {
    "display_name",
    "favorite",
    "rating",
    "category",
    "triggers",
    "tags",
    "notes",
    "recommended_steps",
    "recommended_sampler",
    "recommended_scheduler",
    "generation_settings",
    "author",
    "base_model",
    "source_url",
    "strength_min",
    "strength_max",
}
WATCH_POLL_SECONDS = 2.5
WATCH_SETTLE_SECONDS = 2.5
SCAN_STATE_LOCK = threading.Lock()
SCAN_STATE = {
    "status": "idle",
    "started_at": "",
    "finished_at": "",
    "error": "",
    "config": {"watch_dirs": []},
    "result": None,
    "revision": 0,
}
WATCHER_THREAD_LOCK = threading.Lock()
WATCHER_THREAD_STARTED = False
PENDING_DOWNLOAD_IMPORT_LOCK = threading.Lock()
DOWNLOAD_COMPLETION_LOCK = threading.RLock()
METADATA_WRITE_LOCK = threading.RLock()
PENDING_DOWNLOAD_IMPORT = None
LAST_DOWNLOAD_IMPORT_RESULT = {"status": "idle", "message": "", "updated_at": ""}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_civitai_page_host(host: str) -> bool:
    normalized = normalize_text(host).lower()
    return normalized in CIVITAI_PAGE_HOSTS or normalized.endswith(".civitai.com") or normalized.endswith(".civitai.red")


def civitai_origin_for_url(source_url: str) -> str:
    parsed = urlparse(normalize_text(source_url))
    if parsed.scheme in {"http", "https"} and is_civitai_page_host(parsed.netloc):
        return f"{parsed.scheme}://{parsed.netloc}"
    return CIVITAI_WEB_BASE_URL


def read_json_file(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_file(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def get_default_downloads_dir() -> Path:
    if os.name == "nt":
        try:
            from uuid import UUID

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            def guid_from_uuid(value: UUID) -> GUID:
                data4 = (ctypes.c_ubyte * 8).from_buffer_copy(value.bytes[8:])
                return GUID(value.time_low, value.time_mid, value.time_hi_version, data4)

            downloads_guid = guid_from_uuid(UUID("{374DE290-123F-4565-9164-39C4925E467B}"))
            path_ptr = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            ole32 = ctypes.windll.ole32
            result = shell32.SHGetKnownFolderPath(ctypes.byref(downloads_guid), 0, None, ctypes.byref(path_ptr))
            if result == 0 and path_ptr.value:
                try:
                    return Path(path_ptr.value)
                finally:
                    ole32.CoTaskMemFree(path_ptr)
        except Exception:
            pass

    return Path.home() / "Downloads"


def persist_download_state() -> None:
    with PENDING_DOWNLOAD_IMPORT_LOCK:
        state = {"pending": PENDING_DOWNLOAD_IMPORT, "last_result": LAST_DOWNLOAD_IMPORT_RESULT}
        encoded = json.dumps(state, ensure_ascii=False)
    with db_connection() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS bridge_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        connection.execute("INSERT INTO bridge_state(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (encoded,))


def restore_download_state() -> None:
    global PENDING_DOWNLOAD_IMPORT
    with DOWNLOAD_COMPLETION_LOCK:
        with db_connection() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS bridge_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            row = connection.execute("SELECT payload FROM bridge_state WHERE id=1").fetchone()
        if not row:
            return
        saved = json.loads(row["payload"])
        pending = saved.get("pending")
        if isinstance(pending, dict):
            elapsed = max(0, datetime.now(timezone.utc).timestamp() - float(pending.get("created_epoch") or 0))
            pending["armed_monotonic"] = monotonic() - elapsed
            pending["seen_files"] = {}
        with PENDING_DOWNLOAD_IMPORT_LOCK:
            PENDING_DOWNLOAD_IMPORT = pending if isinstance(pending, dict) else None
            LAST_DOWNLOAD_IMPORT_RESULT.clear()
            LAST_DOWNLOAD_IMPORT_RESULT.update(saved.get("last_result") or {"status": "idle"})
        expire_pending_download()


def expire_pending_download() -> bool:
    with DOWNLOAD_COMPLETION_LOCK:
        with PENDING_DOWNLOAD_IMPORT_LOCK:
            pending = PENDING_DOWNLOAD_IMPORT
            expired = pending is not None and monotonic() - float(pending.get("armed_monotonic") or 0) > PENDING_IMPORT_TTL_MS / 1000
        if expired:
            clear_pending_download_import()
            set_last_download_import_result("error", "ダウンロード待機が24時間を超えました。候補を選んで再開してください。")
        return bool(expired)


def cancel_pending_download(created_epoch) -> dict:
    with DOWNLOAD_COMPLETION_LOCK:
        with PENDING_DOWNLOAD_IMPORT_LOCK:
            pending = PENDING_DOWNLOAD_IMPORT
        if not pending or pending.get("created_epoch") != created_epoch:
            return {"cancelled": False}
        clear_pending_download_import()
        set_last_download_import_result("cancelled", "取り込み待機を解除しました。ダウンロードファイルは変更していません。")
        return {"cancelled": True}


def set_last_download_import_result(status: str, message: str = "", **extra) -> None:
    payload = {
        "status": normalize_text(status) or "idle",
        "message": normalize_text(message),
        "updated_at": utc_now_iso(),
    }
    payload.update(extra)
    with PENDING_DOWNLOAD_IMPORT_LOCK:
        LAST_DOWNLOAD_IMPORT_RESULT.clear()
        LAST_DOWNLOAD_IMPORT_RESULT.update(payload)
    persist_download_state()


def get_pending_download_import_payload() -> dict:
    expire_pending_download()
    with PENDING_DOWNLOAD_IMPORT_LOCK:
        pending = dict(PENDING_DOWNLOAD_IMPORT) if isinstance(PENDING_DOWNLOAD_IMPORT, dict) else None
        last_result = dict(LAST_DOWNLOAD_IMPORT_RESULT)

    return {
        "pending": pending,
        "last_result": last_result,
    }


def has_pending_download_import() -> bool:
    with PENDING_DOWNLOAD_IMPORT_LOCK:
        return isinstance(PENDING_DOWNLOAD_IMPORT, dict)


def build_downloads_dir_model_snapshot(downloads_dir: Path) -> dict[str, dict]:
    snapshot: dict[str, dict] = {}
    if not downloads_dir.exists() or not downloads_dir.is_dir():
        return snapshot

    try:
        for child in downloads_dir.iterdir():
            if not child.is_file() or child.suffix.lower() not in DOWNLOAD_EXTENSIONS:
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            snapshot[str(child.resolve(strict=False))] = {
                "size": int(stat.st_size),
                "mtime": float(stat.st_mtime),
            }
    except OSError:
        return {}

    return snapshot


def arm_pending_download_import(*args, **kwargs) -> dict:
    with DOWNLOAD_COMPLETION_LOCK:
        return _arm_pending_download_import(*args, **kwargs)


def _arm_pending_download_import(
    target_dir_text: str = "",
    metadata_payload: dict | None = None,
    expected_filename: str = "",
    downloads_dir_text: str = "",
    auto_triggered: bool = False,
) -> dict:
    global PENDING_DOWNLOAD_IMPORT
    metadata = metadata_payload if isinstance(metadata_payload, dict) else {}
    source_host = urlparse(normalize_text(metadata.get("source_url"))).hostname or ""
    if auto_triggered and (not is_civitai_page_host(source_host)
                           or not re.match(r"^/models/\d+(?:/|$)", urlparse(normalize_text(metadata.get("source_url"))).path)
                           or not download_resource_key(metadata.get("download_url", ""))):
        raise ValueError("自動待機にはモデルページと確定した取得URLが必要です。")
    if is_civitai_page_host(source_host) and not download_resource_key(metadata.get("download_url", "")):
        raise ValueError("取得URLが確定していません。ページ情報を再取得して候補を選んでください。")
    workflow_requested = (guess_import_model_family_from_payload(metadata) == "workflow"
                          or Path(normalize_text(expected_filename)).suffix.lower() in WORKFLOW_EXTENSIONS)
    if workflow_requested and Path(normalize_text(expected_filename)).suffix.lower() not in WORKFLOW_EXTENSIONS:
        raise ValueError("Workflow は JSON / ZIP のファイル名が分かる候補を選んでください。")
    if workflow_requested:
        metadata = dict(metadata, model_family_hint="workflow")
        workflow_roots = [path for path in load_lora_config()["watch_dirs"] if infer_watch_dir_model_family(path) == "workflow"]
        if not workflow_roots:
            raise ValueError("ComfyUI の workflows フォルダを登録してください。")
    if is_civitai_page_host(source_host):
        config = load_lora_config()
        resolve_import_target_dir(target_dir_text, config["watch_dirs"], Path(expected_filename or "download.safetensors"), metadata)
    expire_pending_download()
    with PENDING_DOWNLOAD_IMPORT_LOCK:
        current = PENDING_DOWNLOAD_IMPORT
    if current:
        if (current.get("source_url") == normalize_text(metadata.get("source_url"))
                and current.get("metadata", {}).get("download_url", "") == metadata.get("download_url", "")
                and current.get("expected_filename", "") == (sanitize_download_filename(expected_filename) if expected_filename else "")
                and current.get("target_dir", "") == normalize_text(target_dir_text)):
            return get_pending_download_import_payload()
        raise ValueError("別の取り込みを待機中です。先に待機を解除してください。")
    downloads_dir = Path(normalize_text(downloads_dir_text)).expanduser() if normalize_text(downloads_dir_text) else get_default_downloads_dir()
    payload = {
        "created_at": utc_now_iso(),
        "created_epoch": datetime.now(timezone.utc).timestamp(),
        "armed_monotonic": monotonic(),
        "target_dir": normalize_text(target_dir_text),
        "expected_filename": sanitize_download_filename(expected_filename) if normalize_text(expected_filename) else "",
        "downloads_dir": str(downloads_dir.resolve(strict=False)),
        "source_url": normalize_text(metadata.get("source_url")),
        "auto_triggered": bool(auto_triggered),
        "existing_files": build_downloads_dir_model_snapshot(downloads_dir),
        "metadata": metadata,
        "seen_files": {},
        "status": "waiting",
        "message": "Waiting for a downloaded model file.",
    }

    with PENDING_DOWNLOAD_IMPORT_LOCK:
        PENDING_DOWNLOAD_IMPORT = payload

    set_last_download_import_result("waiting", "Waiting for a downloaded model file.", downloads_dir=payload["downloads_dir"])
    return get_pending_download_import_payload()


def clear_pending_download_import() -> None:
    with PENDING_DOWNLOAD_IMPORT_LOCK:
        global PENDING_DOWNLOAD_IMPORT
        PENDING_DOWNLOAD_IMPORT = None


def normalize_pending_download_match_name(filename: str) -> str:
    cleaned = sanitize_download_filename(filename)
    path = Path(cleaned)
    stem = re.sub(r"\s+\(\d+\)$", "", path.stem).strip().lower()
    suffix = path.suffix.lower()
    return f"{stem}{suffix}"


def find_pending_download_candidate(pending: dict) -> dict | None:
    downloads_dir = Path(normalize_text(pending.get("downloads_dir"))).expanduser()
    if not downloads_dir.exists() or not downloads_dir.is_dir():
        return None

    expected_filename = normalize_text(pending.get("expected_filename")).lower()
    expected_match_name = normalize_pending_download_match_name(expected_filename) if expected_filename else ""
    created_epoch = float(pending.get("created_epoch") or 0.0)
    existing_files = pending.get("existing_files") if isinstance(pending.get("existing_files"), dict) else {}
    candidates: list[dict] = []

    try:
        for child in downloads_dir.iterdir():
            if not child.is_file():
                continue
            allowed_extensions = WORKFLOW_EXTENSIONS if Path(expected_filename).suffix.lower() in WORKFLOW_EXTENSIONS else MODEL_EXTENSIONS
            if child.suffix.lower() not in allowed_extensions:
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            path_key = str(child.resolve(strict=False))
            previous_signature = existing_files.get(path_key) if isinstance(existing_files, dict) else None
            if (
                isinstance(previous_signature, dict)
                and int(previous_signature.get("size", -1)) == int(stat.st_size)
                and float(previous_signature.get("mtime", -1.0)) == float(stat.st_mtime)
            ):
                continue
            if created_epoch and stat.st_mtime + 2 < created_epoch:
                continue
            candidates.append(
                {
                    "path": child,
                    "name": child.name,
                    "size": int(stat.st_size),
                    "mtime": float(stat.st_mtime),
                    "match_name": normalize_pending_download_match_name(child.name),
                }
            )
    except OSError:
        return None

    if not candidates:
        return None

    if expected_filename:
        exact_matches = [entry for entry in candidates if entry["name"].lower() == expected_filename]
        if exact_matches:
            exact_matches.sort(key=lambda entry: entry["mtime"], reverse=True)
            return exact_matches[0]

        related_matches = [entry for entry in candidates if entry["match_name"] == expected_match_name]
        if related_matches:
            related_matches.sort(key=lambda entry: entry["mtime"], reverse=True)
            return related_matches[0]

        return None

    candidates.sort(key=lambda entry: entry["mtime"], reverse=True)
    return candidates[0]


def process_pending_download_import() -> None:
    with DOWNLOAD_COMPLETION_LOCK:
        _process_pending_download_import()


def download_resource_key(value: str) -> str:
    """Match selected Civitai assets across CDN redirects, not display names."""
    parsed = urlparse(normalize_text(value))
    if parsed.scheme != "https":
        return ""
    host = (parsed.hostname or "").lower()
    path = parsed.path
    # These delivery endpoints expose the same Civitai bucket object key.
    # Keep host + bucket restrictions: a filename alone is not an identity.
    bucket_prefixes = {
        "s3.us-west-004.backblazeb2.com": "/civitai-modelfiles/",
        "b2.civitai.com": "/file/civitai-modelfiles/",
    }
    prefix = bucket_prefixes.get(host)
    if prefix and path.startswith(prefix):
        return "civitai-asset:" + path.removeprefix(prefix)
    if host.startswith("civitai-delivery-worker-prod.") and host.endswith(".r2.cloudflarestorage.com"):
        return "civitai-asset:" + path.lstrip("/")
    if host in CIVITAI_PAGE_HOSTS and re.fullmatch(r"/api/download/models/\d+", path):
        query = parse_qs(parsed.query)
        query.pop("token", None)
        return "civitai-api:" + path + repr(sorted((k, sorted(v)) for k, v in query.items()))
    return ""


def complete_browser_pending_download(payload: dict) -> dict:
    with DOWNLOAD_COMPLETION_LOCK:
        expire_pending_download()
        with PENDING_DOWNLOAD_IMPORT_LOCK:
            pending = dict(PENDING_DOWNLOAD_IMPORT) if isinstance(PENDING_DOWNLOAD_IMPORT, dict) else None
        if not pending or payload.get("created_epoch") != pending.get("created_epoch"):
            return {"matched": False}
        expected = download_resource_key(pending.get("metadata", {}).get("download_url", ""))
        urls = payload.get("download_urls", [])
        if not expected or not isinstance(urls, list) or not any(download_resource_key(u) == expected for u in urls if isinstance(u, str)):
            return {"matched": False}
        if payload.get("download_state") == "interrupted":
            clear_pending_download_import()
            message = "Chromeのダウンロードが中断されました。ダウンロードを再開後、取り込み待機を開始してください。"
            set_last_download_import_result("error", message)
            return {"matched": True, "status": "error", "message": message}
        source = Path(normalize_text(payload.get("source_path"))).expanduser()
        if not source.is_file() or source.suffix.lower() not in DOWNLOAD_EXTENSIONS:
            return {"matched": False}
        try:
            result = import_downloaded_lora(str(source), pending.get("metadata", {}), pending.get("target_dir", ""))
        except (ValueError, OSError, sqlite3.Error) as exc:
            clear_pending_download_import()
            message = f"取り込みに失敗しました: {exc}"
            set_last_download_import_result("error", message)
            return {"matched": True, "status": "error", "message": message}
        clear_pending_download_import()
        set_last_download_import_result("complete", f"{result['filename']} was imported automatically.", **result)
        start_background_scan(force=True)
        return {"matched": True, **result}


def _process_pending_download_import() -> None:
    global PENDING_DOWNLOAD_IMPORT

    with PENDING_DOWNLOAD_IMPORT_LOCK:
        pending = dict(PENDING_DOWNLOAD_IMPORT) if isinstance(PENDING_DOWNLOAD_IMPORT, dict) else None

    if not pending:
        return

    if monotonic() - float(pending.get("armed_monotonic") or 0.0) > PENDING_IMPORT_TTL_MS / 1000:
        clear_pending_download_import()
        set_last_download_import_result("error", "Timed out while waiting for a downloaded model file.")
        return

    if download_resource_key(pending.get("metadata", {}).get("download_url", "")):
        return
    candidate = find_pending_download_candidate(pending)
    if candidate is None:
        return

    path_key = str(candidate["path"].resolve(strict=False))
    current_signature = {"size": candidate["size"], "mtime": candidate["mtime"], "first_seen": monotonic()}

    with PENDING_DOWNLOAD_IMPORT_LOCK:
        if not isinstance(PENDING_DOWNLOAD_IMPORT, dict):
            return
        seen_files = dict(PENDING_DOWNLOAD_IMPORT.get("seen_files") or {})
        previous_signature = seen_files.get(path_key)
        if previous_signature and previous_signature.get("size") == current_signature["size"] and previous_signature.get("mtime") == current_signature["mtime"]:
            current_signature["first_seen"] = float(previous_signature.get("first_seen") or monotonic())
        seen_files[path_key] = current_signature
        PENDING_DOWNLOAD_IMPORT["seen_files"] = seen_files

    if monotonic() - float(current_signature["first_seen"]) < PENDING_IMPORT_SETTLE_SECONDS:
        return

    try:
        result = import_downloaded_lora(
            str(candidate["path"]),
            pending.get("metadata", {}),
            normalize_text(pending.get("target_dir")),
        )
    except Exception as exc:
        clear_pending_download_import()
        set_last_download_import_result("error", str(exc) or "Downloaded file could not be imported")
        return

    watch_dirs = load_lora_config()["watch_dirs"]
    item_snapshot = None
    result_path_text = normalize_text(result.get("path"))
    if result_path_text:
        try:
            item_snapshot = build_lora_item_snapshot(Path(result_path_text), watch_dirs, cache_bust=str(time_ns()))
        except Exception:
            item_snapshot = None

    clear_pending_download_import()
    set_last_download_import_result(
        "complete",
        f"{result.get('filename') or candidate['name']} was imported automatically.",
        imported_count=result.get("imported_count", 1),
        workflow_paths=result.get("workflow_paths", []),
        path=result.get("path", ""),
        filename=result.get("filename", candidate["name"]),
        target_dir=result.get("target_dir", ""),
        item=item_snapshot,
    )
    start_background_scan(force=True)


def empty_library_payload(config: dict | None = None) -> dict:
    current_config = config or load_lora_config()
    return {
        "config": current_config,
        "warnings": [],
        "scanned_at": "",
        "scan_duration_ms": 0,
        "stats": {
            "total": 0,
            "favorites": 0,
            "with_preview": 0,
            "categories": {},
            "families": {},
        },
        "items": [],
    }


def normalize_watch_dirs(raw_dirs: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw in raw_dirs or []:
        text = str(raw).strip()
        if not text:
            continue

        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = (APP_ROOT / candidate).resolve(strict=False)
        else:
            candidate = candidate.resolve(strict=False)

        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(str(candidate))

    return normalized


def load_lora_config() -> dict:
    data = read_json_file(LORA_CONFIG_PATH, {"watch_dirs": []})
    watch_dirs = normalize_watch_dirs(data.get("watch_dirs", []))
    return {"watch_dirs": watch_dirs}


def save_lora_config(payload: dict) -> dict:
    config = {"watch_dirs": normalize_watch_dirs(payload.get("watch_dirs", []))}
    write_json_file(LORA_CONFIG_PATH, config)
    return config


def get_db_connection() -> sqlite3.Connection:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(LORA_DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


@contextmanager
def db_connection():
    connection = get_db_connection()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_lora_db() -> None:
    with db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS lora_metadata (
                path TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                favorite INTEGER NOT NULL DEFAULT 0,
                rating INTEGER NOT NULL DEFAULT 0 CHECK(rating BETWEEN 0 AND 5),
                category TEXT NOT NULL DEFAULT '',
                triggers_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                notes TEXT NOT NULL DEFAULT '',
                recommended_steps TEXT NOT NULL DEFAULT '',
                recommended_sampler TEXT NOT NULL DEFAULT '',
                recommended_scheduler TEXT NOT NULL DEFAULT '',
                generation_settings_json TEXT NOT NULL DEFAULT '{}',
                author TEXT NOT NULL DEFAULT '',
                base_model TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                strength_min REAL,
                strength_max REAL,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(lora_metadata)")}
        if "rating" not in columns:
            connection.execute(
                "ALTER TABLE lora_metadata ADD COLUMN rating INTEGER NOT NULL DEFAULT 0 CHECK(rating BETWEEN 0 AND 5)"
            )
        for key in ("recommended_steps", "recommended_sampler", "recommended_scheduler"):
            if key not in columns:
                connection.execute(f"ALTER TABLE lora_metadata ADD COLUMN {key} TEXT NOT NULL DEFAULT ''")
        if "generation_settings_json" not in columns:
            connection.execute("ALTER TABLE lora_metadata ADD COLUMN generation_settings_json TEXT NOT NULL DEFAULT '{}'")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS browser_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                source_host TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                base_model TEXT NOT NULL DEFAULT '',
                triggers_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
    migrate_legacy_store()


def get_scan_state_payload() -> dict:
    with SCAN_STATE_LOCK:
        config = SCAN_STATE["config"] or load_lora_config()
        base_result = SCAN_STATE["result"] or empty_library_payload(config)
        payload = dict(base_result)
        payload["config"] = config
        payload["revision"] = SCAN_STATE["revision"]
        payload["status"] = SCAN_STATE["status"]
        payload["scan_started_at"] = SCAN_STATE["started_at"]
        payload["scan_finished_at"] = SCAN_STATE["finished_at"]
        payload["scan_error"] = SCAN_STATE["error"]
        return payload


def _complete_background_scan(config: dict, revision: int) -> None:
    try:
        result = scan_lora_library(config)
        status = "ready"
        error = ""
    except Exception as exc:
        result = empty_library_payload(config)
        status = "error"
        error = str(exc)

    finished_at = utc_now_iso()
    # A rating can be saved while the filesystem scan is running. Refresh it
    # before publishing, under the same lock used by metadata writes.
    with METADATA_WRITE_LOCK:
        if status == "ready":
            try:
                result["items"] = apply_saved_ratings_and_settings_to_items(result.get("items", []), load_lora_store())
            except Exception as exc:
                result = empty_library_payload(config)
                status = "error"
                error = str(exc)
        with SCAN_STATE_LOCK:
            if revision != SCAN_STATE["revision"]:
                return
            SCAN_STATE["status"] = status
            SCAN_STATE["finished_at"] = finished_at
            SCAN_STATE["error"] = error
            SCAN_STATE["config"] = config
            SCAN_STATE["result"] = result


def start_background_scan(config: dict | None = None, force: bool = False) -> dict:
    current_config = config or load_lora_config()
    should_return_current = False

    with SCAN_STATE_LOCK:
        same_config = SCAN_STATE["config"].get("watch_dirs", []) == current_config.get("watch_dirs", [])
        if not force and SCAN_STATE["status"] == "scanning" and same_config:
            should_return_current = True
        elif not force and SCAN_STATE["status"] == "ready" and same_config:
            should_return_current = True
        else:
            SCAN_STATE["revision"] += 1
            revision = SCAN_STATE["revision"]
            SCAN_STATE["status"] = "scanning"
            SCAN_STATE["started_at"] = utc_now_iso()
            SCAN_STATE["finished_at"] = ""
            SCAN_STATE["error"] = ""
            SCAN_STATE["config"] = current_config
            SCAN_STATE["result"] = empty_library_payload(current_config)

    if should_return_current:
        return get_scan_state_payload()

    scan_thread = threading.Thread(
        target=_complete_background_scan,
        args=(current_config, revision),
        daemon=True,
        name=f"lora-scan-{revision}",
    )
    scan_thread.start()
    return get_scan_state_payload()


def build_watch_dir_snapshot(config: dict | None = None) -> tuple:
    current_config = config or load_lora_config()
    snapshot_entries: list[tuple[str, int, int]] = []

    for directory in current_config.get("watch_dirs", []):
        root = Path(directory)
        if not root.exists() or not root.is_dir():
            snapshot_entries.append((f"!missing:{Path(directory).resolve(strict=False)}", 0, 0))
            continue

        for file_path in iter_model_files(root, []):
            try:
                stat = file_path.stat()
            except OSError:
                continue
            snapshot_entries.append(
                (
                    str(file_path.resolve()),
                    int(stat.st_size),
                    int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
                )
            )

    snapshot_entries.sort()
    return tuple(snapshot_entries)


def is_scan_running_for_config(config: dict) -> bool:
    watch_dirs = config.get("watch_dirs", [])
    with SCAN_STATE_LOCK:
        return SCAN_STATE["status"] == "scanning" and SCAN_STATE["config"].get("watch_dirs", []) == watch_dirs


def watch_lora_library_changes(stop_event: threading.Event | None = None) -> None:
    active_config_key: tuple[str, ...] | None = None
    emitted_snapshot: tuple | None = None
    pending_snapshot: tuple | None = None
    pending_since = 0.0
    last_snapshot_check = 0.0

    while True:
        try:
            pending_import_active = has_pending_download_import()
            wait_seconds = PENDING_IMPORT_POLL_SECONDS if pending_import_active else WATCH_POLL_SECONDS
            if stop_event and stop_event.wait(wait_seconds):
                return

            if pending_import_active:
                process_pending_download_import()

            now = monotonic()
            if pending_import_active and now - last_snapshot_check < WATCH_POLL_SECONDS:
                continue

            config = load_lora_config()
            config_key = tuple(config.get("watch_dirs", []))

            # Let the active library scan finish before doing another full
            # filesystem walk for change detection.
            if is_scan_running_for_config(config):
                active_config_key = config_key
                pending_snapshot = None
                pending_since = 0.0
                continue

            last_snapshot_check = now
            snapshot = build_watch_dir_snapshot(config)

            if active_config_key != config_key:
                active_config_key = config_key
                emitted_snapshot = snapshot
                pending_snapshot = None
                pending_since = 0.0
                continue

            if emitted_snapshot is None:
                emitted_snapshot = snapshot
                continue

            if snapshot == emitted_snapshot:
                pending_snapshot = None
                pending_since = 0.0
                continue

            if pending_snapshot != snapshot:
                pending_snapshot = snapshot
                pending_since = monotonic()
                continue

            if monotonic() - pending_since < WATCH_SETTLE_SECONDS:
                continue

            if is_scan_running_for_config(config):
                continue

            start_background_scan(config=config, force=True)
            emitted_snapshot = snapshot
            pending_snapshot = None
            pending_since = 0.0
        except Exception as exc:
            print(f"Watch monitor error: {exc.__class__.__name__}: {exc}", flush=True)
            if stop_event and stop_event.wait(max(PENDING_IMPORT_POLL_SECONDS, 0.25)):
                return

def start_watch_dir_monitor() -> None:
    global WATCHER_THREAD_STARTED
    with WATCHER_THREAD_LOCK:
        should_start_watcher = not WATCHER_THREAD_STARTED
        WATCHER_THREAD_STARTED = True

    if should_start_watcher:
        thread = threading.Thread(
            target=watch_lora_library_changes,
            daemon=True,
            name="lora-watch-monitor",
        )
        thread.start()


def encode_json_list(values: list[str]) -> str:
    return json.dumps(normalize_string_list(values), ensure_ascii=False)


def decode_json_list(raw_value) -> list[str]:
    if raw_value in (None, ""):
        return []
    if isinstance(raw_value, list):
        return normalize_string_list(raw_value)
    try:
        parsed = json.loads(str(raw_value))
    except json.JSONDecodeError:
        return normalize_string_list(raw_value)
    return normalize_string_list(parsed)


def normalize_metadata_input(payload: dict, *, preserve_updated_at: bool = False) -> dict:
    metadata: dict = {}

    if "display_name" in payload:
        metadata["display_name"] = normalize_text(payload.get("display_name"))
    if "favorite" in payload:
        metadata["favorite"] = bool(payload.get("favorite"))
    if "rating" in payload:
        rating = payload["rating"]
        if type(rating) is not int or not 0 <= rating <= 5:
            raise ValueError("評価は1〜5の整数、未評価は0で指定してください。")
        metadata["rating"] = rating
    if "category" in payload:
        metadata["category"] = normalize_text(payload.get("category"))
    if "triggers" in payload:
        metadata["triggers"] = normalize_string_list(payload.get("triggers"))
    if "tags" in payload:
        metadata["tags"] = normalize_string_list(payload.get("tags"))
    if "notes" in payload:
        metadata["notes"] = normalize_text(payload.get("notes"))
    for key in ("recommended_steps", "recommended_sampler", "recommended_scheduler"):
        if key in payload:
            metadata[key] = normalize_text(payload.get(key))
    if "generation_settings" in payload:
        metadata["generation_settings"] = forge_connector.normalize_generation_settings(payload["generation_settings"])
    if "author" in payload:
        metadata["author"] = normalize_text(payload.get("author"))
    if "base_model" in payload:
        metadata["base_model"] = normalize_text(payload.get("base_model"))
    if "source_url" in payload:
        metadata["source_url"] = normalize_text(payload.get("source_url"))
    if "strength_min" in payload:
        metadata["strength_min"] = normalize_optional_float(payload.get("strength_min"))
    if "strength_max" in payload:
        metadata["strength_max"] = normalize_optional_float(payload.get("strength_max"))

    lower = metadata.get("strength_min")
    upper = metadata.get("strength_max")
    if lower is not None and upper is not None and lower > upper:
        metadata["strength_min"], metadata["strength_max"] = upper, lower

    if preserve_updated_at and "updated_at" in payload:
        metadata["updated_at"] = normalize_text(payload.get("updated_at"))
    else:
        metadata["updated_at"] = utc_now_iso()
    return metadata


def metadata_to_db_row(path_key: str, metadata: dict) -> tuple:
    return (
        path_key,
        normalize_text(metadata.get("display_name")),
        1 if bool(metadata.get("favorite")) else 0,
        metadata.get("rating", 0),
        normalize_text(metadata.get("category")),
        encode_json_list(metadata.get("triggers", [])),
        encode_json_list(metadata.get("tags", [])),
        normalize_text(metadata.get("notes")),
        normalize_text(metadata.get("recommended_steps")),
        normalize_text(metadata.get("recommended_sampler")),
        normalize_text(metadata.get("recommended_scheduler")),
        json.dumps(metadata.get("generation_settings", {}), ensure_ascii=False),
        normalize_text(metadata.get("author")),
        normalize_text(metadata.get("base_model")),
        normalize_text(metadata.get("source_url")),
        normalize_optional_float(metadata.get("strength_min")),
        normalize_optional_float(metadata.get("strength_max")),
        normalize_text(metadata.get("updated_at")),
    )


def row_to_metadata(row: sqlite3.Row | dict) -> dict:
    return {
        "display_name": normalize_text(row["display_name"]),
        "favorite": bool(row["favorite"]),
        "rating": int(row["rating"]),
        "category": normalize_text(row["category"]),
        "triggers": decode_json_list(row["triggers_json"]),
        "tags": decode_json_list(row["tags_json"]),
        "notes": normalize_text(row["notes"]),
        "recommended_steps": normalize_text(row["recommended_steps"]),
        "recommended_sampler": normalize_text(row["recommended_sampler"]),
        "recommended_scheduler": normalize_text(row["recommended_scheduler"]),
        "generation_settings": json.loads(row["generation_settings_json"]),
        "author": normalize_text(row["author"]),
        "base_model": normalize_text(row["base_model"]),
        "source_url": normalize_text(row["source_url"]),
        "strength_min": normalize_optional_float(row["strength_min"]),
        "strength_max": normalize_optional_float(row["strength_max"]),
        "updated_at": normalize_text(row["updated_at"]),
    }


def upsert_metadata_records(records: dict[str, dict]) -> None:
    if not records:
        return

    rows = [metadata_to_db_row(path_key, metadata) for path_key, metadata in records.items()]
    with db_connection() as connection:
        connection.executemany(
            """
            INSERT INTO lora_metadata (
                path,
                display_name,
                favorite,
                rating,
                category,
                triggers_json,
                tags_json,
                notes,
                recommended_steps,
                recommended_sampler,
                recommended_scheduler,
                generation_settings_json,
                author,
                base_model,
                source_url,
                strength_min,
                strength_max,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                display_name=excluded.display_name,
                favorite=excluded.favorite,
                rating=excluded.rating,
                category=excluded.category,
                triggers_json=excluded.triggers_json,
                tags_json=excluded.tags_json,
                notes=excluded.notes,
                recommended_steps=excluded.recommended_steps,
                recommended_sampler=excluded.recommended_sampler,
                recommended_scheduler=excluded.recommended_scheduler,
                generation_settings_json=excluded.generation_settings_json,
                author=excluded.author,
                base_model=excluded.base_model,
                source_url=excluded.source_url,
                strength_min=excluded.strength_min,
                strength_max=excluded.strength_max,
                updated_at=excluded.updated_at
            """,
            rows,
        )


def load_lora_store(paths: list[str] | None = None) -> dict[str, dict]:
    init_lora_db()
    with db_connection() as connection:
        if paths:
            placeholders = ", ".join("?" for _ in paths)
            cursor = connection.execute(
                f"SELECT * FROM lora_metadata WHERE path IN ({placeholders})",
                paths,
            )
        else:
            cursor = connection.execute("SELECT * FROM lora_metadata")
        return {str(row["path"]): row_to_metadata(row) for row in cursor.fetchall()}


def merge_metadata_changes(path_keys: list[str], changes: dict) -> dict[str, dict]:
    with METADATA_WRITE_LOCK:
        return _merge_metadata_changes(path_keys, changes)


def _merge_metadata_changes(path_keys: list[str], changes: dict) -> dict[str, dict]:
    existing_map = load_lora_store(path_keys)
    updates: dict[str, dict] = {}

    for path_key in path_keys:
        existing = existing_map.get(path_key, {})
        merged = existing | changes
        updates[path_key] = merged

    upsert_metadata_records(updates)
    if any(key in changes for key in ("rating", "recommended_steps", "recommended_sampler", "recommended_scheduler", "generation_settings")):
        with SCAN_STATE_LOCK:
            result = SCAN_STATE["result"]
            if result is not None:
                SCAN_STATE["result"] = result | {
                    "items": apply_saved_ratings_and_settings_to_items(result.get("items", []), updates)
                }
                # Keep the scan revision: this small edit must not trigger a
                # full form refresh in clients with unsaved detail text.
    return updates


def apply_saved_ratings_and_settings_to_items(items: list[dict], metadata: dict[str, dict]) -> list[dict]:
    return [
        item | {
            "rating": metadata[item["path"]].get("rating", 0),
            "recommended_steps": metadata[item["path"]].get("recommended_steps", ""),
            "recommended_sampler": metadata[item["path"]].get("recommended_sampler", ""),
            "recommended_scheduler": metadata[item["path"]].get("recommended_scheduler", ""),
            "generation_settings_summary": generation_settings_summary(metadata[item["path"]].get("generation_settings", {})),
            "updated_at": metadata[item["path"]].get("updated_at") or item.get("updated_at", ""),
        } if item["path"] in metadata else item
        for item in items
    ]


def delete_metadata_records(path_keys: list[str]) -> None:
    if not path_keys:
        return

    with db_connection() as connection:
        placeholders = ", ".join("?" for _ in path_keys)
        connection.execute(
            f"DELETE FROM lora_metadata WHERE path IN ({placeholders})",
            path_keys,
        )


def delete_lora_file(target: Path) -> str:
    resolved = target.resolve()
    resolved.unlink()
    delete_metadata_records([str(resolved)])
    return str(resolved)


def normalize_browser_import(payload: dict) -> dict:
    source_url = normalize_text(payload.get("source_url") or payload.get("url"))
    source_host = normalize_text(urlparse(source_url).netloc) if source_url else ""
    prompt_preview = normalize_multiline_text(payload.get("prompt_preview") or payload.get("prompt"))

    return {
        "created_at": utc_now_iso(),
        "source_url": source_url,
        "source_host": source_host,
        "title": normalize_text(payload.get("title") or payload.get("source_name") or payload.get("name")),
        "description": normalize_multiline_text(payload.get("description") or payload.get("source_description") or prompt_preview),
        "author": normalize_text(payload.get("author")),
        "base_model": normalize_text(payload.get("base_model") or payload.get("baseModel")),
        "triggers": normalize_string_list(payload.get("triggers") or payload.get("trainedWords")),
        "tags": normalize_string_list(payload.get("tags")),
        "payload": payload if isinstance(payload, dict) else {},
    }


def row_to_browser_import(row: sqlite3.Row | dict) -> dict:
    payload = read_json_file_payload(row["payload_json"])
    prompt_meta = build_browser_import_prompt_metadata(payload)
    return {
        "id": int(row["id"]),
        "created_at": normalize_text(row["created_at"]),
        "source_url": normalize_text(row["source_url"]),
        "source_host": normalize_text(row["source_host"]),
        "title": normalize_text(row["title"]),
        "description": normalize_text(row["description"]),
        "author": normalize_text(row["author"]),
        "base_model": normalize_text(row["base_model"]),
        "triggers": decode_json_list(row["triggers_json"]),
        "tags": decode_json_list(row["tags_json"]),
        "has_prompt": bool(prompt_meta.get("has_prompt")),
        "prompt": prompt_meta.get("prompt", ""),
        "negative_prompt": prompt_meta.get("negative_prompt", ""),
        "prompt_preview": prompt_meta.get("prompt_preview", ""),
        "steps": prompt_meta.get("steps", ""),
        "sampler": prompt_meta.get("sampler", ""),
        "cfg_scale": prompt_meta.get("cfg_scale", ""),
        "seed": prompt_meta.get("seed", ""),
        "resources_used": prompt_meta.get("resources_used", []),
        "payload": payload,
    }


def append_import_description_to_notes(existing_notes: str, description: str, source_url: str = "") -> str:
    notes = normalize_multiline_text(existing_notes)
    description_text = normalize_multiline_text(description)
    if not description_text:
        return notes

    host = normalize_text(urlparse(source_url).netloc).lower()
    label = "Civitaiメモ" if is_civitai_page_host(host) else "取得メモ"
    note_block = f"{label}\n{description_text}"
    if note_block in notes or description_text in notes:
        return notes
    if not notes:
        return note_block
    return f"{notes}\n\n{note_block}"


def read_json_file_payload(raw_value):
    if raw_value in (None, ""):
        return {}
    if isinstance(raw_value, dict):
        return raw_value
    try:
        parsed = json.loads(str(raw_value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def store_browser_import(payload: dict) -> dict:
    init_lora_db()
    normalized = normalize_browser_import(payload)
    with db_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO browser_imports (
                created_at,
                source_url,
                source_host,
                title,
                description,
                author,
                base_model,
                triggers_json,
                tags_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["created_at"],
                normalized["source_url"],
                normalized["source_host"],
                normalized["title"],
                normalized["description"],
                normalized["author"],
                normalized["base_model"],
                encode_json_list(normalized["triggers"]),
                encode_json_list(normalized["tags"]),
                json.dumps(normalized["payload"], ensure_ascii=False),
            ),
        )
        row = connection.execute("SELECT * FROM browser_imports WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_to_browser_import(row)


def load_browser_imports(limit: int = 20) -> list[dict]:
    init_lora_db()
    with db_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM browser_imports ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [row_to_browser_import(row) for row in rows]


def delete_browser_import(import_id: int) -> bool:
    init_lora_db()
    with db_connection() as connection:
        cursor = connection.execute("DELETE FROM browser_imports WHERE id = ?", (int(import_id),))
    return cursor.rowcount > 0


def get_browser_import(import_id: int) -> dict | None:
    init_lora_db()
    with db_connection() as connection:
        row = connection.execute("SELECT * FROM browser_imports WHERE id = ?", (int(import_id),)).fetchone()
    return row_to_browser_import(row) if row else None


def apply_browser_import_to_path(import_id: int, path_key: str, consume: bool = False) -> dict:
    import_record = get_browser_import(import_id)
    if not import_record:
        raise KeyError("Browser import not found")

    existing = load_lora_store([path_key]).get(path_key, {})
    changes: dict = {}

    if import_record["author"]:
        changes["author"] = import_record["author"]
    if import_record["base_model"]:
        changes["base_model"] = import_record["base_model"]
    if import_record["source_url"]:
        changes["source_url"] = import_record["source_url"]
    if import_record["triggers"]:
        changes["triggers"] = dedupe_keep_order(existing.get("triggers", []) + import_record["triggers"])
    if import_record["tags"]:
        changes["tags"] = dedupe_keep_order(existing.get("tags", []) + import_record["tags"])
    merged_notes = append_import_description_to_notes(
        existing.get("notes", ""),
        import_record.get("description", ""),
        import_record.get("source_url", ""),
    )
    if merged_notes:
        changes["notes"] = merged_notes

    if not changes:
        updated = existing
    else:
        normalized = sanitize_metadata_input(changes)
        updated = merge_metadata_changes([path_key], normalized).get(path_key, existing)

    preview_url = resolve_preview_media_url(import_record.get("payload", {}))
    if preview_url:
        download_preview_image_for_model(Path(path_key), preview_url, import_record["source_url"])

    if consume:
        delete_browser_import(import_id)

    return updated


def build_library_item_snapshot(path_key: str) -> dict:
    watch_dirs = load_lora_config()["watch_dirs"]
    model_path = Path(path_key).resolve(strict=False)
    if not is_valid_model_path(model_path, watch_dirs):
        raise FileNotFoundError("Model file not found")

    directory_index = build_directory_index(model_path.parent)
    store_meta = load_lora_store([str(model_path)]).get(str(model_path), {})
    return build_lora_item(model_path, watch_dirs, store_meta, directory_index)


def is_civitai_image_page_url(source_url: str) -> bool:
    url = normalize_text(source_url)
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return is_civitai_page_host(host) and bool(re.match(r"^/images/\d+(?:/|$)", parsed.path))


def build_browser_import_prompt_metadata(payload: dict | None = None) -> dict:
    data = payload if isinstance(payload, dict) else {}
    metadata_source = normalize_text(data.get("metadata_source"))
    if not metadata_source and is_civitai_image_page_url(data.get("source_url") or data.get("url")):
        metadata_source = "civitai.image.generation"
    if not metadata_source:
        metadata_source = "browser.import"

    return normalize_reference_prompt_metadata(
        {
            "metadata_source": metadata_source,
            "prompt": data.get("prompt"),
            "negative_prompt": data.get("negative_prompt"),
            "raw_parameters": data.get("raw_parameters"),
            "prompt_json": data.get("prompt_json"),
            "workflow_json": data.get("workflow_json"),
            "source_url": data.get("source_url") or data.get("url"),
            "source_name": data.get("title") or data.get("source_name") or data.get("name"),
            "source_description": data.get("description") or data.get("source_description"),
            "source_host": data.get("source_host"),
            "author": data.get("author"),
            "base_model": data.get("base_model") or data.get("baseModel"),
            "steps": data.get("steps"),
            "sampler": data.get("sampler"),
            "cfg_scale": data.get("cfg_scale") or data.get("cfgScale") or data.get("cfg"),
            "seed": data.get("seed"),
            "resources_used": data.get("resources_used") or data.get("resources"),
        }
    )


def build_reference_filename_from_browser_import(payload: dict | None, preview_url: str, content_type: str = "") -> str:
    data = payload if isinstance(payload, dict) else {}
    source_url = normalize_text(data.get("source_url") or data.get("url"))
    parsed_preview = urlparse(normalize_text(preview_url))
    extension = guess_reference_media_extension(preview_url, content_type)

    match = re.match(r"^/images/(\d+)(?:/|$)", urlparse(source_url).path)
    if match:
        return f"civitai-image-{match.group(1)}{extension}"

    explicit_name = normalize_text(data.get("reference_filename") or data.get("title"))
    if explicit_name:
        if normalize_reference_extension(Path(explicit_name).suffix.lower()) not in REFERENCE_MEDIA_EXTENSIONS:
            explicit_name = f"{explicit_name}{extension}"
        return sanitize_uploaded_reference_name(explicit_name, content_type)

    fallback_name = Path(parsed_preview.path).name or f"reference{extension}"
    return sanitize_uploaded_reference_name(fallback_name, content_type)


def save_reference_image_from_url(
    model_path: Path,
    preview_url: str,
    prompt_metadata: dict | None = None,
    referer_url: str = "",
    preferred_filename: str = "",
) -> Path:
    media_bytes, content_type = fetch_reference_media_bytes(preview_url, referer_url)
    reference_dir = reference_folder_for_model(model_path)
    reference_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_uploaded_reference_name(
        preferred_filename or Path(urlparse(preview_url).path).name or "reference",
        content_type,
    )
    target_path = build_unique_media_path(reference_dir, safe_name)
    target_path.write_bytes(media_bytes)

    metadata = normalize_reference_prompt_metadata(prompt_metadata)
    if reference_media_kind(target_path.name, content_type) == "image" and not metadata.get("has_prompt"):
        extracted = extract_reference_prompt_metadata_from_bytes(media_bytes, target_path.name, content_type)
        metadata = normalize_reference_prompt_metadata(metadata | extracted)
    save_reference_prompt_metadata(target_path, metadata)
    return target_path


def add_browser_import_reference_to_path(
    import_id: int,
    path_key: str,
    consume: bool = False,
    include_items: bool = True,
):
    import_record = get_browser_import(import_id)
    if not import_record:
        raise KeyError("Browser import not found")

    payload = import_record.get("payload", {})
    preview_url = resolve_preview_media_url(payload)
    if not preview_url:
        raise ValueError("Reference media URL was not found in the browser import")

    preferred_filename = build_reference_filename_from_browser_import(payload, preview_url)
    prompt_metadata = build_browser_import_prompt_metadata(payload)
    saved_path = save_reference_image_from_url(
        Path(path_key),
        preview_url,
        prompt_metadata,
        import_record["source_url"],
        preferred_filename,
    )
    encyclopedia_queued = enqueue_encyclopedia_export(saved_path, prompt_metadata)
    if consume:
        delete_browser_import(import_id)

    model_path = Path(path_key)
    if include_items:
        items = build_reference_image_items(model_path)
        return items

    return {
        "count": len(list_reference_image_paths(model_path)),
        "items": [],
        "encyclopedia_queued": 1 if encyclopedia_queued else 0,
    }


def migrate_legacy_store() -> None:
    if not LEGACY_LORA_STORE_PATH.exists():
        return

    legacy_store = read_json_file(LEGACY_LORA_STORE_PATH, {})
    if not isinstance(legacy_store, dict) or not legacy_store:
        return

    with db_connection() as connection:
        existing_count = connection.execute("SELECT COUNT(*) FROM lora_metadata").fetchone()[0]
    if existing_count:
        return

    migrated: dict[str, dict] = {}
    for path_key, metadata in legacy_store.items():
        if not isinstance(metadata, dict):
            continue
        migrated[str(path_key)] = normalize_metadata_input(metadata, preserve_updated_at=True)

    upsert_metadata_records(migrated)


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def normalize_string_list(value) -> list[str]:
    if value is None:
        return []

    raw_values: list[str] = []
    if isinstance(value, (list, tuple, set)):
        for entry in value:
            if entry is None:
                continue
            raw_values.extend(normalize_string_list(entry))
    else:
        raw_values = re.split(r"[\n,;]+", str(value))

    cleaned = [entry.strip() for entry in raw_values if str(entry).strip()]
    return dedupe_keep_order(cleaned)


def normalize_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_multiline_text(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def looks_like_decoded_metadata_text(text: str) -> bool:
    normalized = normalize_multiline_text(text)
    if not normalized:
        return False

    lowered = normalized.lower()
    if normalized.startswith(("{", "[", "<")):
        return True
    if any(marker in lowered for marker in ("negative prompt:", "\nsteps:", "steps:", "resource-stack", "urn:air:", "<x:xmpmeta")):
        return True

    printable_ascii = sum(1 for char in normalized if char in "\r\n\t" or 32 <= ord(char) < 127)
    return printable_ascii >= max(8, int(len(normalized) * 0.55))


def repair_endian_swapped_utf16_text(text: str) -> str:
    normalized = normalize_multiline_text(text)
    if not normalized:
        return ""

    compact = "".join(char for char in normalized if not char.isspace())
    if len(compact) < 8:
        return normalized

    swapped_ratio = sum(1 for char in compact if ord(char) > 0xFF and (ord(char) & 0x00FF) == 0) / max(len(compact), 1)
    if swapped_ratio < 0.6:
        return normalized

    try:
        repaired = normalized.encode("utf-16-be").decode("utf-16-le")
    except UnicodeError:
        return normalized

    repaired = normalize_multiline_text(repaired.replace("\x00", ""))
    if not looks_like_decoded_metadata_text(repaired):
        return normalized
    return repaired


def score_decoded_metadata_text(text: str) -> tuple[int, int, int]:
    normalized = repair_endian_swapped_utf16_text(text.replace("\x00", ""))
    lowered = normalized.lower()
    marker_score = 0
    if normalized.startswith(("{", "[", "<")):
        marker_score += 5
    for marker in ("negative prompt:", "steps:", "resource-stack", "urn:air:", "<x:xmpmeta", "\"class_type\"", "\"inputs\""):
        if marker in lowered:
            marker_score += 3

    printable_ascii = sum(1 for char in normalized if char in "\r\n\t" or 32 <= ord(char) < 127)
    return marker_score, printable_ascii, len(normalized)


def decode_metadata_text_bytes(raw_value) -> str:
    if raw_value in (None, b"", ""):
        return ""

    raw_bytes = bytes(raw_value if not isinstance(raw_value, str) else raw_value.encode("utf-8", errors="ignore"))
    if not raw_bytes:
        return ""

    null_even = sum(1 for entry in raw_bytes[0::2] if entry == 0)
    null_odd = sum(1 for entry in raw_bytes[1::2] if entry == 0)
    likely_utf16 = raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")) or (null_even + null_odd) >= max(4, len(raw_bytes) // 8)

    encodings: list[str] = []
    if raw_bytes.startswith(b"\xff\xfe"):
        encodings.append("utf-16-le")
    if raw_bytes.startswith(b"\xfe\xff"):
        encodings.append("utf-16-be")
    if likely_utf16:
        if null_odd > null_even:
            encodings.extend(["utf-16-le", "utf-16"])
        elif null_even > null_odd:
            encodings.extend(["utf-16-be", "utf-16"])
        else:
            encodings.extend(["utf-16", "utf-16-le", "utf-16-be"])
    encodings.extend(["utf-8", "utf-16-le", "utf-16-be", "latin-1"])

    best_text = ""
    best_score = (-1, -1, -1)
    for encoding in dedupe_keep_order(encodings):
        try:
            decoded = raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        candidate = repair_endian_swapped_utf16_text(decoded.replace("\x00", ""))
        score = score_decoded_metadata_text(candidate)
        if score > best_score:
            best_text = candidate
            best_score = score

    return normalize_multiline_text(best_text)


def normalize_optional_float(value):
    if value in (None, ""):
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def parse_strength_range(raw_value: str) -> tuple[float | None, float | None]:
    numbers = re.findall(r"\d+(?:\.\d+)?", raw_value)
    if not numbers:
        return None, None
    if len(numbers) == 1:
        value = normalize_optional_float(numbers[0])
        return value, value

    lower = normalize_optional_float(numbers[0])
    upper = normalize_optional_float(numbers[1])
    if lower is not None and upper is not None and lower > upper:
        lower, upper = upper, lower
    return lower, upper


def format_weight_value(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".")


def guess_category(model_path: Path) -> str:
    parts = [part.lower() for part in model_path.parts[-5:]]
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(any(keyword in part for keyword in keywords) for part in parts):
            return category
    return ""


def parse_sectioned_sidecar(text: str) -> dict:
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    buffer: list[str] = []

    def commit() -> None:
        if current_section is None:
            return
        sections[current_section] = buffer.copy()

    for line in text.splitlines():
        stripped = line.strip()
        match = re.fullmatch(r"\[(.+?)\]", stripped)
        if match:
            commit()
            current_section = match.group(1).strip().lower()
            buffer = []
            continue
        if current_section is not None:
            buffer.append(line.rstrip())

    commit()

    if not sections:
        notes = text.strip()
        return {"notes": notes} if notes else {}

    metadata: dict = {}
    for key, lines in sections.items():
        value = "\n".join(lines).strip()
        if not value:
            continue

        if key in {"trigger", "triggers"}:
            metadata["triggers"] = normalize_string_list(value)
        elif key in {"tags", "tag"}:
            metadata["tags"] = normalize_string_list(value)
        elif key in {"category", "type"}:
            metadata["category"] = value
        elif key in {"strength", "weight"}:
            strength_min, strength_max = parse_strength_range(value)
            if strength_min is not None:
                metadata["strength_min"] = strength_min
            if strength_max is not None:
                metadata["strength_max"] = strength_max
        elif key in {"notes", "memo"}:
            metadata["notes"] = value
        elif key == "author":
            metadata["author"] = value
        elif key in {"base_model", "base"}:
            metadata["base_model"] = value
        elif key in {"source", "source_url", "url"}:
            metadata["source_url"] = value
        elif key in {"display_name", "name"}:
            metadata["display_name"] = value

    return metadata


def strip_html_tags(text: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", without_tags).strip()


def parse_embedded_json(raw_value):
    if isinstance(raw_value, (dict, list)):
        return raw_value
    if raw_value in (None, ""):
        return None
    try:
        return json.loads(str(raw_value))
    except json.JSONDecodeError:
        return None


def format_reference_base_model_label(value: str) -> str:
    lowered = normalize_text(value).lower().replace("-", "").replace("_", "").replace(".", "")
    if lowered in {"sdxl", "illustriousxl"}:
        return lowered.upper()
    if lowered in {"sd15", "sd1", "sd1x", "sd1_5"}:
        return "SD 1.5"
    return normalize_text(value)


GENERIC_REFERENCE_RESOURCE_HINTS = {"ckpt_name", "lora_name", "model_name", "vae_name", "clip_name"}


def iter_civitai_resource_urn_matches(raw_value, key_hint: str = ""):
    if isinstance(raw_value, dict):
        for key, value in raw_value.items():
            next_hint = normalize_text(key) or key_hint
            yield from iter_civitai_resource_urn_matches(value, next_hint)
        return

    if isinstance(raw_value, list):
        for entry in raw_value:
            yield from iter_civitai_resource_urn_matches(entry, key_hint)
        return

    text = normalize_text(raw_value)
    if not text:
        return

    match = CIVITAI_RESOURCE_URN_RE.match(text)
    if not match:
        return

    yield key_hint, {
        "model_id": match.group("model_id"),
        "model_version_id": match.group("model_version_id") or "",
        "resource_type": normalize_text(match.group("resource_type")),
        "base_model": format_reference_base_model_label(match.group("base_model")),
        "urn": text,
    }


def build_reference_resource_name(resource_type: str, model_id: str, key_hint: str = "", class_type: str = "") -> str:
    hint = normalize_text(key_hint).replace("_", " ").replace("-", " ").strip()
    if hint and normalize_text(key_hint).lower() not in GENERIC_REFERENCE_RESOURCE_HINTS:
        return hint.title()
    type_label = normalize_text(resource_type).replace("_", " ").replace("-", " ").strip().title() or "Resource"
    if class_type and normalize_text(class_type).lower() not in {"checkpointloadersimple", "loraloader", "upscalemodelloader"}:
        return normalize_text(class_type)
    if model_id:
        return f"{type_label} {model_id}"
    return type_label


def extract_reference_resources_from_resource_stack(raw_value) -> list[dict]:
    parsed = parse_embedded_json(raw_value)
    if not isinstance(parsed, dict):
        return []

    normalized: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    candidate_entries: list[dict] = []
    for value in parsed.values():
        if not isinstance(value, dict):
            continue
        if isinstance(value.get("inputs"), (dict, list)):
            candidate_entries.append(value)

    for entry in candidate_entries:
        class_type = normalize_text(entry.get("class_type") or entry.get("classType"))
        inputs = entry.get("inputs")
        for key_hint, match in iter_civitai_resource_urn_matches(inputs, class_type):
            resource_url = build_civitai_resource_url(match["model_id"], match["model_version_id"])
            dedupe_key = (match["model_id"], match["model_version_id"], match["resource_type"].lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(
                {
                    "name": build_reference_resource_name(match["resource_type"], match["model_id"], key_hint, class_type),
                    "type": normalize_text(match["resource_type"]).title(),
                    "base_model": match["base_model"],
                    "model_id": match["model_id"],
                    "model_version_id": match["model_version_id"],
                    "version_name": class_type,
                    "url": resource_url,
                }
            )

    extra_section = parsed.get("extra")
    for key_hint, match in iter_civitai_resource_urn_matches(extra_section, "extra"):
        resource_url = build_civitai_resource_url(match["model_id"], match["model_version_id"])
        dedupe_key = (match["model_id"], match["model_version_id"], match["resource_type"].lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(
            {
                "name": build_reference_resource_name(match["resource_type"], match["model_id"], key_hint),
                "type": normalize_text(match["resource_type"]).title(),
                "base_model": match["base_model"],
                "model_id": match["model_id"],
                "model_version_id": match["model_version_id"],
                "version_name": "",
                "url": resource_url,
            }
        )

    return normalized


def is_resource_stack_only_payload(raw_value) -> bool:
    parsed = parse_embedded_json(raw_value)
    if not isinstance(parsed, dict) or not parsed:
        return False
    return all(normalize_text(key).lower().startswith("resource-stack") for key in parsed.keys())


def extract_reference_details_from_json_payload(raw_value) -> dict:
    parsed = parse_embedded_json(raw_value)
    if not isinstance(parsed, dict):
        return {}

    payload: dict = {
        "resources_used": extract_reference_resources_from_resource_stack(parsed),
    }

    metadata_sources = [parsed]
    extra_metadata = parse_embedded_json(parsed.get("extraMetadata"))
    if isinstance(extra_metadata, dict):
        metadata_sources.insert(0, extra_metadata)

    for candidate in metadata_sources:
        prompt = normalize_multiline_text(candidate.get("prompt"))
        negative_prompt = normalize_multiline_text(candidate.get("negativePrompt") or candidate.get("negative_prompt"))
        steps = normalize_text(candidate.get("steps"))
        sampler = normalize_text(candidate.get("sampler"))
        cfg_scale = normalize_text(candidate.get("cfgScale") or candidate.get("cfg_scale") or candidate.get("cfg"))
        seed = normalize_text(candidate.get("seed"))
        if prompt and not payload.get("prompt"):
            payload["prompt"] = prompt
        if negative_prompt and not payload.get("negative_prompt"):
            payload["negative_prompt"] = negative_prompt
        if steps and not payload.get("steps"):
            payload["steps"] = steps
        if sampler and not payload.get("sampler"):
            payload["sampler"] = sampler
        if cfg_scale and not payload.get("cfg_scale"):
            payload["cfg_scale"] = cfg_scale
        if seed and not payload.get("seed"):
            payload["seed"] = seed

        resources = candidate.get("resources")
        if not isinstance(resources, list):
            continue
        strength_by_version: dict[str, str] = {}
        strength_by_model: dict[str, str] = {}
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            strength_text = normalize_text(resource.get("strength"))
            version_id = normalize_text(resource.get("modelVersionId") or resource.get("model_version_id"))
            model_id = normalize_text(resource.get("modelId") or resource.get("model_id"))
            if strength_text and version_id:
                strength_by_version[version_id] = strength_text
            if strength_text and model_id:
                strength_by_model[model_id] = strength_text

        updated_resources: list[dict] = []
        for entry in payload.get("resources_used", []):
            item = dict(entry)
            if not normalize_text(item.get("strength")):
                item["strength"] = (
                    strength_by_version.get(normalize_text(item.get("model_version_id")))
                    or strength_by_model.get(normalize_text(item.get("model_id")))
                    or ""
                )
            updated_resources.append(item)
        payload["resources_used"] = updated_resources

    return payload


def first_text_value(*values) -> str:
    for value in values:
        text = normalize_text(value)
        if text:
            return text
    return ""


def collect_header_tags(raw_value, limit: int = 24) -> list[str]:
    parsed = parse_embedded_json(raw_value)
    counts: Counter[str] = Counter()

    if isinstance(parsed, list):
        return normalize_string_list(parsed)[:limit]

    if isinstance(parsed, dict):
        for key, value in parsed.items():
            if isinstance(value, dict):
                for tag, score in value.items():
                    try:
                        counts[str(tag)] += float(score)
                    except (TypeError, ValueError):
                        counts[str(tag)] += 1
            elif isinstance(value, (int, float)):
                counts[str(key)] += float(value)

    if counts:
        return [tag for tag, _score in counts.most_common(limit)]
    return []


def read_safetensors_header(model_path: Path) -> dict:
    if model_path.suffix.lower() != ".safetensors":
        return {}

    try:
        with model_path.open("rb") as handle:
            header_size_raw = handle.read(8)
            if len(header_size_raw) != 8:
                return {}
            header_size = struct.unpack("<Q", header_size_raw)[0]
            if header_size <= 0 or header_size > MAX_SAFETENSORS_HEADER_BYTES:
                return {}
            header_bytes = handle.read(header_size)
    except (OSError, struct.error):
        return {}

    try:
        payload = json.loads(header_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_embedded_safetensors_metadata(model_path: Path) -> dict:
    header = read_safetensors_header(model_path)
    raw_meta = header.get("__metadata__")
    if not isinstance(raw_meta, dict):
        return {}

    description = first_text_value(
        raw_meta.get("modelspec.description"),
        raw_meta.get("description"),
    )

    tags = normalize_string_list(
        first_text_value(raw_meta.get("modelspec.tags"), raw_meta.get("ss_dataset_tags"))
    )
    triggers = collect_header_tags(raw_meta.get("ss_tag_frequency"))
    if not triggers:
        triggers = normalize_string_list(first_text_value(raw_meta.get("trainedWords"), raw_meta.get("trigger_words")))

    metadata: dict = {}
    source_name = first_text_value(
        raw_meta.get("modelspec.title"),
        raw_meta.get("ss_output_name"),
        raw_meta.get("output_name"),
        raw_meta.get("name"),
    )
    if source_name:
        metadata["source_name"] = source_name
    if triggers:
        metadata["triggers"] = triggers
    if tags:
        metadata["tags"] = tags

    author = first_text_value(
        raw_meta.get("modelspec.author"),
        raw_meta.get("author"),
    )
    if author:
        metadata["author"] = author

    base_model = first_text_value(
        raw_meta.get("ss_base_model_version"),
        raw_meta.get("ss_sd_model_name"),
        raw_meta.get("modelspec.architecture"),
        raw_meta.get("base_model"),
    )
    if base_model:
        metadata["base_model"] = base_model

    source_url = first_text_value(
        raw_meta.get("source_url"),
        raw_meta.get("modelspec.source_url"),
        raw_meta.get("modelspec.license_link"),
    )
    if source_url:
        metadata["source_url"] = source_url

    if description:
        metadata["source_description"] = strip_html_tags(description)

    strength_min, strength_max = parse_strength_range(
        first_text_value(raw_meta.get("preferred_weight"), raw_meta.get("strength"))
    )
    if strength_min is not None:
        metadata["strength_min"] = strength_min
    if strength_max is not None:
        metadata["strength_max"] = strength_max

    model_family_hint = extract_header_model_family_hint(raw_meta)
    if model_family_hint:
        metadata["model_family_hint"] = model_family_hint

    return metadata


def find_sidecar_file(model_path: Path, suffixes: list[str], directory_index: dict | None = None) -> Path | None:
    if directory_index:
        files_by_name = directory_index["files_by_name"]
        for suffix in suffixes:
            candidate = files_by_name.get(f"{model_path.stem}{suffix}".lower())
            if candidate is not None and candidate.is_file():
                return candidate
        return None

    for suffix in suffixes:
        candidate = model_path.with_name(f"{model_path.stem}{suffix}")
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def extract_json_sidecar_metadata(data: dict) -> dict:
    creator = ""
    if isinstance(data.get("creator"), dict):
        creator = normalize_text(data["creator"].get("username"))
    if not creator and isinstance(data.get("model"), dict):
        model_info = data["model"]
        if isinstance(model_info.get("creator"), dict):
            creator = normalize_text(model_info["creator"].get("username"))

    trained_words = normalize_string_list(
        data.get("trainedWords")
        or data.get("triggerWords")
        or data.get("activation text")
        or data.get("activationText")
    )
    source_url = normalize_text(data.get("url") or data.get("source_url"))
    description = strip_html_tags(normalize_text(data.get("description")))
    metadata: dict = {}

    if trained_words:
        metadata["triggers"] = trained_words
    if creator:
        metadata["author"] = creator

    base_model = normalize_text(data.get("baseModel") or data.get("base_model"))
    if base_model:
        metadata["base_model"] = base_model

    source_name = normalize_text(data.get("modelName") or data.get("title") or data.get("name"))
    if source_name:
        metadata["source_name"] = source_name

    tags = normalize_string_list(data.get("tags"))
    if tags:
        metadata["tags"] = tags

    if source_url:
        metadata["source_url"] = source_url
    if description:
        metadata["source_description"] = description
    model_family_hint = extract_json_model_family_hint(data)
    if model_family_hint:
        metadata["model_family_hint"] = model_family_hint
    return metadata


def load_json_sidecar(model_path: Path, directory_index: dict | None = None) -> dict:
    candidate = find_sidecar_file(
        model_path,
        [
            ".civitai.info",
            ".civitai.info.json",
            ".cm-info.json",
            ".info.json",
            ".json",
        ],
        directory_index,
    )

    if candidate is None:
        return {}

    data = read_json_file(candidate, {})
    if not isinstance(data, dict):
        return {}
    return extract_json_sidecar_metadata(data)


def load_text_sidecar(model_path: Path, directory_index: dict | None = None) -> dict:
    candidate = find_sidecar_file(model_path, [".txt"], directory_index)
    if candidate is None:
        return {}
    try:
        text = candidate.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = candidate.read_text(encoding="utf-8", errors="ignore")
    return parse_sectioned_sidecar(text)


def preview_owner_keys(stem: str) -> list[str]:
    current = stem.lower()
    keys: list[str] = []
    seen: set[str] = set()

    while current and current not in seen:
        seen.add(current)
        keys.append(current)

        trimmed = current
        for separator in (".", "_", "-"):
            for token in PREVIEW_TOKENS:
                suffix = f"{separator}{token}"
                if trimmed.endswith(suffix):
                    trimmed = trimmed[: -len(suffix)]
                    break
            if trimmed != current:
                break

        if trimmed == current:
            for separator in ("_", ".", "-"):
                if separator not in trimmed:
                    continue
                head, tail = trimmed.rsplit(separator, 1)
                if tail.isdigit():
                    trimmed = head
                    break

        if trimmed == current:
            break
        current = trimmed

    return keys


def build_directory_index(directory: Path) -> dict:
    files_by_name: dict[str, Path] = {}
    preview_candidates: dict[str, list[Path]] = {}

    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                candidate = Path(entry.path)
                files_by_name[entry.name.lower()] = candidate
                if normalize_reference_extension(candidate.suffix.lower()) not in PREVIEW_MEDIA_EXTENSIONS:
                    continue
                for key in preview_owner_keys(candidate.stem):
                    preview_candidates.setdefault(key, []).append(candidate)
    except OSError:
        return {"files_by_name": {}, "preview_candidates": {}}

    for paths in preview_candidates.values():
        paths.sort(key=lambda path: path.name.lower())

    return {
        "files_by_name": files_by_name,
        "preview_candidates": preview_candidates,
    }


def discover_preview_path(model_path: Path, directory_index: dict | None = None) -> Path | None:
    target_stem = model_path.stem.lower()

    if directory_index:
        files_by_name = directory_index["files_by_name"]
        for extension in sorted(PREVIEW_MEDIA_EXTENSIONS):
            for candidate_name in (f"{model_path.stem}{extension}", f"{model_path.stem}.preview{extension}"):
                candidate = files_by_name.get(candidate_name.lower())
                if candidate is not None and candidate.is_file():
                    return candidate

        candidates = directory_index["preview_candidates"].get(target_stem, [])
        return candidates[0] if candidates else None

    for extension in sorted(PREVIEW_MEDIA_EXTENSIONS):
        for candidate in (model_path.with_suffix(extension), model_path.with_name(f"{model_path.stem}.preview{extension}")):
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def list_preview_paths_for_model(model_path: Path, directory_index: dict | None = None) -> list[Path]:
    resolved_model_path = model_path.resolve(strict=False)
    current_index = directory_index or build_directory_index(resolved_model_path.parent)
    target_stem = resolved_model_path.stem.lower()
    files_by_name = current_index.get("files_by_name", {})
    preview_candidates = current_index.get("preview_candidates", {})
    preview_paths: list[Path] = []
    seen: set[str] = set()

    for extension in sorted(PREVIEW_MEDIA_EXTENSIONS):
        for candidate_name in (f"{resolved_model_path.stem}{extension}", f"{resolved_model_path.stem}.preview{extension}"):
            candidate = files_by_name.get(candidate_name.lower())
            if candidate is None or not candidate.is_file():
                continue
            path_key = str(candidate.resolve(strict=False)).lower()
            if path_key in seen:
                continue
            seen.add(path_key)
            preview_paths.append(candidate.resolve(strict=False))

    for candidate in preview_candidates.get(target_stem, []):
        if not candidate.is_file():
            continue
        path_key = str(candidate.resolve(strict=False)).lower()
        if path_key in seen:
            continue
        seen.add(path_key)
        preview_paths.append(candidate.resolve(strict=False))

    return preview_paths


def build_preview_api_url(model_path: Path, preview_path: Path | None = None, cache_bust: str = "") -> str:
    resolved_model_path = model_path.resolve(strict=False)
    resolved_preview_path = preview_path.resolve(strict=False) if isinstance(preview_path, Path) else None
    base_url = f"/api/lora/preview?path={quote(str(resolved_model_path), safe='')}"
    if resolved_preview_path is not None:
        base_url += f"&preview={quote(str(resolved_preview_path), safe='')}"
    if cache_bust:
        base_url += f"&v={quote(cache_bust, safe='')}"
    return base_url


def delete_preview_images_for_model(model_path: Path) -> list[str]:
    preview_paths = list_preview_paths_for_model(model_path)
    deleted_paths: list[str] = []
    for preview_path in preview_paths:
        try:
            preview_path.unlink()
            deleted_paths.append(str(preview_path.resolve(strict=False)))
        except OSError:
            continue
    return deleted_paths


def reference_folder_for_model(model_path: Path) -> Path:
    return model_path.with_name(f"{model_path.stem}.references")


def reference_metadata_path(image_path: Path) -> Path:
    return image_path.with_name(f"{image_path.name}{REFERENCE_METADATA_SUFFIX}")


def normalize_reference_extension(extension: str) -> str:
    normalized = normalize_text(extension).lower()
    if normalized == ".jpe":
        return ".jpg"
    if normalized == ".qt":
        return ".mov"
    return normalized


def reference_media_kind(filename: str | Path, mime_type: str = "") -> str:
    normalized_mime_type = normalize_text(mime_type).split(";", 1)[0].strip().lower()
    if normalized_mime_type.startswith("video/"):
        return "video"

    suffix = normalize_reference_extension(Path(str(filename or "")).suffix.lower())
    if suffix in REFERENCE_VIDEO_EXTENSIONS:
        return "video"
    return "image"


def build_civitai_asset_base_url(*values: str) -> str:
    for value in values:
        normalized = normalize_text(value).replace("&amp;", "&")
        if not normalized:
            continue
        match = re.match(r"^(https?://image\.civitai\.com/[^/]+)", normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return CIVITAI_ASSET_BASE_URL


def infer_civitai_asset_extension(mime_type: str, file_name: str = "") -> str:
    explicit_extension = normalize_reference_extension(Path(normalize_text(file_name)).suffix.lower())
    if explicit_extension:
        return explicit_extension

    normalized_mime_type = normalize_text(mime_type).split(";", 1)[0].strip().lower()
    return {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/avif": ".avif",
        "image/gif": ".gif",
    }.get(normalized_mime_type, ".jpeg")


def build_civitai_asset_url(asset_payload: dict | None = None, fallback_url: str = "") -> str:
    payload = asset_payload if isinstance(asset_payload, dict) else {}
    asset_token = normalize_text(payload.get("url"))
    if not asset_token:
        return ""

    mime_type = normalize_text(payload.get("mimeType") or payload.get("mime_type"))
    media_type = normalize_text(payload.get("type"))
    extension = infer_civitai_asset_extension(mime_type, payload.get("name"))
    file_name = normalize_text(payload.get("name")) or f"civitai-asset{extension}"
    base_url = build_civitai_asset_base_url(fallback_url)
    transform = "original=true,quality=90" if reference_media_kind(file_name, mime_type) == "video" or media_type.lower() == "video" else "width=450"
    return f"{base_url}/{quote(asset_token, safe='')}/{transform}/{quote(file_name, safe='')}"


def is_civitai_asset_media_url(value: str) -> bool:
    parsed = urlparse(normalize_text(value))
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "image.civitai.com"


def parse_png_text_chunks(image_bytes: bytes) -> dict[str, str]:
    if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return {}

    offset = 8
    values: dict[str, str] = {}

    while offset + 8 <= len(image_bytes):
        try:
            chunk_length = struct.unpack(">I", image_bytes[offset : offset + 4])[0]
        except struct.error:
            return values
        offset += 4
        chunk_type = image_bytes[offset : offset + 4]
        offset += 4

        chunk_end = offset + chunk_length
        if chunk_end + 4 > len(image_bytes):
            return values
        chunk_data = image_bytes[offset:chunk_end]
        offset = chunk_end + 4

        text_value = ""
        keyword = ""
        if chunk_type == b"tEXt":
            raw_keyword, _, raw_text = chunk_data.partition(b"\x00")
            keyword = raw_keyword.decode("latin-1", errors="ignore")
            text_value = raw_text.decode("utf-8", errors="ignore")
        elif chunk_type == b"zTXt":
            raw_keyword, _, remainder = chunk_data.partition(b"\x00")
            if remainder:
                compression_method = remainder[:1]
                compressed = remainder[1:]
                if compression_method == b"\x00":
                    try:
                        text_value = zlib.decompress(compressed).decode("utf-8", errors="ignore")
                    except zlib.error:
                        text_value = ""
                keyword = raw_keyword.decode("latin-1", errors="ignore")
        elif chunk_type == b"iTXt":
            parts = chunk_data.split(b"\x00", 5)
            if len(parts) == 6:
                raw_keyword, compression_flag, compression_method, _language, _translated, text_data = parts
                if compression_flag == b"\x01" and compression_method == b"\x00":
                    try:
                        text_value = zlib.decompress(text_data).decode("utf-8", errors="ignore")
                    except zlib.error:
                        text_value = ""
                else:
                    text_value = text_data.decode("utf-8", errors="ignore")
                keyword = raw_keyword.decode("latin-1", errors="ignore")

        keyword = normalize_text(keyword)
        if keyword and text_value:
            values[keyword] = normalize_multiline_text(text_value)

        if chunk_type == b"IEND":
            break

    return values


def parse_a1111_parameters(raw_text: str, metadata_source: str = "png.parameters") -> dict:
    text = normalize_multiline_text(raw_text)
    if not text:
        return {}

    prompt = text
    negative_prompt = ""

    if "\nNegative prompt:" in text:
        prompt, remainder = text.split("\nNegative prompt:", 1)
        if "\nSteps:" in remainder:
            negative_prompt = remainder.split("\nSteps:", 1)[0]
        else:
            negative_prompt = remainder
    elif "\nSteps:" in text:
        prompt = text.split("\nSteps:", 1)[0]

    parameter_text = ""
    if "\nSteps:" in text:
        parameter_text = "Steps:" + text.split("\nSteps:", 1)[1]
    elif text.startswith("Steps:"):
        parameter_text = text

    parameter_pairs: dict[str, str] = {}
    if parameter_text:
        flattened = normalize_multiline_text(parameter_text).replace("\n", ", ")
        matches = list(re.finditer(r"(^|,\s*)([A-Za-z][A-Za-z0-9 _./()+-]*?):\s*", flattened))
        for index, match in enumerate(matches):
            key = normalize_text(match.group(2)).lower()
            value_start = match.end()
            value_end = matches[index + 1].start() if index + 1 < len(matches) else len(flattened)
            value = normalize_text(flattened[value_start:value_end].rstrip(","))
            if key and value and key not in parameter_pairs:
                parameter_pairs[key] = value

    return {
        "metadata_source": normalize_text(metadata_source) or "png.parameters",
        "prompt": normalize_multiline_text(prompt),
        "negative_prompt": normalize_multiline_text(negative_prompt),
        "raw_parameters": text,
        "steps": normalize_text(parameter_pairs.get("steps")),
        "sampler": normalize_text(parameter_pairs.get("sampler")),
        "cfg_scale": normalize_text(parameter_pairs.get("cfg scale") or parameter_pairs.get("cfg")),
        "seed": normalize_text(parameter_pairs.get("seed")),
    }


def extract_reference_prompt_metadata_from_text(raw_text: str, metadata_source: str) -> dict:
    text = repair_endian_swapped_utf16_text(raw_text)
    if not text:
        return {}

    if "Negative prompt:" in text or "\nSteps:" in text or text.startswith("Steps:"):
        return normalize_reference_prompt_metadata(parse_a1111_parameters(text, metadata_source))

    payload = {"metadata_source": normalize_text(metadata_source)}
    if text.startswith("{") or text.startswith("["):
        resource_entries = extract_reference_resources_from_resource_stack(text)
        if resource_entries:
            payload["resources_used"] = resource_entries
            if not is_resource_stack_only_payload(text):
                payload["workflow_json"] = text
        else:
            payload["prompt_json"] = text
    else:
        payload["prompt"] = text
    return normalize_reference_prompt_metadata(payload)


def build_reference_prompt_preview(metadata: dict) -> str:
    prompt = normalize_multiline_text(metadata.get("prompt"))
    if prompt:
        return prompt.replace("\n", " / ")[:180]

    negative_prompt = normalize_multiline_text(metadata.get("negative_prompt"))
    if negative_prompt:
        return "Negative: " + negative_prompt.replace("\n", " / ")[:150]

    if normalize_multiline_text(metadata.get("prompt_json")):
        return "ComfyUI prompt metadata"
    if normalize_multiline_text(metadata.get("raw_parameters")):
        return "Prompt metadata"
    if normalize_multiline_text(metadata.get("workflow_json")):
        return "Workflow metadata"
    resources_used = normalize_reference_resources(metadata.get("resources_used") or metadata.get("resources"))
    if resources_used:
        resource_names = [entry["name"] for entry in resources_used if entry.get("name")]
        if resource_names:
            return f"Resources: {', '.join(resource_names[:3])}"[:180]
        return f"Resources used: {len(resources_used)} items"
    generation_parts = []
    if normalize_text(metadata.get("steps")):
        generation_parts.append(f"Steps {normalize_text(metadata.get('steps'))}")
    if normalize_text(metadata.get("sampler")):
        generation_parts.append(f"Sampler {normalize_text(metadata.get('sampler'))}")
    if normalize_text(metadata.get("cfg_scale")):
        generation_parts.append(f"CFG {normalize_text(metadata.get('cfg_scale'))}")
    if normalize_text(metadata.get("seed")):
        generation_parts.append(f"Seed {normalize_text(metadata.get('seed'))}")
    if generation_parts:
        return " / ".join(generation_parts)[:180]
    return ""


def has_reference_prompt_details(metadata: dict) -> bool:
    if normalize_reference_resources(metadata.get("resources_used") or metadata.get("resources")):
        return True
    return any(
        normalize_multiline_text(metadata.get(key))
        for key in (
            "prompt",
            "negative_prompt",
            "raw_parameters",
            "prompt_json",
            "workflow_json",
            "source_url",
            "source_name",
            "source_description",
            "source_host",
            "author",
            "base_model",
            "steps",
            "sampler",
            "cfg_scale",
            "seed",
        )
    )


def build_civitai_resource_url(model_id: str, model_version_id: str = "") -> str:
    normalized_model_id = normalize_text(model_id)
    normalized_version_id = normalize_text(model_version_id)
    if not normalized_model_id:
        return ""
    base_url = f"{CIVITAI_WEB_BASE_URL}/models/{quote(normalized_model_id, safe='')}"
    if not normalized_version_id:
        return base_url
    return f"{base_url}?modelVersionId={quote(normalized_version_id, safe='')}"


def normalize_reference_resources(raw_resources) -> list[dict]:
    normalized: list[dict] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for entry in raw_resources if isinstance(raw_resources, list) else []:
        if not isinstance(entry, dict):
            continue

        model_id = normalize_text(entry.get("model_id") or entry.get("modelId"))
        model_version_id = normalize_text(
            entry.get("model_version_id")
            or entry.get("modelVersionId")
            or entry.get("version_id")
            or entry.get("versionId")
        )
        name = normalize_text(entry.get("name") or entry.get("model_name") or entry.get("modelName") or entry.get("title"))
        resource_type = normalize_text(entry.get("type") or entry.get("model_type") or entry.get("modelType"))
        strength = normalize_text(entry.get("strength") or entry.get("weight"))
        base_model = normalize_text(entry.get("base_model") or entry.get("baseModel"))
        version_name = normalize_text(entry.get("version_name") or entry.get("versionName"))
        trained_words = normalize_string_list(
            entry.get("trained_words") or entry.get("trainedWords") or entry.get("trigger_words") or entry.get("triggerWords")
        )
        resource_url = normalize_text(entry.get("url") or entry.get("source_url"))
        if not resource_url:
            resource_url = build_civitai_resource_url(model_id, model_version_id)

        if not any(
            (
                name,
                resource_type,
                strength,
                base_model,
                version_name,
                model_id,
                model_version_id,
                resource_url,
                trained_words,
            )
        ):
            continue

        key = (
            resource_url.lower(),
            name.lower(),
            resource_type.lower(),
            version_name.lower(),
            strength.lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "name": name,
                "type": resource_type,
                "strength": strength,
                "base_model": base_model,
                "version_name": version_name,
                "model_id": model_id,
                "model_version_id": model_version_id,
                "trained_words": trained_words,
                "url": resource_url,
            }
        )

    return normalized


def normalize_reference_prompt_metadata(metadata: dict | None) -> dict:
    payload = metadata if isinstance(metadata, dict) else {}
    normalized = {
        "metadata_source": normalize_text(payload.get("metadata_source")),
        "prompt": repair_endian_swapped_utf16_text(payload.get("prompt")),
        "negative_prompt": repair_endian_swapped_utf16_text(payload.get("negative_prompt")),
        "raw_parameters": repair_endian_swapped_utf16_text(payload.get("raw_parameters")),
        "prompt_json": repair_endian_swapped_utf16_text(payload.get("prompt_json")),
        "workflow_json": repair_endian_swapped_utf16_text(payload.get("workflow_json")),
        "source_url": normalize_text(payload.get("source_url") or payload.get("url")),
        "source_name": repair_endian_swapped_utf16_text(payload.get("source_name") or payload.get("title") or payload.get("name")),
        "source_description": repair_endian_swapped_utf16_text(payload.get("source_description") or payload.get("description")),
        "source_host": normalize_text(payload.get("source_host")),
        "author": repair_endian_swapped_utf16_text(payload.get("author")),
        "base_model": repair_endian_swapped_utf16_text(payload.get("base_model") or payload.get("baseModel")),
        "resources_used": normalize_reference_resources(payload.get("resources_used") or payload.get("resources")),
        "steps": normalize_text(payload.get("steps")),
        "sampler": normalize_text(payload.get("sampler")),
        "cfg_scale": normalize_text(payload.get("cfg_scale") or payload.get("cfgScale") or payload.get("cfg")),
        "seed": normalize_text(payload.get("seed")),
    }
    if not normalized["resources_used"]:
        for field_name in ("prompt_json", "workflow_json", "raw_parameters", "prompt"):
            field_value = normalized.get(field_name, "")
            resource_entries = extract_reference_resources_from_resource_stack(field_value)
            if not resource_entries:
                continue
            normalized["resources_used"] = normalize_reference_resources(resource_entries)
            if is_resource_stack_only_payload(field_value):
                normalized[field_name] = ""
            break
    for field_name in ("workflow_json", "prompt_json"):
        field_value = normalized.get(field_name, "")
        if not field_value:
            continue
        extracted_details = extract_reference_details_from_json_payload(field_value)
        if not extracted_details:
            continue
        if extracted_details.get("prompt") and not normalized["prompt"]:
            normalized["prompt"] = normalize_multiline_text(extracted_details.get("prompt"))
        if extracted_details.get("negative_prompt") and not normalized["negative_prompt"]:
            normalized["negative_prompt"] = normalize_multiline_text(extracted_details.get("negative_prompt"))
        if extracted_details.get("steps") and not normalized["steps"]:
            normalized["steps"] = normalize_text(extracted_details.get("steps"))
        if extracted_details.get("sampler") and not normalized["sampler"]:
            normalized["sampler"] = normalize_text(extracted_details.get("sampler"))
        if extracted_details.get("cfg_scale") and not normalized["cfg_scale"]:
            normalized["cfg_scale"] = normalize_text(extracted_details.get("cfg_scale"))
        if extracted_details.get("seed") and not normalized["seed"]:
            normalized["seed"] = normalize_text(extracted_details.get("seed"))
        merged_resources = normalize_reference_resources(
            extracted_details.get("resources_used") or normalized["resources_used"]
        )
        if merged_resources:
            normalized["resources_used"] = merged_resources
    parsed_generation = parse_a1111_parameters(normalized["raw_parameters"], normalized["metadata_source"] or "png.parameters")
    normalized["steps"] = normalize_text(normalized["steps"] or parsed_generation.get("steps"))
    normalized["sampler"] = normalize_text(normalized["sampler"] or parsed_generation.get("sampler"))
    normalized["cfg_scale"] = normalize_text(normalized["cfg_scale"] or parsed_generation.get("cfg_scale"))
    normalized["seed"] = normalize_text(normalized["seed"] or parsed_generation.get("seed"))
    if not normalized["source_host"] and normalized["source_url"]:
        normalized["source_host"] = normalize_text(urlparse(normalized["source_url"]).netloc)
    normalized["has_prompt"] = any(
        normalized[key]
        for key in ("prompt", "negative_prompt", "raw_parameters", "prompt_json", "workflow_json")
    )
    normalized["has_details"] = has_reference_prompt_details(normalized)
    normalized["prompt_preview"] = build_reference_prompt_preview(normalized)
    return normalized


def decode_exif_text_value(tag_id: int, raw_value, byte_order: str) -> str:
    if raw_value in (None, b"", ""):
        return ""

    if isinstance(raw_value, str):
        return repair_endian_swapped_utf16_text(raw_value)

    if isinstance(raw_value, int):
        return normalize_text(str(raw_value))

    if isinstance(raw_value, (list, tuple)):
        raw_bytes = bytes(int(entry) & 0xFF for entry in raw_value)
    else:
        raw_bytes = bytes(raw_value)

    if tag_id == 0x9286:
        prefix = raw_bytes[:8]
        payload = raw_bytes[8:] if len(raw_bytes) > 8 else b""
        if prefix.startswith(b"ASCII"):
            return repair_endian_swapped_utf16_text(payload.rstrip(b"\x00").decode("utf-8", errors="ignore"))
        if prefix.startswith(b"UNICODE"):
            for encoding in (
                "utf-16-le" if byte_order == "<" else "utf-16-be",
                "utf-16-be" if byte_order == "<" else "utf-16-le",
                "utf-8",
            ):
                decoded = payload.rstrip(b"\x00").decode(encoding, errors="ignore")
                repaired = repair_endian_swapped_utf16_text(decoded)
                if repaired:
                    return repaired
        if prefix.startswith(b"JIS"):
            return repair_endian_swapped_utf16_text(payload.rstrip(b"\x00").decode("shift_jis", errors="ignore"))
        return repair_endian_swapped_utf16_text(raw_bytes.rstrip(b"\x00").decode("utf-8", errors="ignore"))

    if tag_id in {0x9C9B, 0x9C9C, 0x9C9E, 0x9C9F}:
        return repair_endian_swapped_utf16_text(raw_bytes.decode("utf-16-le", errors="ignore").rstrip("\x00"))

    return repair_endian_swapped_utf16_text(raw_bytes.split(b"\x00", 1)[0].decode("utf-8", errors="ignore"))


def decode_exif_entry_value(tiff_bytes: bytes, byte_order: str, value_type: int, value_count: int, raw_value: bytes):
    if value_count <= 0:
        return None

    type_sizes = {
        1: 1,
        2: 1,
        3: 2,
        4: 4,
        5: 8,
        7: 1,
        9: 4,
        10: 8,
    }
    component_size = type_sizes.get(value_type)
    if component_size is None:
        return None

    value_size = component_size * value_count
    if value_size <= 4:
        data = raw_value[:value_size]
    else:
        try:
            value_offset = struct.unpack(f"{byte_order}I", raw_value)[0]
        except struct.error:
            return None
        if value_offset + value_size > len(tiff_bytes):
            return None
        data = tiff_bytes[value_offset : value_offset + value_size]

    if value_type in {1, 2, 7}:
        return data
    if value_type == 3:
        values = [
            struct.unpack(f"{byte_order}H", data[index : index + 2])[0]
            for index in range(0, len(data), 2)
            if index + 2 <= len(data)
        ]
        return values[0] if len(values) == 1 else values
    if value_type == 4:
        values = [
            struct.unpack(f"{byte_order}I", data[index : index + 4])[0]
            for index in range(0, len(data), 4)
            if index + 4 <= len(data)
        ]
        return values[0] if len(values) == 1 else values
    if value_type == 9:
        values = [
            struct.unpack(f"{byte_order}i", data[index : index + 4])[0]
            for index in range(0, len(data), 4)
            if index + 4 <= len(data)
        ]
        return values[0] if len(values) == 1 else values
    return data


def parse_exif_text_fields(exif_payload: bytes) -> dict[str, str]:
    tiff_bytes = exif_payload[6:] if exif_payload.startswith(b"Exif\x00\x00") else exif_payload
    if len(tiff_bytes) < 8:
        return {}

    endian_marker = tiff_bytes[:2]
    if endian_marker == b"II":
        byte_order = "<"
    elif endian_marker == b"MM":
        byte_order = ">"
    else:
        return {}

    try:
        first_ifd_offset = struct.unpack(f"{byte_order}I", tiff_bytes[4:8])[0]
    except struct.error:
        return {}

    text_fields: dict[str, str] = {}
    visited_offsets: set[int] = set()
    text_tags = {
        0x010E: "image_description",
        0x9C9B: "xp_title",
        0x9C9C: "xp_comment",
        0x9C9E: "xp_keywords",
        0x9C9F: "xp_subject",
        0x9286: "user_comment",
    }

    def walk_ifd(offset: int) -> None:
        if offset <= 0 or offset in visited_offsets or offset + 2 > len(tiff_bytes):
            return
        visited_offsets.add(offset)

        try:
            entry_count = struct.unpack(f"{byte_order}H", tiff_bytes[offset : offset + 2])[0]
        except struct.error:
            return

        cursor = offset + 2
        for _ in range(entry_count):
            entry = tiff_bytes[cursor : cursor + 12]
            if len(entry) < 12:
                return
            cursor += 12

            try:
                tag_id = struct.unpack(f"{byte_order}H", entry[0:2])[0]
                value_type = struct.unpack(f"{byte_order}H", entry[2:4])[0]
                value_count = struct.unpack(f"{byte_order}I", entry[4:8])[0]
            except struct.error:
                continue

            value = decode_exif_entry_value(tiff_bytes, byte_order, value_type, value_count, entry[8:12])
            if tag_id in text_tags:
                decoded = decode_exif_text_value(tag_id, value, byte_order)
                if decoded:
                    text_fields[text_tags[tag_id]] = decoded
            elif tag_id == 0x8769 and isinstance(value, int):
                walk_ifd(value)

        if cursor + 4 <= len(tiff_bytes):
            try:
                next_ifd_offset = struct.unpack(f"{byte_order}I", tiff_bytes[cursor : cursor + 4])[0]
            except struct.error:
                next_ifd_offset = 0
            if next_ifd_offset:
                walk_ifd(next_ifd_offset)

    walk_ifd(first_ifd_offset)
    return text_fields


def extract_jpeg_metadata_chunks(image_bytes: bytes) -> tuple[list[bytes], list[bytes]]:
    if not image_bytes.startswith(b"\xFF\xD8"):
        return [], []

    exif_payloads: list[bytes] = []
    xmp_payloads: list[bytes] = []
    xmp_prefix = b"http://ns.adobe.com/xap/1.0/\x00"
    offset = 2

    while offset + 4 <= len(image_bytes):
        if image_bytes[offset] != 0xFF:
            offset += 1
            continue

        marker = image_bytes[offset + 1]
        offset += 2
        while marker == 0xFF and offset < len(image_bytes):
            marker = image_bytes[offset]
            offset += 1

        if marker in {0xD9, 0xDA}:
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(image_bytes):
            break

        segment_length = struct.unpack(">H", image_bytes[offset : offset + 2])[0]
        offset += 2
        payload_length = segment_length - 2
        if payload_length < 0 or offset + payload_length > len(image_bytes):
            break
        payload = image_bytes[offset : offset + payload_length]
        offset += payload_length

        if marker != 0xE1:
            continue
        if payload.startswith(b"Exif\x00\x00"):
            exif_payloads.append(payload)
        elif payload.startswith(xmp_prefix):
            xmp_payloads.append(payload[len(xmp_prefix) :])

    return exif_payloads, xmp_payloads


def extract_webp_metadata_chunks(image_bytes: bytes) -> tuple[list[bytes], list[bytes]]:
    if len(image_bytes) < 12 or not image_bytes.startswith(b"RIFF") or image_bytes[8:12] != b"WEBP":
        return [], []

    exif_payloads: list[bytes] = []
    xmp_payloads: list[bytes] = []
    offset = 12

    while offset + 8 <= len(image_bytes):
        chunk_type = image_bytes[offset : offset + 4]
        chunk_size = struct.unpack("<I", image_bytes[offset + 4 : offset + 8])[0]
        offset += 8
        chunk_end = offset + chunk_size
        if chunk_end > len(image_bytes):
            break
        chunk_data = image_bytes[offset:chunk_end]
        offset = chunk_end + (chunk_size % 2)

        if chunk_type == b"EXIF":
            exif_payloads.append(chunk_data)
        elif chunk_type == b"XMP ":
            xmp_payloads.append(chunk_data)

    return exif_payloads, xmp_payloads


def extract_reference_prompt_metadata_from_xmp_bytes(xmp_bytes: bytes, metadata_source: str) -> dict:
    decoded = decode_metadata_text_bytes(xmp_bytes)
    if not decoded:
        return {}

    candidates: list[str] = []
    for match in re.finditer(
        r"<(?:dc:description|rdf:li|xmp:Description|prompt|workflow)(?:\s[^>]*)?>(.*?)</(?:dc:description|rdf:li|xmp:Description|prompt|workflow)>",
        decoded,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        snippet = normalize_multiline_text(html.unescape(match.group(1)))
        if snippet:
            candidates.append(snippet)

    fallback_text = normalize_multiline_text(re.sub(r"<[^>]+>", "\n", html.unescape(decoded)))
    if fallback_text:
        candidates.append(fallback_text)

    for candidate in dedupe_keep_order(candidates):
        metadata = extract_reference_prompt_metadata_from_text(candidate, metadata_source)
        if metadata.get("has_details"):
            return metadata
    return {}


def extract_reference_prompt_metadata_from_bytes(image_bytes: bytes, filename: str = "", mime_type: str = "") -> dict:
    extension = Path(filename).suffix.lower()
    normalized_mime_type = normalize_text(mime_type).split(";", 1)[0].strip().lower()
    metadata: dict = {}

    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or extension == ".png" or normalized_mime_type == "image/png":
        text_chunks = parse_png_text_chunks(image_bytes)
        if text_chunks.get("parameters"):
            metadata.update(parse_a1111_parameters(text_chunks.get("parameters", ""), "png.parameters"))
        elif text_chunks.get("Description"):
            metadata.update(extract_reference_prompt_metadata_from_text(text_chunks.get("Description"), "png.description"))

        prompt_json = normalize_multiline_text(text_chunks.get("prompt"))
        workflow_json = normalize_multiline_text(text_chunks.get("workflow"))
        if prompt_json:
            metadata["prompt_json"] = prompt_json
            metadata.setdefault("metadata_source", "png.prompt")
        if workflow_json:
            metadata["workflow_json"] = workflow_json
            metadata.setdefault("metadata_source", "png.workflow")

        return normalize_reference_prompt_metadata(metadata)

    exif_payloads: list[bytes] = []
    xmp_payloads: list[bytes] = []
    if image_bytes.startswith(b"\xFF\xD8") or extension in {".jpg", ".jpeg"} or normalized_mime_type == "image/jpeg":
        exif_payloads, xmp_payloads = extract_jpeg_metadata_chunks(image_bytes)
    elif image_bytes.startswith(b"RIFF") or extension == ".webp" or normalized_mime_type == "image/webp":
        exif_payloads, xmp_payloads = extract_webp_metadata_chunks(image_bytes)

    for exif_payload in exif_payloads:
        text_fields = parse_exif_text_fields(exif_payload)
        for field_name in ("user_comment", "xp_comment", "image_description", "xp_subject", "xp_title", "xp_keywords"):
            field_value = text_fields.get(field_name, "")
            if not field_value:
                continue
            metadata = extract_reference_prompt_metadata_from_text(field_value, f"exif.{field_name}")
            if metadata.get("has_prompt"):
                return metadata

    for xmp_payload in xmp_payloads:
        metadata = extract_reference_prompt_metadata_from_xmp_bytes(xmp_payload, "xmp.packet")
        if metadata.get("has_prompt"):
            return metadata

    return normalize_reference_prompt_metadata({})


def save_reference_prompt_metadata(image_path: Path, metadata: dict) -> None:
    normalized = normalize_reference_prompt_metadata(metadata)
    sidecar_path = reference_metadata_path(image_path)

    if not normalized.get("has_details"):
        if sidecar_path.exists():
            try:
                sidecar_path.unlink()
            except OSError:
                return
        return

    payload = {
        "metadata_source": normalized["metadata_source"],
        "prompt": normalized["prompt"],
        "negative_prompt": normalized["negative_prompt"],
        "raw_parameters": normalized["raw_parameters"],
        "prompt_json": normalized["prompt_json"],
        "workflow_json": normalized["workflow_json"],
        "source_url": normalized["source_url"],
        "source_name": normalized["source_name"],
        "source_description": normalized["source_description"],
        "source_host": normalized["source_host"],
        "author": normalized["author"],
        "base_model": normalized["base_model"],
        "steps": normalized["steps"],
        "sampler": normalized["sampler"],
        "cfg_scale": normalized["cfg_scale"],
        "seed": normalized["seed"],
        "resources_used": normalized["resources_used"],
    }
    write_json_file(sidecar_path, payload)


def load_reference_prompt_metadata(image_path: Path) -> dict:
    sidecar_path = reference_metadata_path(image_path)
    if sidecar_path.exists():
        data = read_json_file(sidecar_path, {})
        if isinstance(data, dict):
            return normalize_reference_prompt_metadata(data)

    if normalize_reference_extension(image_path.suffix.lower()) not in IMAGE_EXTENSIONS:
        return normalize_reference_prompt_metadata({})

    try:
        metadata = extract_reference_prompt_metadata_from_bytes(
            image_path.read_bytes(),
            image_path.name,
            mimetypes.guess_type(image_path.name)[0] or "",
        )
    except OSError:
        return normalize_reference_prompt_metadata({})

    if metadata.get("has_details"):
        try:
            save_reference_prompt_metadata(image_path, metadata)
        except OSError:
            pass
    return metadata


def list_reference_image_paths(model_path: Path) -> list[Path]:
    reference_dir = reference_folder_for_model(model_path)
    if not reference_dir.exists() or not reference_dir.is_dir():
        return []

    images: list[Path] = []
    try:
        with os.scandir(reference_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                candidate = Path(entry.path)
                if normalize_reference_extension(candidate.suffix.lower()) in REFERENCE_MEDIA_EXTENSIONS:
                    images.append(candidate)
    except OSError:
        return []

    images.sort(key=lambda path: path.name.lower())
    return images


def build_reference_image_items(model_path: Path) -> list[dict]:
    path_text = str(model_path.resolve())
    items: list[dict] = []

    for image_path in list_reference_image_paths(model_path):
        try:
            stat = image_path.stat()
        except OSError:
            continue

        resolved_image = image_path.resolve()
        prompt_meta = load_reference_prompt_metadata(image_path)
        media_kind = reference_media_kind(image_path.name)
        items.append(
            {
                "name": image_path.name,
                "path": str(resolved_image),
                "url": f"/api/lora/reference-image?path={quote(path_text, safe='')}&file={quote(str(resolved_image), safe='')}",
                "media_kind": media_kind,
                "is_video": media_kind == "video",
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "has_prompt": bool(prompt_meta.get("has_prompt")),
                "prompt": prompt_meta.get("prompt", ""),
                "negative_prompt": prompt_meta.get("negative_prompt", ""),
                "raw_parameters": prompt_meta.get("raw_parameters", ""),
                "prompt_json": prompt_meta.get("prompt_json", ""),
                "workflow_json": prompt_meta.get("workflow_json", ""),
                "prompt_preview": prompt_meta.get("prompt_preview", ""),
                "metadata_source": prompt_meta.get("metadata_source", ""),
                "source_url": prompt_meta.get("source_url", ""),
                "source_name": prompt_meta.get("source_name", ""),
                "source_description": prompt_meta.get("source_description", ""),
                "source_host": prompt_meta.get("source_host", ""),
                "author": prompt_meta.get("author", ""),
                "base_model": prompt_meta.get("base_model", ""),
                "steps": prompt_meta.get("steps", ""),
                "sampler": prompt_meta.get("sampler", ""),
                "cfg_scale": prompt_meta.get("cfg_scale", ""),
                "seed": prompt_meta.get("seed", ""),
                "resources_used": prompt_meta.get("resources_used", []),
            }
        )

    return items


def build_encyclopedia_export_payload(image_path: Path, prompt_meta: dict | None = None) -> dict:
    metadata = normalize_reference_prompt_metadata(prompt_meta or load_reference_prompt_metadata(image_path))
    return {
        "path": str(image_path.resolve()),
        "prompt": metadata.get("prompt", ""),
        "negative_prompt": metadata.get("negative_prompt", ""),
        "raw_parameters": metadata.get("raw_parameters", ""),
        "resources_used": metadata.get("resources_used", []),
    }


def enqueue_encyclopedia_export(image_path: Path, prompt_meta: dict | None = None) -> bool:
    try:
        resolved_path = image_path.resolve()
    except OSError:
        return False
    if not resolved_path.exists() or reference_media_kind(resolved_path.name) != "image":
        return False

    payload = build_encyclopedia_export_payload(resolved_path, prompt_meta)
    with ENCYCLOPEDIA_EXPORT_QUEUE_LOCK:
        if any(item.get("path") == payload["path"] for item in ENCYCLOPEDIA_EXPORT_QUEUE):
            return False
        ENCYCLOPEDIA_EXPORT_QUEUE.append(payload)
    return True


def decode_uploaded_image_bytes(raw_value: str) -> tuple[bytes, str]:
    text = normalize_text(raw_value)
    if not text:
        raise ValueError("Image payload is empty")

    mime_type = ""
    encoded = text
    if text.startswith("data:"):
        header, separator, payload = text.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("Image payload is invalid")
        mime_type = header[5:].split(";", 1)[0].strip().lower()
        encoded = payload

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Image payload could not be decoded") from exc

    if not image_bytes:
        raise ValueError("Image payload is empty")
    if len(image_bytes) > MAX_REFERENCE_UPLOAD_BYTES:
        raise ValueError("Reference image is too large")
    return image_bytes, mime_type


def decode_uploaded_preview_bytes(raw_value: str) -> tuple[bytes, str]:
    text = normalize_text(raw_value)
    if not text:
        raise ValueError("Preview media payload is empty")

    mime_type = ""
    encoded = text
    if text.startswith("data:"):
        header, separator, payload = text.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("Preview media payload is invalid")
        mime_type = header[5:].split(";", 1)[0].strip().lower()
        encoded = payload

    try:
        media_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Preview media payload could not be decoded") from exc

    if not media_bytes:
        raise ValueError("Preview media payload is empty")
    if len(media_bytes) > MAX_REFERENCE_UPLOAD_BYTES:
        raise ValueError("Preview media is too large")
    return media_bytes, mime_type


def decode_uploaded_reference_bytes(raw_value: str) -> tuple[bytes, str]:
    text = normalize_text(raw_value)
    if not text:
        raise ValueError("Reference media payload is empty")

    mime_type = ""
    encoded = text
    if text.startswith("data:"):
        header, separator, payload = text.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("Reference media payload is invalid")
        mime_type = header[5:].split(";", 1)[0].strip().lower()
        encoded = payload

    try:
        media_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Reference media payload could not be decoded") from exc

    if not media_bytes:
        raise ValueError("Reference media payload is empty")
    if len(media_bytes) > MAX_REFERENCE_UPLOAD_BYTES:
        raise ValueError("Reference media is too large")
    return media_bytes, mime_type


def sanitize_uploaded_image_name(filename: str, mime_type: str = "") -> str:
    raw_name = Path(normalize_text(filename) or "reference").name
    stem = re.sub(r"[\\/:*?\"<>|]+", "_", Path(raw_name).stem).strip(" .")
    if not stem:
        stem = "reference"

    extension = Path(raw_name).suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        guessed_extension = mimetypes.guess_extension(mime_type) if mime_type else ""
        if guessed_extension == ".jpe":
            guessed_extension = ".jpg"
        extension = guessed_extension if guessed_extension in IMAGE_EXTENSIONS else ".png"

    return f"{stem}{extension}"


def sanitize_uploaded_preview_name(filename: str, mime_type: str = "") -> str:
    raw_name = Path(normalize_text(filename) or "preview").name
    stem = re.sub(r"[\\/:*?\"<>|]+", "_", Path(raw_name).stem).strip(" .")
    if not stem:
        stem = "preview"

    extension = normalize_reference_extension(Path(raw_name).suffix.lower())
    if extension not in PREVIEW_MEDIA_EXTENSIONS:
        guessed_extension = normalize_reference_extension(mimetypes.guess_extension(mime_type) if mime_type else "")
        if guessed_extension in PREVIEW_MEDIA_EXTENSIONS:
            extension = guessed_extension
        elif normalize_text(mime_type).split(";", 1)[0].strip().lower().startswith("video/"):
            extension = ".mp4"
        else:
            extension = ".png"

    return f"{stem}{extension}"


def sanitize_uploaded_reference_name(filename: str, mime_type: str = "") -> str:
    raw_name = Path(normalize_text(filename) or "reference").name
    stem = re.sub(r"[\\/:*?\"<>|]+", "_", Path(raw_name).stem).strip(" .")
    if not stem:
        stem = "reference"

    extension = normalize_reference_extension(Path(raw_name).suffix.lower())
    if extension not in REFERENCE_MEDIA_EXTENSIONS:
        guessed_extension = normalize_reference_extension(mimetypes.guess_extension(mime_type) if mime_type else "")
        if guessed_extension in REFERENCE_MEDIA_EXTENSIONS:
            extension = guessed_extension
        elif normalize_text(mime_type).split(";", 1)[0].strip().lower().startswith("video/"):
            extension = ".mp4"
        else:
            extension = ".png"

    return f"{stem}{extension}"


def build_unique_media_path(directory: Path, filename: str) -> Path:
    base_name = Path(filename).name or "reference.png"
    stem = Path(base_name).stem or "reference"
    suffix = Path(base_name).suffix or ".png"
    candidate = directory / base_name
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def save_reference_images_for_model(
    model_path: Path,
    files: list[dict],
    include_items: bool = True,
):
    if not isinstance(files, list) or not files:
        raise ValueError("No reference files were provided")
    if len(files) > MAX_REFERENCE_UPLOAD_FILES:
        raise ValueError(f"Reference files must be {MAX_REFERENCE_UPLOAD_FILES} files or fewer")

    reference_dir = reference_folder_for_model(model_path)
    reference_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    encyclopedia_queued = 0
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("Reference file payload is invalid")

        raw_image_value = entry.get("data_url") or entry.get("content_base64") or ""
        image_bytes, mime_type = decode_uploaded_reference_bytes(raw_image_value)
        safe_name = sanitize_uploaded_reference_name(entry.get("name", ""), mime_type)
        target_path = build_unique_media_path(reference_dir, safe_name)
        target_path.write_bytes(image_bytes)
        metadata = normalize_reference_prompt_metadata({})
        if reference_media_kind(target_path.name, mime_type) == "image":
            metadata = extract_reference_prompt_metadata_from_bytes(image_bytes, target_path.name, mime_type)
        save_reference_prompt_metadata(target_path, metadata)
        if enqueue_encyclopedia_export(target_path, metadata):
            encyclopedia_queued += 1
        saved_count += 1

    if not saved_count:
        raise ValueError("No valid reference files were provided")
    if include_items:
        items = build_reference_image_items(model_path)
        return items
    return {
        "count": len(list_reference_image_paths(model_path)),
        "items": [],
        "encyclopedia_queued": encyclopedia_queued,
    }


def delete_reference_image_for_model(model_path: Path, image_path: Path) -> str:
    resolved_model_path = model_path.resolve(strict=False)
    resolved_image_path = image_path.resolve()
    if resolved_image_path.parent != reference_folder_for_model(resolved_model_path).resolve(strict=False):
        raise ValueError("Reference image does not belong to this LoRA")

    resolved_image_path.unlink()
    sidecar_path = reference_metadata_path(resolved_image_path)
    if sidecar_path.exists():
        try:
            sidecar_path.unlink()
        except OSError:
            pass
    return str(resolved_image_path)


def is_path_within_roots(target: Path, roots: list[str]) -> bool:
    resolved_target = target.resolve(strict=False)
    for root_str in roots:
        root = Path(root_str).resolve(strict=False)
        if resolved_target == root or root in resolved_target.parents:
            return True
    return False


def is_valid_reference_image_path(target: Path, model_path: Path, roots: list[str]) -> bool:
    if not is_valid_reference_media_path(target, roots):
        return False
    return target.resolve(strict=False).parent == reference_folder_for_model(model_path).resolve(strict=False)


def is_valid_model_path(target: Path, roots: list[str]) -> bool:
    return (
        target.exists()
        and target.is_file()
        and (target.suffix.lower() in MODEL_EXTENSIONS or is_workflow_file(target))
        and is_path_within_roots(target, roots)
    )


def is_valid_image_path(target: Path, roots: list[str]) -> bool:
    return (
        target.exists()
        and target.is_file()
        and target.suffix.lower() in IMAGE_EXTENSIONS
        and is_path_within_roots(target, roots)
    )


def is_valid_preview_media_path(target: Path, roots: list[str]) -> bool:
    return (
        target.exists()
        and target.is_file()
        and normalize_reference_extension(target.suffix.lower()) in PREVIEW_MEDIA_EXTENSIONS
        and is_path_within_roots(target, roots)
    )


def is_valid_reference_media_path(target: Path, roots: list[str]) -> bool:
    return (
        target.exists()
        and target.is_file()
        and normalize_reference_extension(target.suffix.lower()) in REFERENCE_MEDIA_EXTENSIONS
        and is_path_within_roots(target, roots)
    )


def guess_import_model_family_from_payload(metadata_payload: dict | None = None) -> str:
    payload = metadata_payload if isinstance(metadata_payload, dict) else {}
    tags_text = " ".join(normalize_string_list(payload.get("tags", [])))
    triggers_text = " ".join(normalize_string_list(payload.get("triggers", [])))
    combined = first_text_value(
        payload.get("model_family"),
        payload.get("model_family_hint"),
        payload.get("title"),
        payload.get("description"),
        payload.get("source_url"),
        payload.get("download_url"),
        tags_text,
        triggers_text,
    )
    return normalize_model_family(combined)


def infer_watch_dir_model_family(directory: Path | str) -> str:
    parts = [normalize_text(part) for part in Path(directory).parts][-4:]
    for part in reversed(parts):
        collapsed = re.sub(r"[^a-z0-9]+", "", part.lower())
        for family, aliases in WATCH_DIR_FAMILY_ALIASES.items():
            if collapsed and collapsed in {re.sub(r"[^a-z0-9]+", "", alias.lower()) for alias in aliases}:
                return family
        normalized = normalize_model_family(part)
        if normalized:
            return normalized
    return ""


def find_standard_family_subdir(root: Path, family: str) -> Path | None:
    family = normalize_model_family(family)
    if not family:
        return None
    if not root.exists() or not root.is_dir():
        return None

    candidate_names = {
        "lora": ["lora", "loras"],
        "lycoris": ["lycoris", "locon", "loha", "lokr"],
        "checkpoint": ["checkpoints", "checkpoint", "stable-diffusion", "stablediffusion"],
        "embedding": ["embeddings", "embedding", "textualinversion", "textual-inversion"],
    }.get(family, [])

    try:
        children = list(root.iterdir())
    except OSError:
        return None

    for name in candidate_names:
        for child in children:
            if not child.is_dir():
                continue
            child_name = normalize_text(child.name).lower()
            normalized_child = normalize_model_family(child_name)
            collapsed_child = re.sub(r"[^a-z0-9]+", "", child_name)
            if normalized_child == family or collapsed_child == re.sub(r"[^a-z0-9]+", "", name):
                return child.resolve(strict=False)
    return None


def pick_watch_dir_for_model_family(model_family: str, watch_dirs: list[str]) -> Path:
    resolved_watch_dirs = [Path(path).resolve(strict=False) for path in watch_dirs]
    family = collapse_model_family(model_family)
    if family == "workflow":
        matches = [path for path in resolved_watch_dirs if infer_watch_dir_model_family(path) == "workflow"]
        if not matches:
            raise ValueError("ComfyUI の workflows フォルダを登録してください。")
        return matches[0]
    resolved_watch_dirs = [path for path in resolved_watch_dirs if infer_watch_dir_model_family(path) != "workflow"]
    if not resolved_watch_dirs:
        raise ValueError("モデルの保存先フォルダを登録してください。")
    if not family:
        return resolved_watch_dirs[0]

    explicit_matches = [path for path in resolved_watch_dirs if infer_watch_dir_model_family(path) == family]
    if explicit_matches:
        return explicit_matches[0]

    for root in resolved_watch_dirs:
        candidate = find_standard_family_subdir(root, family)
        if candidate is not None and is_path_within_roots(candidate, watch_dirs):
            return candidate

    return resolved_watch_dirs[0]


def detect_import_model_family(source_path: Path, metadata_payload: dict | None = None) -> str:
    if source_path.suffix.lower() in WORKFLOW_EXTENSIONS:
        return "workflow"
    imported_meta: dict = {}
    if source_path.suffix.lower() == ".safetensors":
        try:
            imported_meta.update(load_embedded_safetensors_metadata(source_path))
        except (OSError, ValueError, struct.error, json.JSONDecodeError):
            pass

    payload_hint = guess_import_model_family_from_payload(metadata_payload)
    if payload_hint:
        imported_meta["model_family"] = payload_hint

    try:
        size_bytes = source_path.stat().st_size
    except OSError:
        size_bytes = 0

    return detect_model_family(source_path, imported_meta, size_bytes)


def resolve_import_target_dir(target_dir_text: str, watch_dirs: list[str], source_path: Path | None = None, metadata_payload: dict | None = None) -> Path:
    if not watch_dirs:
        raise ValueError("Watch folder is not configured")

    normalized_target = normalize_text(target_dir_text)
    if not normalized_target or normalized_target == "__auto__":
        if source_path is not None:
            family = detect_import_model_family(source_path, metadata_payload)
            return pick_watch_dir_for_model_family(family, watch_dirs)
        return Path(watch_dirs[0]).resolve(strict=False)

    candidate = Path(normalized_target).expanduser()
    if not candidate.is_absolute():
        candidate = (APP_ROOT / candidate).resolve(strict=False)
    else:
        candidate = candidate.resolve(strict=False)

    if not is_path_within_roots(candidate, watch_dirs):
        raise ValueError("Target folder is not registered")
    if source_path is not None:
        workflow_roots = [path for path in watch_dirs if infer_watch_dir_model_family(path) == "workflow"]
        is_workflow_target = is_path_within_roots(candidate, workflow_roots)
        if (source_path.suffix.lower() in WORKFLOW_EXTENSIONS) != is_workflow_target:
            raise ValueError("Workflow は workflows フォルダ、モデルはモデル用フォルダを選んでください。")
    return candidate


def build_unique_import_path(directory: Path, filename: str) -> Path:
    base_name = Path(filename).name or "downloaded_model.safetensors"
    stem = Path(base_name).stem or "downloaded_model"
    suffix = Path(base_name).suffix
    candidate = directory / base_name
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def parse_content_disposition_filename(header_value: str) -> str:
    text = normalize_text(header_value)
    if not text:
        return ""

    utf8_match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", text, flags=re.IGNORECASE)
    if utf8_match:
        return normalize_text(unquote(utf8_match.group(1).strip().strip('"')))

    quoted_match = re.search(r'filename\s*=\s*"([^"]+)"', text, flags=re.IGNORECASE)
    if quoted_match:
        return normalize_text(quoted_match.group(1))

    plain_match = re.search(r"filename\s*=\s*([^;]+)", text, flags=re.IGNORECASE)
    if plain_match:
        return normalize_text(plain_match.group(1).strip().strip('"'))

    return ""


def sanitize_download_filename(filename: str, fallback_url: str = "") -> str:
    base_name = Path(normalize_text(filename)).name
    if not base_name and fallback_url:
        base_name = Path(urlparse(fallback_url).path).name
    base_name = normalize_text(base_name)
    if not base_name:
        base_name = "downloaded_model.safetensors"

    base_name = re.sub(r'[<>:"/\\|?*]+', "_", base_name).strip(" .")
    if not base_name:
        base_name = "downloaded_model.safetensors"

    suffix = Path(base_name).suffix.lower()
    if suffix in DOWNLOAD_EXTENSIONS:
        return base_name

    fallback_suffix = Path(urlparse(fallback_url).path).suffix.lower() if fallback_url else ""
    if fallback_suffix in DOWNLOAD_EXTENSIONS:
        return f"{Path(base_name).stem or 'downloaded_model'}{fallback_suffix}"

    return f"{Path(base_name).stem or 'downloaded_model'}.safetensors"


def wait_for_downloaded_model_file(source_path: Path) -> Path | None:
    candidate = source_path.expanduser()
    deadline = monotonic() + max(DOWNLOAD_IMPORT_WAIT_SECONDS, 0)

    while True:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except OSError:
            pass

        if monotonic() >= deadline:
            return None
        sleep(max(DOWNLOAD_IMPORT_POLL_SECONDS, 0.05))


def fetch_json_url(json_url: str, referer_url: str = "") -> dict | list:
    url = normalize_text(json_url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("JSON URL is invalid")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    if referer_url:
        headers["Referer"] = referer_url

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read(MAX_PREVIEW_DOWNLOAD_BYTES)

    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON response could not be parsed") from exc


def fetch_civitai_preview_image_url(source_url: str) -> str:
    url = normalize_text(source_url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""

    host = parsed.netloc.lower()
    if not is_civitai_page_host(host):
        return ""

    version_id = normalize_text((parse_qs(parsed.query).get("modelVersionId") or [""])[0])
    api_origin = civitai_origin_for_url(url)
    if version_id.isdigit():
        api_url = f"{api_origin}/api/v1/model-versions/{version_id}"
        payload = fetch_json_url(api_url, url)
        images = payload.get("images", []) if isinstance(payload, dict) else []
    else:
        match = re.match(r"^/models/(\d+)(?:/|$)", parsed.path)
        if not match:
            return ""
        api_url = f"{api_origin}/api/v1/models/{match.group(1)}"
        payload = fetch_json_url(api_url, url)
        model_versions = payload.get("modelVersions", []) if isinstance(payload, dict) else []
        images = model_versions[0].get("images", []) if model_versions and isinstance(model_versions[0], dict) else []

    for image in images:
        if not isinstance(image, dict):
            continue
        image_url = normalize_text(image.get("url"))
        if not image_url:
            continue
        image_type = normalize_text(image.get("type")).lower()
        if image_type and image_type != "image":
            continue
        return image_url

    return ""


def fetch_civitai_image_media_url(source_url: str, fallback_url: str = "") -> str:
    url = normalize_text(source_url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""

    host = parsed.netloc.lower()
    if not is_civitai_page_host(host):
        return ""

    match = re.match(r"^/images/(\d+)(?:/|$)", parsed.path)
    if not match:
        return ""

    input_value = quote(json.dumps({"json": {"id": int(match.group(1))}}, separators=(",", ":")), safe="")
    api_url = f"{civitai_origin_for_url(url)}/api/trpc/image.get?input={input_value}"
    payload = fetch_json_url(api_url, url)
    result = payload.get("result", {}).get("data", {}).get("json") if isinstance(payload, dict) else {}
    return build_civitai_asset_url(result, fallback_url)


def resolve_preview_image_url(metadata_payload: dict | None = None) -> str:
    payload = metadata_payload if isinstance(metadata_payload, dict) else {}
    preview_image_url = normalize_text(payload.get("preview_image_url"))
    if preview_image_url:
        return preview_image_url

    source_url = normalize_text(payload.get("source_url"))
    if not source_url:
        return ""

    try:
        return fetch_civitai_preview_image_url(source_url)
    except (OSError, ValueError):
        return ""


def resolve_preview_media_url(metadata_payload: dict | None = None) -> str:
    payload = metadata_payload if isinstance(metadata_payload, dict) else {}
    source_url = normalize_text(payload.get("source_url") or payload.get("url"))
    preview_media_kind = normalize_text(payload.get("preview_media_kind") or payload.get("media_kind")).lower()
    preview_video_url = normalize_text(payload.get("preview_video_url"))
    if preview_media_kind == "video" and preview_video_url:
        return preview_video_url

    preview_media_url = normalize_text(payload.get("preview_media_url"))
    if preview_media_kind == "video" and guess_reference_media_extension(preview_media_url) in REFERENCE_VIDEO_EXTENSIONS:
        return preview_media_url

    preview_image_url = normalize_text(payload.get("preview_image_url"))
    if is_civitai_image_page_url(source_url):
        if is_civitai_asset_media_url(preview_media_url):
            return preview_media_url
        if not preview_media_url and is_civitai_asset_media_url(preview_image_url):
            return preview_image_url
        try:
            civitai_media_url = fetch_civitai_image_media_url(source_url, preview_media_url or preview_image_url)
        except (OSError, ValueError):
            civitai_media_url = ""
        if civitai_media_url:
            return civitai_media_url

    if preview_media_kind == "image":
        if preview_media_url and guess_reference_media_extension(preview_media_url) not in REFERENCE_VIDEO_EXTENSIONS:
            return preview_media_url
        if preview_image_url:
            return preview_image_url
        return resolve_preview_image_url(payload)

    if preview_video_url:
        return preview_video_url
    if guess_reference_media_extension(preview_media_url) in REFERENCE_VIDEO_EXTENSIONS:
        return preview_media_url
    if preview_media_url:
        return preview_media_url
    if preview_image_url:
        return preview_image_url
    return resolve_preview_image_url(payload)


def guess_reference_media_extension(preview_url: str, content_type: str = "") -> str:
    url_extension = normalize_reference_extension(Path(urlparse(preview_url).path).suffix.lower())
    if url_extension in REFERENCE_MEDIA_EXTENSIONS:
        return url_extension

    mime_type = normalize_text(content_type).split(";", 1)[0].strip().lower()
    guessed_extension = normalize_reference_extension(mimetypes.guess_extension(mime_type) if mime_type else "")
    if guessed_extension in REFERENCE_MEDIA_EXTENSIONS:
        return guessed_extension
    if mime_type.startswith("video/"):
        return ".mp4"
    return ".jpg"


def fetch_reference_media_bytes(preview_url: str, referer_url: str = "") -> tuple[bytes, str]:
    url = normalize_text(preview_url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Reference media URL is invalid")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135 Safari/537.36",
        "Accept": "video/webm,video/mp4,image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
    }
    if referer_url:
        headers["Referer"] = referer_url

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        content_type = normalize_text(response.headers.get("Content-Type"))
        payload = response.read(MAX_REFERENCE_DOWNLOAD_BYTES + 1)

    if len(payload) > MAX_REFERENCE_DOWNLOAD_BYTES:
        raise ValueError("Reference media is too large")

    mime_type = content_type.split(";", 1)[0].strip().lower()
    url_extension = normalize_reference_extension(Path(parsed.path).suffix.lower())
    if mime_type and not (mime_type.startswith("image/") or mime_type.startswith("video/")) and url_extension not in REFERENCE_MEDIA_EXTENSIONS:
        raise ValueError("Reference URL did not return supported media")
    if not mime_type and url_extension not in REFERENCE_MEDIA_EXTENSIONS:
        raise ValueError("Reference URL did not return supported media")

    return payload, content_type


def guess_preview_extension(preview_url: str, content_type: str = "") -> str:
    url_extension = normalize_reference_extension(Path(urlparse(preview_url).path).suffix.lower())
    if url_extension in PREVIEW_MEDIA_EXTENSIONS:
        return url_extension

    mime_type = normalize_text(content_type).split(";", 1)[0].strip().lower()
    guessed_extension = normalize_reference_extension(mimetypes.guess_extension(mime_type) if mime_type else "")
    if guessed_extension in PREVIEW_MEDIA_EXTENSIONS:
        return guessed_extension
    if mime_type.startswith("video/"):
        return ".mp4"
    return ".jpg"


def fetch_preview_image_bytes(preview_url: str, referer_url: str = "") -> tuple[bytes, str]:
    url = normalize_text(preview_url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Preview media URL is invalid")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
    }
    if referer_url:
        headers["Referer"] = referer_url

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        content_type = normalize_text(response.headers.get("Content-Type"))
        payload = response.read(MAX_PREVIEW_DOWNLOAD_BYTES + 1)

    if len(payload) > MAX_PREVIEW_DOWNLOAD_BYTES:
        raise ValueError("Preview media is too large")

    mime_type = content_type.split(";", 1)[0].strip().lower()
    url_extension = normalize_reference_extension(Path(parsed.path).suffix.lower())
    if mime_type and not (mime_type.startswith("image/") or mime_type.startswith("video/")) and url_extension not in PREVIEW_MEDIA_EXTENSIONS:
        raise ValueError("Preview URL did not return supported media")
    if not mime_type and url_extension not in PREVIEW_MEDIA_EXTENSIONS:
        raise ValueError("Preview URL did not return supported media")

    return payload, content_type


def save_preview_bytes_for_model(model_path: Path, image_bytes: bytes, preview_url: str = "", content_type: str = "") -> Path | None:
    if not image_bytes:
        return None

    existing_preview = discover_preview_path(model_path, build_directory_index(model_path.parent))
    if existing_preview is not None:
        return existing_preview

    extension = guess_preview_extension(preview_url, content_type)
    preview_path = model_path.with_suffix(extension)
    preview_path.write_bytes(image_bytes)
    return preview_path


def replace_preview_bytes_for_model(model_path: Path, image_bytes: bytes, extension: str) -> Path:
    if not image_bytes:
        raise ValueError("Preview media is empty")
    import tempfile
    preview_path = model_path.with_suffix(extension)
    old_paths = list_preview_paths_for_model(model_path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=model_path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(image_bytes)
        os.replace(temporary, preview_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    for old in old_paths:
        if old.resolve() != preview_path.resolve():
            old.unlink(missing_ok=True)
    return preview_path.resolve()


def replace_remote_preview_for_model(model_path: Path, preview_url: str, referer_url: str = "") -> Path:
    image_bytes, content_type = fetch_preview_image_bytes(preview_url, referer_url)
    return replace_preview_bytes_for_model(model_path, image_bytes, guess_preview_extension(preview_url, content_type))


def replace_preview_image_for_model(model_path: Path, file_payload: dict | None = None) -> Path:
    payload = file_payload if isinstance(file_payload, dict) else {}
    image_bytes, mime_type = decode_uploaded_preview_bytes(payload.get("data_url") or payload.get("content_base64") or "")
    safe_name = sanitize_uploaded_preview_name(payload.get("name", ""), mime_type)
    extension = normalize_reference_extension(Path(safe_name).suffix.lower())
    if extension not in PREVIEW_MEDIA_EXTENSIONS:
        extension = ".png"

    return replace_preview_bytes_for_model(model_path, image_bytes, extension)


def download_preview_image_for_model(model_path: Path, preview_url: str, referer_url: str = "") -> Path | None:
    if not preview_url:
        return None

    try:
        image_bytes, content_type = fetch_preview_image_bytes(preview_url, referer_url)
    except (OSError, ValueError):
        return None

    try:
        return save_preview_bytes_for_model(model_path, image_bytes, preview_url, content_type)
    except OSError:
        return None


def build_lora_item_snapshot(model_path: Path, watch_dirs: list[str] | None = None, cache_bust: str = "") -> dict:
    current_watch_dirs = watch_dirs or load_lora_config()["watch_dirs"]
    resolved_model_path = model_path.resolve(strict=False)
    path_key = str(resolved_model_path)
    store_meta = load_lora_store([path_key]).get(path_key, {})
    if not isinstance(store_meta, dict):
        store_meta = {}
    directory_index = build_directory_index(resolved_model_path.parent)
    item = build_lora_item(resolved_model_path, current_watch_dirs, store_meta, directory_index)
    if item.get("preview_available") and cache_bust:
        preview_path = discover_preview_path(resolved_model_path, directory_index)
        item["preview_url"] = build_preview_api_url(resolved_model_path, preview_path, cache_bust)
    return item


def is_workflow_document(data) -> bool:
    if not isinstance(data, dict):
        return False
    nodes = data.get("nodes")
    if isinstance(nodes, list) and nodes and isinstance(data.get("links"), list):
        return all(isinstance(node, dict) and isinstance(node.get("id"), (str, int))
                   and isinstance(node.get("type"), str) and node["type"] for node in nodes)
    # ComfyUI API-format graphs use node IDs as keys.
    return bool(data) and all(isinstance(node, dict) and isinstance(node.get("class_type"), str)
                             and node["class_type"] and isinstance(node.get("inputs"), dict)
                             for node in data.values())


def read_workflow_bytes(payload: bytes) -> dict:
    if len(payload) > MAX_WORKFLOW_JSON_BYTES:
        raise ValueError("Workflow JSON is too large")
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Workflow JSON could not be read") from exc
    if not is_workflow_document(data):
        raise ValueError("ComfyUI workflow JSON was not found")
    return data


@lru_cache(maxsize=256)
def _is_workflow_file_cached(path_text: str, size: int, modified_ns: int) -> bool:
    try:
        read_workflow_bytes(Path(path_text).read_bytes())
        return True
    except (OSError, ValueError):
        return False


def is_workflow_file(path: Path) -> bool:
    if path.suffix.lower() != ".json":
        return False
    try:
        stat = path.stat()
        return stat.st_size <= MAX_WORKFLOW_JSON_BYTES and _is_workflow_file_cached(str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return False


def read_workflow_package(source: Path) -> list[tuple[str, bytes]]:
    if source.suffix.lower() == ".json":
        if source.stat().st_size > MAX_WORKFLOW_JSON_BYTES:
            raise ValueError("Workflow JSON is too large")
        payload = source.read_bytes()
        read_workflow_bytes(payload)
        return [(source.name, payload)]
    if source.stat().st_size > MAX_WORKFLOW_ARCHIVE_BYTES:
        raise ValueError("Workflow ZIP is too large")
    workflows = []
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_WORKFLOW_ARCHIVE_FILES or sum(entry.file_size for entry in entries) > MAX_WORKFLOW_EXPANDED_BYTES:
            raise ValueError("Workflow ZIP contains too much data")
        for entry in entries:
            normalized = entry.filename.replace("\\", "/")
            parts = PurePosixPath(normalized).parts
            if normalized.startswith("/") or ".." in parts or any(":" in part for part in parts) or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Workflow ZIP contains an unsafe path")
            if entry.is_dir() or PurePosixPath(normalized).suffix.lower() != ".json":
                continue
            if entry.file_size > MAX_WORKFLOW_JSON_BYTES or entry.flag_bits & 1:
                raise ValueError("Workflow ZIP JSON is too large or encrypted")
            payload = archive.read(entry)
            try:
                read_workflow_bytes(payload)
            except ValueError:
                continue  # Bundled configuration files are not workflows.
            # Keep variants from different archive folders distinct without extracting paths.
            filename = sanitize_download_filename(" - ".join(parts))
            workflows.append((filename, payload))
    if not workflows:
        raise ValueError("ZIP 内に ComfyUI のワークフローJSONがありません。")
    return workflows


def import_downloaded_workflow(source: Path, metadata_payload: dict, target_dir_text: str = "") -> dict:
    try:
        workflows = read_workflow_package(source)  # Validate the whole package before writing.
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError("Workflow ZIP を読み取れません。通常のZIPファイルを指定してください。") from exc
    watch_dirs = load_lora_config()["watch_dirs"]
    target_dir = resolve_import_target_dir(target_dir_text, watch_dirs, source, {"model_family_hint": "workflow"})
    target_dir.mkdir(parents=True, exist_ok=True)
    targets = []
    created = []
    archived = None
    archive_path = ""
    try:
        for filename, payload in workflows:
            if source.suffix.lower() == ".json" and source.resolve().parent == target_dir.resolve():
                target = source.resolve()
            else:
                target = build_unique_import_path(target_dir, filename)
                # Exclusive creation avoids overwriting existing workflows.
                with target.open("xb") as handle:
                    created.append(target)
                    handle.write(payload)
            targets.append(target.resolve())
        changes = sanitize_metadata_input(metadata_payload)
        changes["notes"] = append_import_description_to_notes(
            normalize_text(changes.get("notes")),
            normalize_text(metadata_payload.get("description")),
            normalize_text(metadata_payload.get("source_url")),
        )
        if source.suffix.lower() == ".zip":
            archive_dir = DATA_ROOT / "workflow-archives"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archived = build_unique_import_path(archive_dir, source.name)
            shutil.move(str(source), str(archived))
            archive_path = str(archived.resolve())
        merge_metadata_changes([str(target) for target in targets], changes)
    except Exception:
        if archived is not None and archived.exists() and not source.exists():
            shutil.move(str(archived), str(source))
        for target in created:
            target.unlink(missing_ok=True)
        raise
    source_retained = False
    if source.suffix.lower() == ".json" and source.resolve() not in targets:
        try:
            source.unlink()
        except OSError:
            source_retained = True
    first = targets[0]
    return {"path": str(first), "filename": source.name, "target_dir": str(target_dir.resolve()),
            "model_family": "workflow", "model_family_label": "Workflow", "preview_path": "",
            "metadata": changes, "workflow_paths": [str(target) for target in targets],
            "imported_count": len(targets), "archive_path": archive_path, "source_retained": source_retained}


def import_downloaded_lora(*args, **kwargs) -> dict:
    with DOWNLOAD_COMPLETION_LOCK:
        return _import_downloaded_lora(*args, **kwargs)


def _import_downloaded_lora(source_path_text: str, metadata_payload: dict | None = None, target_dir_text: str = "") -> dict:
    metadata_payload = metadata_payload if isinstance(metadata_payload, dict) else {}
    watch_dirs = load_lora_config()["watch_dirs"]
    requested_source_path = Path(normalize_text(source_path_text)).expanduser()
    source_path = wait_for_downloaded_model_file(requested_source_path)
    if source_path is None:
        raise FileNotFoundError(f"Downloaded file not found: {requested_source_path}")
    if source_path.suffix.lower() in WORKFLOW_EXTENSIONS:
        return import_downloaded_workflow(source_path, metadata_payload, target_dir_text)
    if source_path.suffix.lower() not in MODEL_EXTENSIONS:
        raise ValueError("Downloaded file is not a supported model file")

    detected_family = collapse_model_family(detect_import_model_family(source_path, metadata_payload)) or "other"
    target_dir = resolve_import_target_dir(target_dir_text, watch_dirs, source_path, metadata_payload)
    target_dir.mkdir(parents=True, exist_ok=True)

    resolved_source = source_path.resolve(strict=False)
    resolved_target_dir = target_dir.resolve(strict=False)
    if resolved_source.parent == resolved_target_dir:
        target_path = resolved_source
    else:
        target_path = build_unique_import_path(resolved_target_dir, resolved_source.name)
        shutil.move(str(resolved_source), str(target_path))
        target_path = target_path.resolve(strict=False)

    path_key = str(target_path)
    changes_payload = dict(metadata_payload)
    if "display_name" not in changes_payload and normalize_text(metadata_payload.get("title")):
        changes_payload["display_name"] = normalize_text(metadata_payload["title"])
    merged_notes = append_import_description_to_notes(
        normalize_text(changes_payload.get("notes")),
        first_text_value(changes_payload.get("description"), changes_payload.get("source_description")),
        first_text_value(changes_payload.get("source_url"), changes_payload.get("url")),
    )
    if merged_notes:
        changes_payload["notes"] = merged_notes
    try:
        changes = sanitize_metadata_input(changes_payload)
        if changes:
            metadata = merge_metadata_changes([path_key], changes).get(path_key, {})
        else:
            metadata = load_lora_store([path_key]).get(path_key, {})
    except Exception:
        if resolved_source != target_path and not resolved_source.exists():
            shutil.move(str(target_path), str(resolved_source))
        raise

    preview_path = download_preview_image_for_model(target_path, resolve_preview_media_url(metadata_payload), normalize_text(metadata_payload.get("source_url")))

    return {
        "path": path_key,
        "filename": target_path.name,
        "target_dir": str(resolved_target_dir),
        "model_family": detected_family,
        "model_family_label": label_model_family(detected_family),
        "preview_path": str(preview_path.resolve()) if preview_path else "",
        "metadata": metadata,
    }


DOWNLOAD_RECOVERY_LOCK = threading.Lock()
DOWNLOAD_RECOVERY_JOB = {"status": "idle", "results": []}


def get_download_recovery_status() -> dict:
    with DOWNLOAD_RECOVERY_LOCK:
        return dict(DOWNLOAD_RECOVERY_JOB, results=list(DOWNLOAD_RECOVERY_JOB.get("results", [])))


def start_download_recovery(payload: dict) -> dict:
    downloads = payload.get("downloads", [])
    if not isinstance(downloads, list) or len(downloads) > 5000:
        raise ValueError("Download history is invalid or exceeds 5000 entries")
    if not load_lora_config()["watch_dirs"]:
        raise ValueError("先に LoRA管理で保存先フォルダーを登録してください。")
    with DOWNLOAD_RECOVERY_LOCK:
        if DOWNLOAD_RECOVERY_JOB.get("status") == "running":
            return dict(DOWNLOAD_RECOVERY_JOB, already_running=True)
        DOWNLOAD_RECOVERY_JOB.clear()
        DOWNLOAD_RECOVERY_JOB.update(status="running", id=str(time_ns()), total=0, processed=0,
                                     imported=0, skipped=0, failed=0, results=[], message="ダウンロード済みファイルを調査しています。")
    threading.Thread(target=run_download_recovery, args=(payload,), daemon=True).start()
    return get_download_recovery_status()


def resolve_civitai_recovery_metadata(source: Path, record: dict, page_metadata: dict) -> dict:
    """Fetch by public IDs, then compare hashes locally; never transmit a file hash."""
    version_urls = []
    model_urls = []
    urls = list(record.get("download_urls", [])) + [record.get("referrer", ""), page_metadata.get("source_url", "")]
    for url in urls:
        if not isinstance(url, str):
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in CIVITAI_PAGE_HOSTS:
            continue
        origin = f"https://{parsed.hostname}"
        match = re.fullmatch(r"/api/download/models/(\d+)", parsed.path)
        if match:
            version_urls.append(f"{origin}/api/v1/model-versions/{match.group(1)}")
        match = re.match(r"^/models/(\d+)(?:/|$)", parsed.path)
        if match:
            model_urls.append(f"{origin}/api/v1/models/{match.group(1)}")
    requests = list(dict.fromkeys(version_urls + model_urls))
    if not requests:
        return {}
    digest = None
    before = source.stat()
    errors = []
    for api_url in requests[:6]:
        try:
            data = fetch_json_url(api_url)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            continue
        if not isinstance(data, dict):
            continue
        is_model = "/api/v1/models/" in api_url
        versions = data.get("modelVersions", []) if is_model else [data]
        for version in versions:
            if not isinstance(version, dict):
                continue
            model = data if is_model else version.get("model", {})
            if not isinstance(model, dict):
                continue
            family = collapse_model_family(normalize_model_family(model.get("type")))
            model_id = model.get("id") if is_model else version.get("modelId")
            version_id = version.get("id")
            if family not in {"lora", "checkpoint", "embedding", "workflow"} or not str(model_id).isdigit() or not str(version_id).isdigit():
                continue
            for file in version.get("files", []):
                if not isinstance(file, dict):
                    continue
                expected = str((file.get("hashes") or {}).get("SHA256", "")).upper()
                if not re.fullmatch(r"[0-9A-F]{64}", expected):
                    continue
                size_kb = file.get("sizeKB")
                if isinstance(size_kb, (int, float)) and abs(before.st_size - round(size_kb * 1024)) > 1024:
                    continue
                if digest is None:
                    digest = sha256_file(source).upper()
                    after = source.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError("照合中にファイルが変更されました。完了後に再実行してください。")
                if digest != expected:
                    continue
                title = normalize_text(model.get("name"))
                version_name = normalize_text(version.get("name"))
                return {
                    "title": " / ".join(part for part in (title, version_name) if part),
                    "model_family": family,
                    "model_family_hint": family,
                    "source_url": f"https://{urlparse(api_url).hostname}/models/{model_id}?modelVersionId={version_id}",
                    "source_name": title,
                    "base_model": normalize_text(version.get("baseModel")),
                    "triggers": normalize_string_list(version.get("trainedWords", [])),
                    "_recovery_sha256": digest,
                }
    if errors:
        raise ValueError("Civitaiの配布情報を取得できません。接続を確認して再実行してください。")
    raise ValueError("配布情報のSHA-256と一致するファイルがありません。名前だけでは移動しません。")


def recovery_metadata(source: Path, record: dict, page_metadata: dict) -> tuple[dict, dict | None]:
    metadata = {}
    if source.suffix.lower() == ".safetensors":
        header = read_safetensors_header(source)
        if not header:
            raise ValueError("有効な safetensors ヘッダーを読み取れません。")
        with source.open("rb") as handle:
            header_size = struct.unpack("<Q", handle.read(8))[0]
        available = source.stat().st_size - 8 - header_size
        for key, tensor in header.items():
            if key == "__metadata__":
                continue
            offsets = tensor.get("data_offsets") if isinstance(tensor, dict) else None
            if not isinstance(offsets, list) or len(offsets) != 2 or not all(type(v) is int for v in offsets) or not 0 <= offsets[0] <= offsets[1] <= available:
                raise ValueError("モデルデータが未完了、またはヘッダーが不正です。")
        metadata.update(load_embedded_safetensors_metadata(source))
        if not metadata.get("model_family"):
            names = " ".join(key.lower() for key in header if key != "__metadata__")
            if any(token in names for token in ("lora_down", "lora_up", "lora_a.", "lora_b.", "hada_w", "lokr_")):
                metadata["model_family"] = "lora"
            elif "model.diffusion_model." in names and ("first_stage_model." in names or "cond_stage_model." in names):
                metadata["model_family"] = "checkpoint"
    metadata.update(load_json_sidecar(source))
    keys = {download_resource_key(url) for url in record.get("download_urls", []) if isinstance(url, str)} - {""}
    pending = get_pending_download_import_payload().get("pending")
    matched_pending = None
    # Only exact download identity may contribute metadata from an open page/wait.
    for candidate in (page_metadata, pending.get("metadata", {}) if pending else {}):
        key = download_resource_key(candidate.get("download_url", ""))
        if key and key in keys:
            metadata.update(candidate)
            if pending and candidate is not page_metadata:
                matched_pending = pending
    if source.suffix.lower() == ".safetensors" and not guess_import_model_family_from_payload(metadata):
        metadata.update(resolve_civitai_recovery_metadata(source, record, page_metadata))
    return metadata, matched_pending


def recover_download_file(source: Path, record: dict, page_metadata: dict, snapshot: tuple) -> dict:
    with DOWNLOAD_COMPLETION_LOCK:
        if not source.is_file():
            return {"status": "skipped", "reason": "移動済み、またはファイルがありません。"}
        if source.is_symlink():
            return {"status": "skipped", "reason": "リンクのため移動しません。"}
        watch_dirs = load_lora_config()["watch_dirs"]
        if is_path_within_roots(source, watch_dirs):
            return {"status": "skipped", "reason": "登録済みフォルダー内です。"}
        stat = source.stat()
        if (stat.st_size, stat.st_mtime_ns) != snapshot or stat.st_size == 0 or any(Path(str(source) + suffix).exists() for suffix in (".crdownload", ".part", ".tmp")):
            return {"status": "skipped", "reason": "ダウンロード中、更新中、または空のファイルです。"}
        metadata, matched_pending = recovery_metadata(source, record, page_metadata)
        if source.suffix.lower() in WORKFLOW_EXTENSIONS:
            read_workflow_package(source)  # Reject ordinary JSON/ZIP and unsafe archives before moving.
        if source.suffix.lower() == ".safetensors" and not guess_import_model_family_from_payload(metadata):
            return {"status": "skipped", "reason": "モデル種別を確定できません。配布ページで対象を選んで再実行してください。"}
        family = collapse_model_family(detect_import_model_family(source, metadata))
        if family not in {"lora", "checkpoint", "embedding", "workflow"}:
            return {"status": "skipped", "reason": "モデル種別を判定できません。"}
        # Recovery must not use the existing 'first folder' fallback for another family.
        target = resolve_import_target_dir(matched_pending.get("target_dir", "") if matched_pending else "", watch_dirs, source, metadata)
        target_family = collapse_model_family(infer_watch_dir_model_family(target))
        if target_family and target_family != family:
            return {"status": "skipped", "reason": f"{label_model_family(family)} 用の保存先を登録してください。"}
        metadata["model_family"] = family
        metadata.setdefault("display_name", normalize_text(metadata.get("title")) or normalize_text(metadata.get("source_name")) or source.stem)
        current_stat = source.stat()
        if (current_stat.st_size, current_stat.st_mtime_ns) != snapshot:
            return {"status": "skipped", "reason": "照合中にファイルが変更されました。完了後に再実行してください。"}
        result = import_downloaded_lora(str(source), metadata, str(target))
        if matched_pending:
            clear_pending_download_import()
            set_last_download_import_result("complete", "ダウンロード完了履歴から取り込みました。", **result)
        return {"status": "imported", "path": result["path"], "target_dir": result["target_dir"],
                "reason": "登録して移動しました。", "imported_count": result.get("imported_count", 1)}


def run_download_recovery(payload: dict) -> None:
    try:
        downloads_dir = get_default_downloads_dir()
        candidates = {}
        blocked = set()
        for record in payload.get("downloads", []):
            if not isinstance(record, dict) or not isinstance(record.get("source_path"), str):
                continue
            source = Path(record["source_path"])
            if not source.is_absolute() or source.suffix.lower() not in DOWNLOAD_EXTENSIONS:
                continue
            key = os.path.normcase(str(source.absolute()))
            if record.get("download_state") != "complete":
                blocked.add(key)
                continue
            if source.is_file():
                candidates[key] = (source, record)
        # Scan the Windows Downloads directory itself, plus completed Chrome paths
        # (which may be in subfolders or a custom download directory).
        if payload.get("scan_downloads_dir", True) and downloads_dir.is_dir():
            for source in downloads_dir.iterdir():
                if source.is_file() and source.suffix.lower() in DOWNLOAD_EXTENSIONS:
                    key = os.path.normcase(str(source.absolute()))
                    candidates.setdefault(key, (source, {}))
        if len(candidates) > 5000:
            raise ValueError("候補が5000件を超えています。Downloadsを分割してから再実行してください。")
        snapshots = {}
        for key, (source, _) in candidates.items():
            try:
                stat = source.stat()
                snapshots[key] = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                pass
        with DOWNLOAD_RECOVERY_LOCK:
            DOWNLOAD_RECOVERY_JOB.update(total=len(candidates), downloads_dir=str(downloads_dir))
        if candidates:
            sleep(PENDING_IMPORT_SETTLE_SECONDS)
        page_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        for key, (source, record) in candidates.items():
            try:
                if key in blocked:
                    result = {"status": "skipped", "reason": "Chromeで未完了のダウンロードです。"}
                elif key not in snapshots:
                    result = {"status": "skipped", "reason": "ファイルを確認できません。"}
                else:
                    result = recover_download_file(source, record, page_metadata, snapshots[key])
            except (ValueError, zipfile.BadZipFile, struct.error) as exc:
                result = {"status": "skipped", "reason": str(exc)}
            except Exception as exc:
                result = {"status": "failed", "reason": str(exc)}
            result.update(source_path=str(source), filename=source.name)
            with DOWNLOAD_RECOVERY_LOCK:
                DOWNLOAD_RECOVERY_JOB["results"].append(result)
                DOWNLOAD_RECOVERY_JOB[result["status"]] += 1
                DOWNLOAD_RECOVERY_JOB["processed"] += 1
                DOWNLOAD_RECOVERY_JOB["message"] = f"{DOWNLOAD_RECOVERY_JOB['processed']} / {DOWNLOAD_RECOVERY_JOB['total']}件を確認しました。"
        start_background_scan(force=True)
        with DOWNLOAD_RECOVERY_LOCK:
            DOWNLOAD_RECOVERY_JOB.update(status="complete", message="ダウンロードフォルダーの調査が完了しました。")
    except Exception as exc:
        with DOWNLOAD_RECOVERY_LOCK:
            DOWNLOAD_RECOVERY_JOB.update(status="error", message=str(exc))


def download_remote_model_to_temp_file(
    download_url: str,
    *,
    suggested_filename: str = "",
    cookie_header: str = "",
    referer_url: str = "",
) -> Path:
    normalized_url = normalize_text(download_url)
    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Download URL is invalid")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135 Safari/537.36",
        "Accept": "*/*",
    }
    if referer_url:
        headers["Referer"] = referer_url
    request = urllib.request.Request(normalized_url, headers=headers)
    if cookie_header:
        if (parsed.scheme != "https" or not is_civitai_page_host(parsed.hostname or "")
                or parsed.username is not None or parsed.password is not None):
            raise ValueError("認証Cookieを送信できるのはHTTPSのCivitai取得先だけです。")
        # urllib's redirect handler omits unredirected_headers from new requests.
        request.add_unredirected_header("Cookie", cookie_header)
    temp_dir = DATA_ROOT / "_bridge_downloads"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response_url = normalize_text(getattr(response, "geturl", lambda: normalized_url)() or normalized_url)
            content_type = normalize_text(response.headers.get("Content-Type")).lower()
            if "/login" in urlparse(response_url).path.lower():
                raise PermissionError("Civitai download was unauthorized")
            if content_type.startswith("text/html"):
                raise PermissionError("Civitai download was unauthorized")
            content_disposition = normalize_text(response.headers.get("Content-Disposition"))
            resolved_name = sanitize_download_filename(
                suggested_filename or parse_content_disposition_filename(content_disposition),
                response_url or normalized_url,
            )
            temp_path = build_unique_import_path(temp_dir, resolved_name)
            with temp_path.open("wb") as handle:
                first_chunk = True
                while True:
                    chunk = response.read(REMOTE_DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    if first_chunk:
                        first_chunk = False
                        preview = chunk[:256].lstrip()
                        lowered = preview.lower()
                        if lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html"):
                            raise PermissionError("Civitai download was unauthorized")
                    handle.write(chunk)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise PermissionError("Civitai download was unauthorized") from exc
        raise ValueError(f"Remote download failed with HTTP {exc.code}") from exc
    except PermissionError:
        raise
    except OSError as exc:
        raise ValueError("Remote download failed") from exc

    return temp_path


def download_remote_model_and_import(
    download_url: str,
    metadata_payload: dict | None = None,
    target_dir_text: str = "",
    *,
    suggested_filename: str = "",
    cookie_header: str = "",
    referer_url: str = "",
) -> dict:
    temp_path = download_remote_model_to_temp_file(
        download_url,
        suggested_filename=suggested_filename,
        cookie_header=cookie_header,
        referer_url=referer_url,
    )
    try:
        return import_downloaded_lora(str(temp_path), metadata_payload, target_dir_text)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def relative_path_from_roots(target: Path, roots: list[str]) -> str:
    resolved_target = target.resolve(strict=False)
    for root_str in roots:
        root = Path(root_str).resolve(strict=False)
        try:
            return str(resolved_target.relative_to(root))
        except ValueError:
            continue
    return target.name


def metadata_value(store_meta: dict, imported_meta: dict, key: str, default=None):
    if key in store_meta:
        return store_meta[key]
    if key in imported_meta:
        return imported_meta[key]
    return default


def normalize_model_family(value) -> str:
    text = normalize_text(value).lower()
    if not text:
        return ""

    collapsed = re.sub(r"[^a-z0-9]+", "", text)
    if not collapsed:
        return ""

    if "workflow" in collapsed:
        return "workflow"
    if any(token in collapsed for token in ("lycoris", "locon", "loha", "lokr", "ia3", "dylora")):
        return "lycoris"
    if any(token in collapsed for token in ("textualinversion", "embedding", "embeddings")):
        return "embedding"
    if any(token in collapsed for token in ("checkpoint", "checkpoints", "ckpt")):
        return "checkpoint"
    if "lora" in collapsed:
        return "lora"
    return ""


def collapse_model_family(value) -> str:
    normalized = normalize_model_family(value)
    if normalized == "lycoris":
        return "lora"
    return normalized


def label_model_family(family: str) -> str:
    normalized = collapse_model_family(family) or "other"
    return MODEL_FAMILY_LABELS.get(normalized, "Other")


def extract_header_model_family_hint(raw_meta: dict) -> str:
    hinted_family = normalize_model_family(
        first_text_value(
            raw_meta.get("model_type"),
            raw_meta.get("type"),
            raw_meta.get("ss_network_module"),
            raw_meta.get("network_module"),
        )
    )
    if hinted_family:
        return hinted_family

    architecture = normalize_text(raw_meta.get("modelspec.architecture")).lower()
    if architecture:
        if any(token in architecture for token in ("lycoris", "locon", "loha", "lokr", "ia3", "dylora")):
            return "lycoris"
        if "lora" in architecture:
            return "lora"

    if first_text_value(raw_meta.get("ss_network_module"), raw_meta.get("network_module")):
        return "lora"
    return ""


def extract_json_model_family_hint(data: dict) -> str:
    model_info = data.get("model")
    model_type = ""
    if isinstance(model_info, dict):
        model_type = normalize_text(model_info.get("type"))

    return normalize_model_family(
        first_text_value(
            data.get("modelType"),
            data.get("model_type"),
            data.get("type"),
            model_type,
        )
    )


def detect_model_family(model_path: Path, imported_meta: dict, size_bytes: int) -> str:
    if model_path.suffix.lower() == ".json" and is_workflow_file(model_path):
        return "workflow"
    hinted_family = normalize_model_family(
        imported_meta.get("model_family") or imported_meta.get("model_family_hint")
    )
    if hinted_family:
        return hinted_family

    path_text = "/".join(part.lower() for part in model_path.parts)
    if any(token in path_text for token in ("lycoris", "locon", "loha", "lokr", "ia3", "dylora")):
        return "lycoris"
    if any(token in path_text for token in ("embeddings", "embedding", "textualinversion", "textual-inversion")):
        return "embedding"
    if any(token in path_text for token in ("/checkpoints/", "/checkpoint/")):
        return "checkpoint"
    if any(token in path_text for token in ("/loras/", "/lora/")):
        return "lora"

    extension = model_path.suffix.lower()
    if extension == ".ckpt":
        return "checkpoint"
    if extension in {".pt", ".pth", ".bin"}:
        if size_bytes >= CHECKPOINT_SIZE_THRESHOLD_BYTES:
            return "checkpoint"
        return "other"
    if extension == ".safetensors":
        if size_bytes >= CHECKPOINT_SIZE_THRESHOLD_BYTES:
            return "checkpoint"
        return "lora"
    return "other"


def build_prompt_snippet(model_name: str, triggers: list[str], strength_min, strength_max, model_family: str) -> str:
    family = collapse_model_family(model_family) or "lora"
    weight = strength_max
    if weight is None:
        weight = strength_min
    if weight is None:
        weight = 0.8

    trigger_text = ", ".join(triggers)
    if family != "lora":
        return trigger_text

    parts = [f"<lora:{model_name}:{format_weight_value(weight)}>"]
    if trigger_text:
        parts.append(trigger_text)
    return ", ".join(part for part in parts if part)


def build_stat_timestamp_iso(stat_result, *attribute_names: str) -> str:
    for attribute_name in attribute_names:
        timestamp = getattr(stat_result, attribute_name, None)
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
    return ""


def generation_settings_summary(settings: dict) -> dict:
    if not settings:
        return {}
    return {key: settings.get(key) for key in ("width", "height", "cfg_scale", "seed")} | {
        "count": len(settings.get("widgets", {})) + len(settings.get("options", {})),
        "modules": [m.get("name", "") for m in settings.get("modules", [])],
    }


def save_current_forge_settings(expected_path: str = "") -> dict:
    status = forge_connector.forge_request("/library-desk/status")
    if not status.get("connected") or not status.get("snapshot"):
        raise ValueError("Forge画面を開いて再読み込みしてください。設定の読み取りを待っています。")
    snapshot = forge_connector.fresh_snapshot()
    target = Path(snapshot.get("checkpoint_path", ""))
    watch_dirs = load_lora_config()["watch_dirs"]
    if not is_valid_model_path(target, watch_dirs):
        raise ValueError("Forgeで選んだモデルがLibrary Deskの登録フォルダにありません。")
    if expected_path and str(Path(expected_path).resolve()).casefold() != str(target.resolve()).casefold():
        raise ValueError("Library Deskで選択中のモデルとForgeのモデルが異なります。Forgeのモデルを選んで保存してください。")
    settings = forge_connector.normalize_generation_settings(snapshot["settings"])
    changes = sanitize_metadata_input({"generation_settings": settings,
        "recommended_steps": str(settings["steps"]), "recommended_sampler": settings["sampler"],
        "recommended_scheduler": settings["scheduler"]})
    merge_metadata_changes([str(target.resolve())], changes)
    item = build_lora_item_snapshot(target, watch_dirs)
    return {"ok": True, "item": item, "message": f"{item['display_name']}の生成設定を保存しました。"}


def build_lora_item(model_path: Path, watch_dirs: list[str], store_meta: dict, directory_index: dict | None = None) -> dict:
    header_meta = load_embedded_safetensors_metadata(model_path)
    json_meta = {} if model_path.suffix.lower() == ".json" else load_json_sidecar(model_path, directory_index)
    imported_meta: dict = {}
    imported_meta.update(header_meta)
    imported_meta.update(json_meta)

    stat = model_path.stat()
    model_family = collapse_model_family(detect_model_family(model_path, imported_meta, stat.st_size)) or "other"
    preview_path = discover_preview_path(model_path, directory_index)
    preview_media_kind = reference_media_kind(preview_path.name) if preview_path is not None else ""
    reference_count = len(list_reference_image_paths(model_path))

    triggers = normalize_string_list(metadata_value(store_meta, imported_meta, "triggers", []))
    tags = normalize_string_list(metadata_value(store_meta, imported_meta, "tags", []))
    strength_min = normalize_optional_float(metadata_value(store_meta, imported_meta, "strength_min"))
    strength_max = normalize_optional_float(metadata_value(store_meta, imported_meta, "strength_max"))
    if strength_min is not None and strength_max is not None and strength_min > strength_max:
        strength_min, strength_max = strength_max, strength_min

    display_name = normalize_text(metadata_value(store_meta, imported_meta, "display_name"))
    source_name = normalize_text(imported_meta.get("source_name"))
    if not display_name:
        display_name = model_path.stem

    category = normalize_text(metadata_value(store_meta, imported_meta, "category"))
    if not category:
        category = guess_category(model_path)

    resolved_path = model_path.resolve()
    path_text = str(resolved_path)
    metadata_sources = []
    if header_meta:
        metadata_sources.append("header")
    if json_meta:
        metadata_sources.append("json")
    if store_meta:
        metadata_sources.append("library")

    return {
        "id": quote(path_text, safe=""),
        "path": path_text,
        "filename": model_path.name,
        "display_name": display_name,
        "source_name": source_name,
        "relative_path": relative_path_from_roots(model_path, watch_dirs),
        "directory": str(model_path.parent.resolve()),
        "extension": model_path.suffix.lower(),
        "model_family": model_family,
        "model_family_label": label_model_family(model_family),
        "size_bytes": stat.st_size,
        "created_at": build_stat_timestamp_iso(stat, "st_birthtime", "st_ctime", "st_mtime"),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "favorite": bool(metadata_value(store_meta, imported_meta, "favorite", False)),
        "rating": store_meta.get("rating", 0),
        "category": category,
        "triggers": triggers,
        "tags": tags,
        "notes": normalize_text(metadata_value(store_meta, imported_meta, "notes")),
        "recommended_steps": normalize_text(store_meta.get("recommended_steps")),
        "recommended_sampler": normalize_text(store_meta.get("recommended_sampler")),
        "recommended_scheduler": normalize_text(store_meta.get("recommended_scheduler")),
        "generation_settings_summary": generation_settings_summary(store_meta.get("generation_settings", {})),
        "author": normalize_text(metadata_value(store_meta, imported_meta, "author")),
        "base_model": normalize_text(metadata_value(store_meta, imported_meta, "base_model")),
        "source_url": normalize_text(metadata_value(store_meta, imported_meta, "source_url")),
        "source_description": normalize_text(imported_meta.get("source_description")),
        "strength_min": strength_min,
        "strength_max": strength_max,
        "strength_label": build_strength_label(strength_min, strength_max),
        "preview_available": preview_path is not None,
        "preview_media_kind": preview_media_kind,
        "preview_url": build_preview_api_url(model_path, preview_path) if preview_path else "",
        "reference_count": reference_count,
        "prompt_snippet": build_prompt_snippet(model_path.stem, triggers, strength_min, strength_max, model_family),
        "updated_at": normalize_text(store_meta.get("updated_at")),
        "metadata_sources": dedupe_keep_order(metadata_sources),
    }


def build_strength_label(strength_min, strength_max) -> str:
    if strength_min is None and strength_max is None:
        return ""
    if strength_min == strength_max:
        return format_weight_value(strength_min)
    if strength_min is None:
        return f"~{format_weight_value(strength_max)}"
    if strength_max is None:
        return f"{format_weight_value(strength_min)}~"
    return f"{format_weight_value(strength_min)}-{format_weight_value(strength_max)}"


def iter_model_files(root: Path, warnings: list[str]):
    def on_error(error: OSError) -> None:
        failed_path = getattr(error, "filename", None) or str(root)
        warnings.append(f"読み取れなかったフォルダ: {failed_path}")

    for current_root, dirnames, filenames in os.walk(root, onerror=on_error):
        dirnames.sort(key=str.lower)
        filenames.sort(key=str.lower)
        for filename in filenames:
            candidate = Path(current_root) / filename
            if candidate.suffix.lower() in MODEL_EXTENSIONS or is_workflow_file(candidate):
                yield candidate


def scan_lora_library(config: dict | None = None) -> dict:
    started = perf_counter()
    config = config or load_lora_config()
    store = load_lora_store()
    watch_dirs = config["watch_dirs"]
    warnings: list[str] = []
    items: list[dict] = []
    directory_cache: dict[str, dict] = {}

    for directory in watch_dirs:
        root = Path(directory)
        if not root.exists() or not root.is_dir():
            warnings.append(f"見つからないフォルダ: {directory}")
            continue

        for file_path in iter_model_files(root, warnings):
            resolved_path = str(file_path.resolve())
            store_meta = store.get(resolved_path, {})
            if not isinstance(store_meta, dict):
                store_meta = {}
            directory_key = str(file_path.parent.resolve())
            directory_index = directory_cache.get(directory_key)
            if directory_index is None:
                directory_index = build_directory_index(file_path.parent)
                directory_cache[directory_key] = directory_index
            items.append(build_lora_item(file_path, watch_dirs, store_meta, directory_index))

    items.sort(
        key=lambda item: (
            0 if item["favorite"] else 1,
            item["display_name"].lower(),
            item["filename"].lower(),
        )
    )

    categories: dict[str, int] = {}
    families: dict[str, int] = {}
    for item in items:
        category_key = item["category"] or "uncategorized"
        categories[category_key] = categories.get(category_key, 0) + 1
        family_key = item.get("model_family") or "other"
        families[family_key] = families.get(family_key, 0) + 1

    return {
        "config": config,
        "warnings": warnings,
        "scanned_at": utc_now_iso(),
        "scan_duration_ms": int((perf_counter() - started) * 1000),
        "stats": {
            "total": len(items),
            "favorites": sum(1 for item in items if item["favorite"]),
            "with_preview": sum(1 for item in items if item["preview_available"]),
            "categories": categories,
            "families": families,
        },
        "items": items,
    }


def sanitize_metadata_input(payload: dict) -> dict:
    metadata = normalize_metadata_input(payload)
    if not any(key in EDITABLE_METADATA_KEYS for key in metadata):
        return {}
    return metadata


def reveal_in_file_browser(target: Path) -> None:
    resolved = target.resolve()
    if os.name == "nt":
        destination = resolved if resolved.is_dir() else resolved.parent
        subprocess.Popen(["explorer.exe", str(destination)])
        return
    if os.name == "posix":
        subprocess.Popen(["xdg-open", str(resolved.parent)])
        return
    raise OSError("Unsupported platform")


def pick_folder_dialog() -> str | None:
    if os.name == "nt":
        script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'LoRAフォルダを選択'
$dialog.UseDescriptionForTitle = $true
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  Write-Output $dialog.SelectedPath
}
"""
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-STA",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or "Folder picker failed")
        selected = normalize_text(result.stdout)
        return selected or None

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise OSError("Folder picker is not available") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(title="LoRAフォルダを選択")
    finally:
        root.destroy()
    selected = normalize_text(selected)
    return selected or None


# Fixed ID of the bundled Chrome extension (derived from the "key" in
# chrome_extension/manifest.json). Forks that change the key can add their
# own IDs with --extension-id or the LIBRARY_DESK_EXTENSION_IDS variable.
LIBRARY_DESK_EXTENSION_ID = "cmfddaijajbljjdalpabmocolipfkafj"
EXTENSION_IDS_ENV = "LIBRARY_DESK_EXTENSION_IDS"
LAN_TOKEN_COOKIE = "library_desk_token"
LAN_TOKEN_HEADER = "X-Library-Desk-Token"
LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}
# Files a phone may fetch before it has the token cookie (PWA metadata only).
LAN_TOKEN_EXEMPT_PATHS = {"/manifest.webmanifest", "/app-icon.svg", "/favicon.ico"}


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    # hashlib.file_digest needs Python 3.11; keep 3.10 working.
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def configured_extension_ids(extra=()) -> frozenset:
    ids = {LIBRARY_DESK_EXTENSION_ID}
    ids.update(os.environ.get(EXTENSION_IDS_ENV, "").replace(";", ",").split(","))
    ids.update(extra or ())
    return frozenset(item.strip().lower() for item in ids if item and item.strip())


def is_allowed_host_header(host_header: str, allowed_hosts=frozenset()) -> bool:
    """Reject DNS-rebinding requests: only IP literals, localhost and explicitly
    allowed host names may be used to reach the server."""
    if not host_header:
        return False
    try:
        hostname = urlparse("//" + host_header.strip()).hostname
    except ValueError:
        return False
    if not hostname:
        return False
    hostname = hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname in allowed_hosts:
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def is_loopback_bind_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_or_create_lan_token(path: Path = LAN_TOKEN_PATH) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if len(token) >= 16:
        return token
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    return token


class Handler(BaseHTTPRequestHandler):
    web_root: Path
    extension_ids: frozenset = configured_extension_ids()
    allowed_hosts: frozenset = frozenset()
    lan_token: str | None = None

    def _is_browser_import_endpoint(self, path: str) -> bool:
        return path.startswith("/api/lora/browser-imports") or path in {
            "/api/lora/import-download",
            "/api/lora/recover-downloads",
            "/api/lora/import-download-remote",
            "/api/lora/await-download-import",
            "/api/lora/complete-pending-download",
            "/api/lora/cancel-pending-download",
        }

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        if not origin:
            return True
        try:
            parsed = urlparse(origin)
            hostname = (parsed.hostname or "").lower()
        except ValueError:
            return False
        if parsed.scheme == "chrome-extension":
            return hostname in self.extension_ids
        if parsed.scheme not in {"http", "https"}:
            return False
        if hostname in LOOPBACK_HOSTNAMES:
            return True
        host = self.headers.get("Host", "")
        return parsed.netloc == host and is_allowed_host_header(host, self.allowed_hosts)

    def _client_is_loopback(self) -> bool:
        try:
            return ipaddress.ip_address(str(self.client_address[0])).is_loopback
        except (ValueError, IndexError, TypeError):
            return False

    def _send_plain(self, status: HTTPStatus, text: str, *, content_type: str = "text/plain") -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._finish_response(data)

    def _lan_token_valid(self, parsed) -> bool:
        expected = self.lan_token or ""
        candidates = [self.headers.get(LAN_TOKEN_HEADER, "")]
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == LAN_TOKEN_COOKIE:
                candidates.append(value)
        return any(value and secrets.compare_digest(value.encode(), expected.encode()) for value in candidates)

    def _guard_request(self) -> bool:
        """Common checks for every request. Returns False after sending an error."""
        parsed = urlparse(self.path)
        if not is_allowed_host_header(self.headers.get("Host", ""), self.allowed_hosts):
            self._send_plain(HTTPStatus.FORBIDDEN, "Host is not allowed")
            return False
        # Cross-site subresource requests (e.g. <img src> from another web site)
        # carry no Origin header; never let them reach the API.
        if (
            parsed.path.startswith("/api/")
            and not self.headers.get("Origin")
            and self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site"
        ):
            self._send_api_error(HTTPStatus.FORBIDDEN, "Cross-site request is not allowed")
            return False
        if not self.lan_token or self._client_is_loopback():
            return True
        if parsed.path in LAN_TOKEN_EXEMPT_PATHS or self._lan_token_valid(parsed):
            return True
        query = parse_qs(parsed.query, keep_blank_values=True)
        supplied = (query.get("token") or [""])[0]
        if self.command == "GET" and supplied and secrets.compare_digest(supplied.encode(), self.lan_token.encode()):
            query.pop("token", None)
            remaining = "&".join(f"{quote(k)}={quote(v)}" for k, values in query.items() for v in values)
            location = parsed.path + (f"?{remaining}" if remaining else "")
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header(
                "Set-Cookie",
                f"{LAN_TOKEN_COOKIE}={self.lan_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000",
            )
            self.send_header("Location", location or "/")
            self.send_header("Content-Length", "0")
            self._finish_response()
            return False
        if parsed.path.startswith("/api/"):
            self._send_api_error(HTTPStatus.UNAUTHORIZED, "LAN access token is required")
        else:
            self._send_plain(
                HTTPStatus.UNAUTHORIZED,
                "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                "<title>LoRA Library Desk</title><p>LAN接続にはアクセス用トークンが必要です。"
                "PCの起動画面に表示された <code>?token=...</code> 付きのURLを開いてください。</p>",
                content_type="text/html",
            )
        return False

    def _send_cors_headers(self) -> None:
        if not self._origin_allowed():
            return
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _finish_response(self, data: bytes = b"") -> None:
        try:
            self.end_headers()
            if data:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def _serve_file(self, path: Path, *, no_store: bool = False) -> None:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        if path.name in {"service-worker.js", "manifest.webmanifest"}:
            self.send_header("Cache-Control", "no-cache")
        if path.name == "service-worker.js":
            self.send_header("Service-Worker-Allowed", "/")
        self._finish_response(data)

    def _resolve_web_path(self, rel_path: str) -> Path | None:
        target = (self.web_root / rel_path).resolve()
        web_root = self.web_root.resolve()
        if web_root not in target.parents and target != web_root:
            return None
        if not target.exists() or not target.is_file():
            return None
        return target

    def _send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if self._is_browser_import_endpoint(urlparse(self.path).path):
            self._send_cors_headers()
        self._finish_response(data)

    def _send_api_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)

    def _read_json_body(self) -> dict | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            return None

        if content_length < 0 or content_length > 128 * 1024 * 1024:
            return None
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def do_OPTIONS(self) -> None:
        if not self._guard_request():
            return
        if not self._origin_allowed():
            return self._send_api_error(HTTPStatus.FORBIDDEN, "Origin is not allowed")
        parsed = urlparse(self.path)
        if not self._is_browser_import_endpoint(parsed.path):
            self.send_response(HTTPStatus.NO_CONTENT)
            self._finish_response()
            return

        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self._finish_response()

    def _handle_lora_preview(self, parsed) -> None:
        watch_dirs = load_lora_config()["watch_dirs"]
        query = parse_qs(parsed.query)
        model_path = Path(query.get("path", [""])[0])
        if not is_valid_model_path(model_path, watch_dirs):
            return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

        preview_query = normalize_text(query.get("preview", [""])[0])
        preview_path = Path(preview_query) if preview_query else None
        if preview_path is not None and not is_valid_preview_media_path(preview_path, watch_dirs):
            preview_path = None
        if preview_path is None:
            preview_path = discover_preview_path(model_path)
        if preview_path is None:
            return self._send_api_error(HTTPStatus.NOT_FOUND, "Preview media not found")

        return self._serve_file(preview_path, no_store=True)

    def _handle_lora_reference_image(self, parsed) -> None:
        watch_dirs = load_lora_config()["watch_dirs"]
        query = parse_qs(parsed.query)
        model_path = Path(query.get("path", [""])[0])
        if not is_valid_model_path(model_path, watch_dirs):
            return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

        image_query = normalize_text(query.get("file", [""])[0])
        image_path = Path(image_query) if image_query else None
        if image_path is not None and not is_valid_reference_image_path(image_path, model_path, watch_dirs):
            image_path = None
        if image_path is None:
            references = list_reference_image_paths(model_path)
            image_path = references[0] if references else None
        if image_path is None:
            return self._send_api_error(HTTPStatus.NOT_FOUND, "Reference media not found")

        return self._serve_file(image_path)

    def do_GET(self) -> None:
        if not self._guard_request():
            return
        if self.path.startswith("/api/") and not self._origin_allowed():
            return self._send_api_error(HTTPStatus.FORBIDDEN, "Origin is not allowed")
        parsed = urlparse(self.path)

        if parsed.path == "/api/lora/forge/connection":
            try:
                return self._send_json({"forge_url": forge_connector.get_forge_url()})
            except ValueError as exc:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))

        if parsed.path == "/api/lora/forge/status":
            try:
                status = forge_connector.forge_request("/library-desk/status")
                return self._send_json(status)
            except ValueError as exc:
                return self._send_json({"connected": False, "error": str(exc)})

        if parsed.path == "/api/lora/forge/catalog":
            try:
                return self._send_json(forge_connector.forge_request("/library-desk/catalog"))
            except ValueError as exc:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))

        if parsed.path == "/api/lora/forge/item-settings":
            target = Path(parse_qs(parsed.query).get("path", [""])[0])
            if not is_valid_model_path(target, load_lora_config()["watch_dirs"]):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "Model file not found")
            metadata = load_lora_store([str(target.resolve())]).get(str(target.resolve()), {})
            return self._send_json({"settings": metadata.get("generation_settings", {})})

        if parsed.path in {"/", "/scenario", "/scenario/"}:
            return self._serve_file(self.web_root / "index.html")

        if parsed.path in {"/lora", "/lora/"}:
            return self._serve_file(self.web_root / "lora" / "index.html")

        root_assets = {
            "/service-worker.js": "service-worker.js",
            "/manifest.webmanifest": "manifest.webmanifest",
            "/app-icon.svg": "app-icon.svg",
            "/offline.html": "offline.html",
        }
        if parsed.path in root_assets:
            return self._serve_file(self.web_root / root_assets[parsed.path])

        if parsed.path.startswith("/static/"):
            rel_path = parsed.path.replace("/static/", "", 1)
            file_path = self._resolve_web_path(rel_path)
            if file_path is not None:
                return self._serve_file(file_path)
            return self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        if parsed.path == "/api/lora/config":
            return self._send_json(load_lora_config())

        if parsed.path == "/api/lora/library":
            return self._send_json(get_scan_state_payload())

        if parsed.path == "/api/lora/recover-downloads":
            return self._send_json(get_download_recovery_status())

        if parsed.path == "/api/lora/pending-download":
            return self._send_json(get_pending_download_import_payload())

        if parsed.path == "/api/lora/browser-imports":
            return self._send_json({"items": load_browser_imports()})

        if parsed.path == "/api/lora/preview":
            return self._handle_lora_preview(parsed)

        if parsed.path == "/api/lora/reference-image":
            return self._handle_lora_reference_image(parsed)

        if parsed.path == "/api/lora/references":
            watch_dirs = load_lora_config()["watch_dirs"]
            model_path = Path(parse_qs(parsed.query).get("path", [""])[0])
            if not is_valid_model_path(model_path, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")
            items = build_reference_image_items(model_path)
            return self._send_json({"ok": True, "path": str(model_path.resolve()), "count": len(items), "items": items})

        if parsed.path == "/api/lora/encyclopedia-queue":
            with ENCYCLOPEDIA_EXPORT_QUEUE_LOCK:
                items = list(ENCYCLOPEDIA_EXPORT_QUEUE)
                ENCYCLOPEDIA_EXPORT_QUEUE.clear()
            return self._send_json({"ok": True, "items": items})

        if parsed.path == "/health":
            body = b"ok"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._finish_response(body)
            return

        return self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        if not self._guard_request():
            return
        if not self._origin_allowed():
            return self._send_api_error(HTTPStatus.FORBIDDEN, "Origin is not allowed")
        try:
            return self._do_POST()
        except (ValueError, TypeError) as exc:
            return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))
        except (OSError, sqlite3.Error):
            return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "ファイルまたはDBへ保存できませんでした。")

    def _do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json_body()
        if payload is None:
            return self._send_api_error(HTTPStatus.BAD_REQUEST, "Invalid JSON payload")

        if parsed.path == "/api/lora/forge/connection":
            forge_url = forge_connector.normalize_forge_url(payload.get("forge_url"))
            write_json_file(forge_connector.CONNECTION_CONFIG_PATH, {"forge_url": forge_url})
            return self._send_json({"ok": True, "forge_url": forge_url})

        if parsed.path == "/api/lora/forge/send":
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, load_lora_config()["watch_dirs"]):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "Model file not found")
            item = build_lora_item_snapshot(target)
            return self._send_json(forge_connector.queue_library_item(item, payload.get("settings", {})))

        if parsed.path == "/api/lora/forge/save-current":
            return self._send_json(save_current_forge_settings(normalize_text(payload.get("path"))))

        if parsed.path == "/api/lora/encyclopedia-queue":
            path = payload.get("path", "")
            if path:
                with ENCYCLOPEDIA_EXPORT_QUEUE_LOCK:
                    ENCYCLOPEDIA_EXPORT_QUEUE.append({
                        "path": str(path),
                        "prompt": payload.get("prompt", ""),
                        "negative_prompt": payload.get("negative_prompt", ""),
                        "raw_parameters": payload.get("raw_parameters", ""),
                        "resources_used": payload.get("resources_used", []),
                    })
            return self._send_json({"ok": True, "queued": 1 if path else 0})

        if parsed.path == "/api/lora/config":
            config = save_lora_config(payload)
            return self._send_json(config)

        if parsed.path == "/api/lora/scan":
            force = bool(payload.get("force", False))
            return self._send_json(start_background_scan(force=force))

        if parsed.path == "/api/lora/item":
            watch_dirs = load_lora_config()["watch_dirs"]
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

            path_key = str(target.resolve())
            changes = sanitize_metadata_input(payload.get("metadata", {}))
            if not changes:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, "No editable metadata was provided")
            updated = merge_metadata_changes([path_key], changes).get(path_key, {})
            return self._send_json({"ok": True, "path": path_key, "metadata": updated})

        if parsed.path == "/api/lora/items/bulk":
            watch_dirs = load_lora_config()["watch_dirs"]
            raw_paths = payload.get("paths", [])
            if not isinstance(raw_paths, list) or not raw_paths:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, "No LoRA paths were provided")

            resolved_paths: list[str] = []
            for raw_path in raw_paths:
                target = Path(normalize_text(raw_path))
                if not is_valid_model_path(target, watch_dirs):
                    return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")
                resolved_paths.append(str(target.resolve()))

            changes = sanitize_metadata_input(payload.get("changes", {}))
            if not changes:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, "No editable metadata was provided")

            updated = merge_metadata_changes(resolved_paths, changes)
            return self._send_json(
                {
                    "ok": True,
                    "count": len(updated),
                    "paths": list(updated.keys()),
                    "metadata": changes,
                }
            )

        if parsed.path == "/api/lora/references/upload":
            watch_dirs = load_lora_config()["watch_dirs"]
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

            try:
                result = save_reference_images_for_model(
                    target.resolve(),
                    payload.get("files", []),
                    include_items=not bool(payload.get("summary_only")),
                )
            except ValueError as exc:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))
            except OSError:
                return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Reference media could not be saved")

            if isinstance(result, dict):
                count = int(result.get("count") or 0)
                items = result.get("items") if isinstance(result.get("items"), list) else []
            else:
                items = result if isinstance(result, list) else []
                count = len(items)

            return self._send_json(
                {
                    "ok": True,
                    "path": str(target.resolve()),
                    "count": count,
                    "items": items,
                    "encyclopedia_queued": int(result.get("encyclopedia_queued") or 0) if isinstance(result, dict) else 0,
                },
                HTTPStatus.CREATED,
            )

        if parsed.path == "/api/lora/preview/upload":
            watch_dirs = load_lora_config()["watch_dirs"]
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

            resolved_target = target.resolve(strict=False)
            remote_preview_url = normalize_text(payload.get("url"))
            referer_url = normalize_text(payload.get("referer_url"))
            try:
                if remote_preview_url:
                    preview_path = replace_remote_preview_for_model(resolved_target, remote_preview_url, referer_url)
                    if preview_path is None:
                        return self._send_api_error(HTTPStatus.BAD_REQUEST, "Preview media could not be downloaded")
                else:
                    preview_path = replace_preview_image_for_model(resolved_target, payload.get("file"))
            except ValueError as exc:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))
            except OSError:
                return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Preview media could not be saved")

            item = build_lora_item_snapshot(resolved_target, watch_dirs, cache_bust=str(time_ns()))
            return self._send_json(
                {
                    "ok": True,
                    "path": str(resolved_target),
                    "preview_path": str(preview_path),
                    "item": item,
                },
                HTTPStatus.CREATED,
            )

        if parsed.path == "/api/lora/preview/delete":
            watch_dirs = load_lora_config()["watch_dirs"]
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

            resolved_target = target.resolve(strict=False)
            deleted_paths = delete_preview_images_for_model(resolved_target)
            if not deleted_paths:
                return self._send_api_error(HTTPStatus.NOT_FOUND, "Preview media not found")

            item = build_lora_item_snapshot(resolved_target, watch_dirs)
            return self._send_json(
                {
                    "ok": True,
                    "path": str(resolved_target),
                    "deleted": len(deleted_paths),
                    "deleted_paths": deleted_paths,
                    "item": item,
                }
            )

        if parsed.path == "/api/lora/references/delete":
            watch_dirs = load_lora_config()["watch_dirs"]
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

            image_path = Path(normalize_text(payload.get("file")))
            if not is_valid_reference_image_path(image_path, target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "Reference media not found")

            try:
                deleted_path = delete_reference_image_for_model(target.resolve(), image_path.resolve())
            except ValueError as exc:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))
            except OSError:
                return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Reference media could not be deleted")

            items = build_reference_image_items(target.resolve())
            return self._send_json(
                {
                    "ok": True,
                    "path": str(target.resolve()),
                    "file": deleted_path,
                    "count": len(items),
                    "items": items,
                }
            )

        if parsed.path == "/api/lora/browser-imports":
            record = store_browser_import(payload)
            return self._send_json({"ok": True, "item": record}, HTTPStatus.CREATED)

        if parsed.path == "/api/lora/browser-imports/apply":
            watch_dirs = load_lora_config()["watch_dirs"]
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

            try:
                import_id = int(payload.get("import_id"))
            except (TypeError, ValueError):
                return self._send_api_error(HTTPStatus.BAD_REQUEST, "Import id is invalid")

            try:
                consume = bool(payload.get("consume"))
                resolved_target = str(target.resolve())
                updated = apply_browser_import_to_path(import_id, resolved_target, consume=consume)
                item_snapshot = build_library_item_snapshot(resolved_target)
            except KeyError:
                return self._send_api_error(HTTPStatus.NOT_FOUND, "Browser import not found")
            except FileNotFoundError:
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")
            response_payload = {
                "ok": True,
                "path": str(target.resolve()),
                "metadata": updated,
                "item": item_snapshot,
            }
            if consume:
                response_payload["import_id"] = import_id
            return self._send_json(response_payload)

        if parsed.path == "/api/lora/browser-imports/reference":
            watch_dirs = load_lora_config()["watch_dirs"]
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

            try:
                import_id = int(payload.get("import_id"))
            except (TypeError, ValueError):
                return self._send_api_error(HTTPStatus.BAD_REQUEST, "Import id is invalid")

            try:
                result = add_browser_import_reference_to_path(
                    import_id,
                    str(target.resolve()),
                    bool(payload.get("consume")),
                    include_items=not bool(payload.get("summary_only")),
                )
            except KeyError:
                return self._send_api_error(HTTPStatus.NOT_FOUND, "Browser import not found")
            except ValueError as exc:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))
            except OSError:
                return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Reference image could not be saved")

            return self._send_json(
                {
                    "ok": True,
                    "path": str(target.resolve()),
                    "count": result["count"] if isinstance(result, dict) else len(result),
                    "items": result["items"] if isinstance(result, dict) else result,
                    "encyclopedia_queued": int(result.get("encyclopedia_queued") or 0) if isinstance(result, dict) else 0,
                }
            )

        if parsed.path == "/api/lora/browser-imports/delete":
            try:
                import_id = int(payload.get("import_id"))
            except (TypeError, ValueError):
                return self._send_api_error(HTTPStatus.BAD_REQUEST, "Import id is invalid")

            if not delete_browser_import(import_id):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "Browser import not found")
            return self._send_json({"ok": True, "import_id": import_id})

        if parsed.path == "/api/lora/recover-downloads":
            return self._send_json(start_download_recovery(payload))

        if parsed.path == "/api/lora/import-download":
            try:
                result = import_downloaded_lora(
                    normalize_text(payload.get("source_path")),
                    payload.get("metadata", {}),
                    normalize_text(payload.get("target_dir")),
                )
            except FileNotFoundError as exc:
                return self._send_api_error(HTTPStatus.NOT_FOUND, str(exc) or "Downloaded file not found")
            except ValueError as exc:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))
            except OSError:
                return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Downloaded file could not be imported")

            start_background_scan(force=True)
            return self._send_json({"ok": True, **result})

        if parsed.path == "/api/lora/import-download-remote":
            try:
                result = download_remote_model_and_import(
                    normalize_text(payload.get("download_url")),
                    payload.get("metadata", {}),
                    normalize_text(payload.get("target_dir")),
                    suggested_filename=normalize_text(payload.get("suggested_filename")),
                    cookie_header=normalize_text(payload.get("cookie_header")),
                    referer_url=normalize_text(payload.get("referer_url")),
                )
            except PermissionError:
                return self._send_api_error(HTTPStatus.FORBIDDEN, "Civitai download was unauthorized")
            except ValueError as exc:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))
            except OSError:
                return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Remote downloaded file could not be imported")

            start_background_scan(force=True)
            return self._send_json({"ok": True, **result})

        if parsed.path == "/api/lora/complete-pending-download":
            try:
                candidates = payload.get("downloads", [payload])
                if not isinstance(candidates, list) or len(candidates) > 100:
                    raise ValueError("Invalid download candidates")
                result = {"matched": False}
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    result = complete_browser_pending_download(dict(candidate, created_epoch=payload.get("created_epoch")))
                    if result.get("matched"):
                        break
            except (ValueError, OSError) as exc:
                return self._send_api_error(HTTPStatus.BAD_REQUEST, str(exc))
            return self._send_json({"ok": True, **result})

        if parsed.path == "/api/lora/cancel-pending-download":
            return self._send_json({"ok": True, **cancel_pending_download(payload.get("created_epoch"))})

        if parsed.path == "/api/lora/await-download-import":
            armed = arm_pending_download_import(
                normalize_text(payload.get("target_dir")),
                payload.get("metadata", {}),
                normalize_text(payload.get("expected_filename")),
                normalize_text(payload.get("downloads_dir")),
                bool(payload.get("auto_triggered")),
            )
            return self._send_json({"ok": True, **armed})

        if parsed.path == "/api/lora/reveal":
            watch_dirs = load_lora_config()["watch_dirs"]
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

            try:
                reveal_in_file_browser(target)
            except OSError:
                return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Explorer could not be opened")
            return self._send_json({"ok": True})

        if parsed.path == "/api/lora/delete":
            watch_dirs = load_lora_config()["watch_dirs"]
            target = Path(normalize_text(payload.get("path")))
            if not is_valid_model_path(target, watch_dirs):
                return self._send_api_error(HTTPStatus.NOT_FOUND, "LoRA file not found")

            try:
                deleted_path = delete_lora_file(target)
            except OSError:
                return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "LoRA file could not be deleted")
            return self._send_json({"ok": True, "path": deleted_path})

        if parsed.path == "/api/lora/pick-folder":
            try:
                selected = pick_folder_dialog()
            except (OSError, subprocess.SubprocessError):
                return self._send_api_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Folder picker could not be opened")
            return self._send_json({"path": selected or "", "cancelled": not bool(selected)})

        return self._send_api_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, fmt: str, *args) -> None:
        return


def build_handler(web_root: Path, *, lan_token: str | None = None, allowed_hosts=(), extension_ids=()):
    class BoundHandler(Handler):
        pass

    BoundHandler.web_root = web_root
    BoundHandler.lan_token = lan_token or None
    BoundHandler.allowed_hosts = frozenset(h.strip().lower().rstrip(".") for h in allowed_hosts if h and h.strip())
    BoundHandler.extension_ids = configured_extension_ids(extension_ids)
    return BoundHandler


def discover_private_ipv4_addresses() -> list[str]:
    found: set[str] = set()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            found.add(sock.getsockname()[0])
    except OSError:
        pass

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
        for info in infos:
            address = info[4][0]
            found.add(address)
    except socket.gaierror:
        pass

    filtered = []
    for address in sorted(found):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_loopback:
            continue
        if ip.is_private:
            filtered.append(address)

    return filtered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LoRA Library Desk")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host. Use 0.0.0.0 to allow access from other devices on the LAN.",
    )
    parser.add_argument("--port", type=int, default=8787, help="Server port")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Allow access from other devices on the same local network (requires the access token shown at startup).",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="NAME",
        help="Extra host name allowed in the Host header (e.g. mypc.local). IP addresses and localhost are always allowed.",
    )
    parser.add_argument(
        "--extension-id",
        action="append",
        default=[],
        metavar="ID",
        help="Additional Chrome extension ID allowed to call the API (only needed for a modified extension).",
    )
    parser.add_argument(
        "--reset-lan-token",
        action="store_true",
        help="Generate a new LAN access token (devices must open the new URL).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically",
    )
    parser.add_argument(
        "--open-path",
        default="/lora",
        help="Path to open in the browser automatically. Default: /lora",
    )
    return parser.parse_args()


def main() -> None:
    init_lora_db()
    restore_download_state()
    start_watch_dir_monitor()
    initial_config = load_lora_config()
    if initial_config.get("watch_dirs"):
        start_background_scan(config=initial_config, force=False)
    args = parse_args()
    web_root = APP_ROOT / "web"
    bind_host = "0.0.0.0" if args.lan else args.host
    remote_access = not is_loopback_bind_host(bind_host)
    lan_token = None
    if remote_access:
        if args.reset_lan_token:
            LAN_TOKEN_PATH.unlink(missing_ok=True)
        lan_token = load_or_create_lan_token()
    allowed_hosts = list(args.allowed_host)
    if not remote_access or bind_host not in {"0.0.0.0", "::"}:
        allowed_hosts.append(bind_host)
    handler = build_handler(
        web_root,
        lan_token=lan_token,
        allowed_hosts=allowed_hosts,
        extension_ids=args.extension_id,
    )
    server = ThreadingHTTPServer((bind_host, args.port), handler)
    local_url = f"http://127.0.0.1:{args.port}"
    open_path = args.open_path if args.open_path.startswith("/") else f"/{args.open_path}"
    browser_url = f"{local_url}{open_path}"

    print(f"LoRA Library Desk: {local_url}/lora")
    print(f"Root: {local_url}")
    if remote_access:
        lan_addresses = discover_private_ipv4_addresses()
        if lan_addresses:
            print("Android / other devices (keep this URL private; it contains the access token):")
            for address in lan_addresses:
                print(f"  http://{address}:{args.port}/lora?token={lan_token}")
        else:
            print("Android / other devices: local IP address could not be detected automatically.")
            print("  Check your PC's Wi-Fi IPv4 address and open that on Android.")
            print(f"  Append ?token={lan_token} to the URL the first time.")
    print("Stop: Ctrl+C")

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(browser_url)).start()

    server.serve_forever()


if __name__ == "__main__":
    main()

"""Local Forge Neo connection. No generation or file transfer endpoints."""
import json
import re
import importlib.util
import urllib.error
import urllib.request
from time import monotonic, sleep
from pathlib import Path

FORGE_URL = "http://127.0.0.1:7860"
CONNECTION_CONFIG_PATH = Path(__file__).parent / 'data' / 'connection.json'
_local_spec = importlib.util.spec_from_file_location('desk_local_connection', Path(__file__).parent / 'local_connection.py')
_local = importlib.util.module_from_spec(_local_spec)
_local_spec.loader.exec_module(_local)


def get_forge_url():
    try:
        config = json.loads(CONNECTION_CONFIG_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        config = {}
    except (ValueError, OSError) as exc:
        raise ValueError('Forge接続設定を読み取れません。接続設定を保存し直してください。') from exc
    if not isinstance(config, dict):
        raise ValueError('Forge接続設定の形式が不正です。')
    return _local.normalize_loopback_url(config.get('forge_url', FORGE_URL))


def normalize_forge_url(value):
    return _local.normalize_loopback_url(value)
SETTING_KEYS = ("prompt", "negative_prompt", "steps", "sampler", "scheduler", "cfg_scale", "width", "height", "seed")


def fresh_snapshot():
    requested = forge_request("/library-desk/capture", {})
    deadline = monotonic() + 8
    while monotonic() < deadline:
        status = forge_request("/library-desk/status")
        if status.get("snapshot") and status["snapshot"]["captured_at"] >= requested["requested_at"]:
            return status["snapshot"]
        sleep(0.3)
    raise ValueError("Forge画面から最新設定を読み取れませんでした。Forgeの画面を開いてください。")


def normalize_generation_settings(value):
    if not isinstance(value, dict):
        raise ValueError("生成設定はオブジェクトで指定してください。")
    result = {}
    if "options" in value:
        options = value["options"]
        if not isinstance(options, dict) or len(options) > 300 or len(json.dumps(options, ensure_ascii=False, allow_nan=False)) > 64000:
            raise ValueError("生成オプションの形式が不正です。")
        result["options"] = options
    if "widgets" in value:
        widgets = value["widgets"]
        if not isinstance(widgets, dict) or len(widgets) > 600 or len(json.dumps(widgets, ensure_ascii=False)) > 256000:
            raise ValueError("Forge設定の項目数またはサイズが不正です。")
        for key, entry in widgets.items():
            if not isinstance(key, str) or not isinstance(entry, dict) or "value" not in entry or not isinstance(entry["value"], (str, int, float, bool, list, type(None))):
                raise ValueError("Forge設定の形式が不正です。")
        result["widgets"] = widgets
    if "modules" in value:
        modules = value["modules"]
        if not isinstance(modules, list) or len(modules) > 32 or any(not isinstance(m, dict) or not isinstance(m.get("path"), str) for m in modules):
            raise ValueError("VAE／エンコーダーの設定が不正です。")
        result["modules"] = modules
    for key in ("preset", "dtype"):
        if key in value and isinstance(value[key], str):
            result[key] = value[key]
    for key in SETTING_KEYS:
        if key not in value:
            continue
        raw = value[key]
        if key in ("prompt", "negative_prompt", "sampler", "scheduler"):
            if not isinstance(raw, str) or len(raw) > (16000 if "prompt" in key else 160):
                raise ValueError(f"{key}が不正です。")
            result[key] = raw if "prompt" in key else raw.strip()
        else:
            limits = {"steps": (1, 150), "cfg_scale": (1, 24), "width": (64, 2048), "height": (64, 2048), "seed": (-1, 9007199254740991)}
            low, high = limits[key]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not low <= raw <= high:
                raise ValueError(f"{key}は{low}〜{high}で指定してください。")
            if key != "cfg_scale" and int(raw) != raw:
                raise ValueError(f"{key}は整数で指定してください。")
            if key in ("width", "height") and int(raw) % 8:
                raise ValueError("画像サイズは8の倍数で指定してください。")
            result[key] = float(raw) if key == "cfg_scale" else int(raw)
    return result


def forge_request(route, data=None):
    base_url = get_forge_url()
    req = urllib.request.Request(base_url + route,
        data=json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None,
        headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Forgeの応答が大きすぎます。")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(8192)).get("detail", "")
        except (ValueError, AttributeError):
            detail = ""
        if exc.code == 404:
            raise ValueError("Forge連携機能がまだ読み込まれていません。Forgeの再起動が必要です。") from exc
        raise ValueError(detail or f"Forgeとの通信に失敗しました（{exc.code}）。") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"Forgeに接続できません。{base_url} の起動と接続設定を確認してください。") from exc


def matching_resource(path, entries):
    target = str(Path(path).resolve()).casefold()
    for entry in entries:
        if str(Path(entry.get("path", "")).resolve()).casefold() == target:
            return entry
    raise ValueError("選択したファイルがForgeに登録されていません。Forge側でモデル一覧を更新してください。")


def queue_library_item(item, settings):
    status = forge_request("/library-desk/status")
    if status.get("busy"):
        raise ValueError("Forgeは生成中です。生成が終わってから送信してください。")
    if not status.get("connected"):
        raise ValueError("Forgeの画面を開いて再読み込みしてください。連携画面の接続を待っています。")
    catalog = forge_request("/library-desk/catalog")
    family = item.get("model_family")
    if family == "checkpoint":
        model = matching_resource(item["path"], catalog["checkpoints"])
        params = normalize_generation_settings(settings)
        if params.get("sampler") and params["sampler"] not in catalog["samplers"]:
            raise ValueError("選択したSamplerがForgeにありません。")
        if params.get("scheduler") and params["scheduler"] not in catalog["schedulers"]:
            raise ValueError("選択したSchedulerがForgeにありません。")
        payload = {"mode": "checkpoint", "checkpoint": model["title"], "settings": params}
    elif family == "lora":
        model = matching_resource(item["path"], catalog["loras"])
        weight = item.get("strength_max")
        if weight is None:
            weight = item.get("strength_min")
        if weight is None:
            weight = 0.8
        payload = {"mode": "lora", "name": model["name"], "weight": weight, "triggers": item.get("triggers", [])}
    else:
        raise ValueError("送信できるのはチェックポイントとLoRAです。")
    return forge_request("/library-desk/command", payload)


def reference_settings(reference, catalog):
    """Use only recorded txt2img fields, reporting incompatible metadata."""
    settings, warnings = {}, []
    for key in ("prompt", "negative_prompt"):
        if reference.get(key) or (key == "negative_prompt" and reference.get("prompt")):
            settings.update(normalize_generation_settings({key: reference.get(key) or ""}))
    for key in ("steps", "cfg_scale", "seed", "width", "height"):
        raw = reference.get(key)
        if raw is None or raw == "":
            continue
        try:
            if isinstance(raw, bool):
                raise ValueError()
            number = float(raw)
            settings.update(normalize_generation_settings({key: number}))
        except (ValueError, TypeError, OverflowError):
            label = {"steps": "Step", "cfg_scale": "CFG", "seed": "Seed", "width": "幅", "height": "高さ"}[key]
            warnings.append(f"{label}は対応範囲外のため反映していません。")

    def choice(value, choices):
        normalized = re.sub(r"[\s_]+", " ", str(value)).strip().casefold()
        return next((v for v in choices if re.sub(r"[\s_]+", " ", v).strip().casefold() == normalized), None)

    sampler = str(reference.get("sampler") or "").strip()
    scheduler = str(reference.get("scheduler") or "").strip()
    if sampler:
        selected = choice(sampler, catalog.get("samplers", []))
        if selected is None:
            for name in sorted(catalog.get("schedulers", []), key=len, reverse=True):
                suffix = " " + name
                if sampler.casefold().endswith(suffix.casefold()):
                    selected = choice(sampler[:-len(suffix)], catalog.get("samplers", []))
                    if selected:
                        scheduler = scheduler or name
                        break
        if selected:
            settings["sampler"] = selected
        else:
            warnings.append("SamplerがForgeにないため反映していません。")
    if scheduler:
        selected = choice(scheduler, catalog.get("schedulers", []))
        if selected:
            settings["scheduler"] = selected
        else:
            warnings.append("SchedulerがForgeにないため反映していません。")
    if not settings:
        raise ValueError("この画像にはSDへ送れるPromptや生成設定がありません。")
    return settings, warnings


def queue_reference_settings(reference):
    status = forge_request("/library-desk/status")
    if status.get("busy"):
        raise ValueError("Forgeは生成中です。生成が終わってから送信してください。")
    if not status.get("connected"):
        raise ValueError("Forgeの画面を開いて再読み込みしてください。")
    catalog = forge_request("/library-desk/catalog")
    settings, warnings = reference_settings(reference, catalog)
    snapshot = fresh_snapshot()
    model = matching_resource(snapshot.get("checkpoint_path", ""), catalog.get("checkpoints", []))
    result = forge_request("/library-desk/command", {
        "mode": "checkpoint", "checkpoint": model["title"], "settings": settings,
    })
    return dict(result, applied_fields=list(settings), warnings=warnings)

"""Capture/restore all serializable txt2img controls, without starting generation."""
import importlib.util
import copy
import inspect
import json
from pathlib import Path
import re
import traceback
import urllib.request
from urllib.parse import urlparse

import gradio as gr
from gradio.context import Context
from fastapi import HTTPException, Request
from modules import script_callbacks, sd_models, sd_samplers, sd_schedulers, shared
from modules.call_queue import queue_lock
from modules.options import OptionInfo
from modules_forge import main_entry

spec = importlib.util.spec_from_file_location("library_desk_core", Path(__file__).resolve().parents[1] / "bridge_core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
local_spec = importlib.util.spec_from_file_location("bridge_local_connection", Path(__file__).resolve().parents[1] / "local_connection.py")
local = importlib.util.module_from_spec(local_spec)
local_spec.loader.exec_module(local)
state = core.BridgeState()
widgets = {}
by_element = {}
txt2img_root = None
occurrences = {}
headers = {}
controls = {}
events_bound = False
BASIC = {"prompt": "txt2img_prompt", "negative_prompt": "txt2img_neg_prompt", "steps": "txt2img_steps",
         "sampler": "txt2img_sampling", "scheduler": "txt2img_scheduler", "cfg_scale": "txt2img_cfg_scale",
         "width": "txt2img_width", "height": "txt2img_height", "seed": "txt2img_seed"}
SCALAR_TYPES = {"textbox", "number", "slider", "checkbox", "dropdown", "radio", "checkboxgroup"}


def library_url():
    return local.normalize_loopback_url(shared.opts.data.get("library_desk_url", "http://127.0.0.1:8787"))


def ui_settings():
    shared.opts.add_option("library_desk_url", OptionInfo("http://127.0.0.1:8787", "Library Desk URL (local PC only)", section=("library_desk", "Library Desk")))


def stable_id(elem):
    return elem if elem and not re.fullmatch(r"(?:uuid_[a-f0-9]+|component-\d+|input-accordion-\d+(?:-checkbox)?)", elem) else None


def generation_options():
    sections = {"sd", "vae", "txt2img", "refiner", "sampler-params", "optimizations", "face-restoration", "upscaling"}
    return {key: copy.deepcopy(getattr(shared.opts, key)) for key, info in shared.opts.data_labels.items()
            if info.section and (info.section[0] in sections or key in {"eta_ddim", "eta_ancestral", "s_churn", "s_tmin", "s_tmax", "s_noise", "sigma_min", "sigma_max", "rho", "eta_noise_seed_delta", "always_discard_next_to_last_sigma", "beta_dist_alpha", "beta_dist_beta", "invert_sigmas", "use_karras_sigmas", "use_exponential_sigmas", "use_beta_sigmas"})
            and key != "sd_model_checkpoint" and not key.startswith("forge_")
            and not info.restrict_api and isinstance(getattr(shared.opts, key, None), (str, int, float, bool, list))}


def busy():
    return bool(shared.state.job_count > 0 or shared.state.job)


def after_component(component, **kwargs):
    global txt2img_root
    elem = getattr(component, "elem_id", None)
    root = Context.root_block
    # Gradio also constructs unrendered copies while returning UI updates.
    # Only the original components created inside Blocks can be connected.
    if root is None or kwargs.get("render") is False:
        return
    if elem in {"setting_sd_model_checkpoint", "setting_sd_modules", "forge_ui_preset", "forge_ui_dtype"}:
        if events_bound:
            return
        headers[elem] = component
        if len(headers) == 4 and controls:
            bind_events()
        return
    if txt2img_root is None and elem and elem.startswith("txt2img_"):
        txt2img_root = root
    if root is not txt2img_root or txt2img_root is None or component.get_block_name() not in SCALAR_TYPES:
        return
    # ForgeCanvas transports image pixels through a Textbox subclass. It is
    # image material, and its postprocessor cannot accept saved text values.
    if component.__class__.__name__ == "LogicalImage":
        return
    if getattr(component, "interactive", None) is False:
        return
    parent = component
    while parent and parent is not root:
        parent_id = str(getattr(parent, "elem_id", None) or "")
        if "edit_user_metadata" in parent_id or parent_id.startswith("config_preset_"):
            return
        parent = getattr(parent, "parent", None)
    if elem and (elem.startswith(("config_preset_", "script_config_preset_", "txt2img_styles_edit_")) or elem in {"txt2img_preview_filename", "generation_info_txt2img"}):
        return
    label = str(getattr(component, "label", None) or stable_id(elem) or "設定")
    if stable_id(elem):
        key = "id:" + elem
        by_element[elem] = key
    else:
        ancestors = []
        parent = getattr(component, "parent", None)
        while parent and parent is not root:
            name = getattr(parent, "label", None) or stable_id(getattr(parent, "elem_id", None))
            if name:
                ancestors.append(str(name))
            parent = getattr(parent, "parent", None)
        base = "/".join(reversed(ancestors)) + "/" + component.get_block_name() + ":" + label
        occurrences[base] = occurrences.get(base, 0) + 1
        key = f"path:{base}#{occurrences[base]}"
    widgets[key] = component


def snapshot(values, session):
    checkpoint, module_names, preset, dtype, *current = values
    match = sd_models.get_closet_checkpoint_match(checkpoint or shared.opts.sd_model_checkpoint)
    entries = {key: {"label": str(getattr(widget, "label", None) or stable_id(widget.elem_id) or "設定"),
                     "type": widget.get_block_name(), "value": core.capture_selection(value, widget.choices or []) if getattr(widget, "type", None) == "index" else value}
               for (key, widget), value in zip(widgets.items(), current)}
    settings = {key: entries[by_element[elem]]["value"] for key, elem in BASIC.items() if elem in by_element}
    selected = module_names if module_names is not None else [name for name, path in main_entry.module_list.items()
        if str(path) in (shared.opts.forge_additional_modules or [])]
    settings.update(widgets=entries, modules=[{"name": name, "path": str(main_entry.module_list.get(name, ""))} for name in selected], preset=preset, dtype=dtype, options=generation_options())
    result = {"checkpoint": checkpoint or shared.opts.sd_model_checkpoint,
              "checkpoint_path": match.filename if match else "", "settings": settings}
    state.capture(result, session)
    return result


def capture_ui(*values, request: gr.Request):
    result = snapshot(values, request.session_hash)
    return f"接続中：txt2imgの{len(widgets)}項目とVAE／エンコーダー{len(result['settings']['modules'])}件を読み取れます。"


def validate_value(widget, value):
    kind = widget.get_block_name()
    if kind in ("dropdown", "radio", "checkboxgroup"):
        choices = [v[1] if isinstance(v, (tuple, list)) else v for v in (widget.choices or [])]
        values = value if getattr(widget, "multiselect", False) or kind == "checkboxgroup" else [value]
        if value is not None and choices and any(v not in choices for v in values):
            raise ValueError(f"{widget.label}の選択肢が現在のForgeにありません。")
    if kind in ("slider", "number"):
        low, high = getattr(widget, "minimum", None), getattr(widget, "maximum", None)
        outside = value != widget.value and ((low is not None and value is not None and value < low) or (high is not None and value is not None and value > high)) if isinstance(value, (int, float)) else False
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or outside):
            raise ValueError(f"{widget.label}の値が範囲外です。")
    if kind == "checkbox" and not isinstance(value, bool):
        raise ValueError(f"{widget.label}はチェック状態で指定してください。")


def apply_ui(*values, request: gr.Request):
    command = state.take(request.session_hash, busy())
    no_change = list(values[:4]) + [core.capture_selection(value, widget.choices or []) if getattr(widget, "type", None) == "index" else value for widget, value in zip(widgets.values(), values[4:])]
    if command is None:
        return no_change + [gr.skip()]
    try:
        current = snapshot(values, request.session_hash)["settings"]
        updates = {}
        checkpoint, module_output, preset_output, dtype_output = values[:4]
        if command["mode"] == "checkpoint":
            target = sd_models.checkpoints_list.get(command.get("checkpoint"))
            if target is None:
                raise ValueError("チェックポイントが見つかりません。")
            checkpoint_choice = target.short_title if shared.opts.sd_checkpoint_dropdown_use_short else target.name
            settings = command.get("settings", {})
            saved = settings.get("widgets", {})
            missing = set(saved) - set(widgets)
            if missing:
                raise ValueError(f"保存時の設定欄が{len(missing)}項目見つかりません。拡張機能の構成を確認してください。")
            for key, entry in saved.items():
                if entry.get("type") != widgets[key].get_block_name():
                    raise ValueError("保存時と設定欄の種類が異なります。")
                updates[key] = entry["value"]
            for key, elem in BASIC.items():
                if key in settings:
                    updates[by_element[elem]] = settings[key]
            for key, value in updates.items():
                validate_value(widgets[key], value)
            options = settings.get("options", {})
            available_options = generation_options()
            for key, value in options.items():
                if key not in available_options:
                    raise ValueError("保存した生成オプションが現在のForgeと一致しません：" + key)
                options[key] = core.compatible_option(value, available_options[key])
            modules = None
            if "modules" in settings:
                modules = []
                by_path = {str(Path(path).resolve()).casefold(): name for name, path in main_entry.module_list.items()}
                for module in settings["modules"]:
                    name = by_path.get(str(Path(module["path"]).resolve()).casefold())
                    if name is None:
                        raise ValueError("保存したVAE／エンコーダーが見つかりません：" + module.get("name", ""))
                    modules.append(name)
            preset = settings.get("preset") or values[2]
            dtype = settings.get("dtype") or values[3]
            validate_value(main_entry.ui_forge_preset, preset)
            validate_value(main_entry.ui_forge_unet_dtype, dtype)
            if preset != values[2]:
                # Let Forge's preset callback finish before restoring saved values.
                state.defer(command)
                return [values[0], values[1], preset, values[3]] + no_change[4:] + ["UI Presetを切り替えてから設定を復元します。"]
            if not queue_lock.acquire(blocking=False):
                raise ValueError("Forgeが処理中です。終了してから送り直してください。")
            try:
                if busy():
                    raise ValueError("Forgeは生成中のため変更しませんでした。")
                previous = copy.deepcopy(shared.opts.data)
                try:
                    for key, value in options.items():
                        shared.opts.set(key, value)
                    main_entry.checkpoint_change(checkpoint_choice, preset, save=False, refresh=False)
                    if modules is not None:
                        main_entry.modules_change(modules, preset, save=False, refresh=False)
                        module_output = modules
                    main_entry.dtype_change(dtype, preset, save=False, refresh=False)
                    shared.opts.set("forge_preset", preset)
                    main_entry.refresh_model_loading_parameters()
                    shared.opts.save(shared.config_filename)
                except Exception:
                    shared.opts.data.clear()
                    shared.opts.data.update(previous)
                    main_entry.refresh_model_loading_parameters()
                    raise
            finally:
                queue_lock.release()
            checkpoint = checkpoint_choice
            dtype_output = dtype
        else:
            import networks
            name = command.get("name", "")
            weight = command.get("weight", 0.8)
            if name not in networks.available_networks or any(c in name for c in "<>\r\n"):
                raise ValueError("LoRAが見つかりません。")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 3:
                raise ValueError("LoRA強度が不正です。")
            prompt = re.sub(r"<lora:" + re.escape(name) + r":[^>]+>", "", current["prompt"] or "").strip(" ,")
            tokens = {t.strip() for t in prompt.split(",")}
            additions = [f"<lora:{name}:{weight:g}>"] + [t for t in command.get("triggers", []) if t and t not in tokens]
            if not queue_lock.acquire(blocking=False):
                raise ValueError("Forgeは処理中のため変更しませんでした。")
            try:
                if busy():
                    raise ValueError("Forgeは生成中のため変更しませんでした。")
                updates[by_element["txt2img_prompt"]] = ", ".join(([prompt] if prompt else []) + additions)
            finally:
                queue_lock.release()
        message = "Library Deskの設定を反映しました。生成は開始していません。"
        state.finish(command, True, message)
        return [checkpoint, module_output, preset_output, dtype_output] + [updates.get(k, current["widgets"][k]["value"]) for k in widgets] + [message]
    except Exception as exc:
        state.finish(command, False, str(exc))
        return no_change + ["反映できませんでした：" + str(exc)]


def save_ui(*values, request: gr.Request):
    snapshot(values, request.session_hash)
    try:
        req = urllib.request.Request(library_url() + "/api/lora/forge/save-current", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.load(response)
        return result["message"]
    except Exception:
        return "保存できませんでした。Library Deskの起動・登録フォルダを確認してください。"


def ui_tab():
    if not all(elem in by_element for elem in BASIC.values()):
        print("Library Desk: txt2img components not found; bridge UI disabled")
        return []
    with gr.Blocks() as tab:
        gr.Markdown("### LoRA Library Desk連携\n連携ボタンはtxt2imgのGenerate下にあります。モデル別に設定とVAE／エンコーダーを保存・復元します。")
        with gr.Group(elem_id="library_desk_panel"):
            try:
                desk_url = library_url()
            except ValueError:
                desk_url = "http://127.0.0.1:8787"
            gr.Markdown(f"**Library Desk** · [開く]({desk_url}/lora)", elem_id="library_desk_heading")
            with gr.Column(min_width=0, elem_id="library_desk_actions"):
                receive = gr.Button("Library Deskの設定を受け取る", elem_id="library_desk_receive")
                save = gr.Button("現在の設定をLibrary Deskへ保存", elem_id="library_desk_save")
                capture = gr.Button("接続を更新", elem_id="library_desk_capture")
            status = gr.Markdown("Forge画面を読み込み中です。", elem_id="library_desk_status")
        controls.update(capture=capture, receive=receive, save=save, status=status)
    return [(tab, "Library Desk", "library_desk")]


def bind_events():
    global events_bound
    required = [headers[name] for name in ("setting_sd_model_checkpoint", "setting_sd_modules", "forge_ui_preset", "forge_ui_dtype")] + list(widgets.values())
    def bind(fn):
        def callback(*args):
            return fn(*args[:-1], request=args[-1])
        callback.__name__ = fn.__name__
        callback.__signature__ = inspect.Signature(
            [inspect.Parameter(f"value{i}", inspect.Parameter.POSITIONAL_OR_KEYWORD) for i in range(len(required))]
            + [inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=gr.Request)])
        callback.__annotations__ = {"request": gr.Request}
        return callback
    status = controls["status"]
    controls["capture"].click(bind(capture_ui), inputs=required, outputs=[status], queue=False, api_name="library_desk_capture", show_api=False, show_progress=False)
    controls["receive"].click(bind(apply_ui), inputs=required, outputs=required + [status], queue=False, api_name="library_desk_receive", show_api=False, show_progress=False)
    controls["save"].click(bind(save_ui), inputs=required, outputs=[status], queue=False, api_name="library_desk_save", show_api=False, show_progress=False)
    events_bound = True


def check_local(request):
    host = request.client.host if request.client else ""
    origin = request.headers.get("origin", "")
    allowed_ports = {request.url.port or 80}
    try:
        allowed_ports.add(urlparse(library_url()).port or 80)
    except ValueError:
        pass
    try:
        parsed_origin = urlparse(origin)
        valid_origin = (parsed_origin.scheme == "http" and parsed_origin.hostname in {"127.0.0.1", "localhost", "::1"}
                        and parsed_origin.username is None and parsed_origin.password is None
                        and (parsed_origin.port or 80) in allowed_ports)
    except ValueError:
        valid_origin = False
    if host not in {"127.0.0.1", "::1"} or (origin and not valid_origin):
        raise HTTPException(403, "この連携はPC内からのみ利用できます。")


def app_started(_demo, app):
    original_postprocess = _demo.postprocess_data
    async def postprocess(block_fn, predictions, session_state):
        try:
            return await original_postprocess(block_fn, predictions, session_state)
        except Exception as exc:
            if controls.get("status") in block_fn.outputs:
                Path(__file__).resolve().parents[1].joinpath("bridge-error.log").write_text(traceback.format_exc(), encoding="utf-8")
                with state.lock:
                    if state.last_result:
                        state.last_result.update(ok=False, message="Forge画面への反映に失敗しました：" + str(exc))
            raise
    _demo.postprocess_data = postprocess
    def get_status(request: Request):
        check_local(request)
        return state.status(busy())

    def catalog(request: Request):
        check_local(request)
        try:
            import networks
            loras = [{"name": n.name, "path": n.filename} for n in networks.available_networks.values()]
        except ImportError:
            loras = []
        return {"checkpoints": [{"title": c.title, "path": c.filename} for c in sd_models.checkpoints_list.values()],
                "loras": loras, "samplers": [s.name for s in sd_samplers.all_samplers],
                "schedulers": [s.label for s in sd_schedulers.schedulers],
                "modules": [{"name": name, "path": str(path)} for name, path in main_entry.module_list.items()],
                "widgets": [{"key": key, "id": widget._id, "elem_id": widget.elem_id,
                             "type": widget.get_block_name(), "label": widget.label}
                            for key, widget in widgets.items()]}

    async def command(request: Request):
        check_local(request)
        raw = await request.body()
        if len(raw) > 300000:
            raise HTTPException(400, "送信内容が大きすぎます。")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("送信内容が不正です。")
            return state.queue(payload, busy())
        except (ValueError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    def capture_request(request: Request):
        check_local(request)
        try:
            return state.request_capture()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    app.add_api_route("/library-desk/status", get_status, methods=["GET"])
    app.add_api_route("/library-desk/catalog", catalog, methods=["GET"])
    app.add_api_route("/library-desk/command", command, methods=["POST"])
    app.add_api_route("/library-desk/capture", capture_request, methods=["POST"])


script_callbacks.on_after_component(after_component)
script_callbacks.on_ui_settings(ui_settings)
script_callbacks.on_ui_tabs(ui_tab)
script_callbacks.on_app_started(app_started)

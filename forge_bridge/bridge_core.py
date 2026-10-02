import copy
import math
import threading
import time
import uuid


def capture_selection(value, choices):
    """Persist choice values instead of Gradio's transient choice indexes."""
    if value is None:
        return None
    if isinstance(value, list):
        return [capture_selection(v, choices) for v in value]
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(choices):
        raise ValueError("選択項目を読み取れませんでした。Forge画面を再読み込みしてください。")
    choice = choices[value]
    return choice[1] if isinstance(choice, (tuple, list)) else choice


def compatible_option(value, current):
    # JSON browsers serialize 0.0 as 0; preserve the Forge numeric type.
    if not isinstance(value, bool) and not isinstance(current, bool) and isinstance(value, (int, float)) and isinstance(current, (int, float)):
        if not math.isfinite(value) or (isinstance(current, int) and int(value) != value):
            raise ValueError("生成オプションの数値が不正です。")
        return float(value) if isinstance(current, float) else int(value)
    if type(value) is not type(current):
        raise ValueError("生成オプションの型が一致しません。")
    return copy.deepcopy(value)


class BridgeState:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.snapshot = None
        self.pending = None
        self.last_result = None
        self.capture_request = None

    def capture(self, snapshot, session):
        with self.lock:
            owner = self.pending or self.capture_request
            if owner and owner["session"] != session:
                return
            self.snapshot = dict(snapshot, session=session, captured_at=self.clock())
            self.capture_request = None

    def request_capture(self):
        with self.lock:
            if not self.snapshot or self.clock() - self.snapshot["captured_at"] >= 15:
                raise ValueError("Forge画面の接続を待っています。")
            self.capture_request = {"session": self.snapshot["session"], "requested_at": self.clock()}
            return {"ok": True, "requested_at": self.capture_request["requested_at"]}

    def status(self, busy=False):
        with self.lock:
            now = self.clock()
            if self.pending and now - self.pending["queued_at"] > 30:
                self.last_result = {"id": self.pending["id"], "ok": False, "message": "送信期限が切れました。Forge画面を開いて送り直してください。"}
                self.pending = None
            if self.capture_request and now - self.capture_request["requested_at"] > 30:
                self.capture_request = None
            snapshot = copy.deepcopy(self.snapshot)
            return {"bridge_version": "1.0", "busy": busy,
                    "connected": bool(snapshot and now - snapshot["captured_at"] < 15),
                    "snapshot": snapshot, "pending": bool(self.pending), "capture_requested": bool(self.capture_request), "last_result": copy.deepcopy(self.last_result)}

    def queue(self, payload, busy=False):
        with self.lock:
            if busy:
                raise ValueError("Forgeは生成中です。生成が終わってから送信してください。")
            if not self.snapshot or self.clock() - self.snapshot["captured_at"] >= 15:
                raise ValueError("Forgeの画面を開いて再読み込みしてください。")
            if self.pending:
                raise ValueError("前の送信を反映中です。少し待ってください。")
            if payload.get("mode") not in ("checkpoint", "lora"):
                raise ValueError("送信の種類が不正です。")
            self.pending = dict(copy.deepcopy(payload), id=str(uuid.uuid4()), queued_at=self.clock(), session=self.snapshot["session"])
            return {"ok": True, "id": self.pending["id"], "message": "Forge画面へ送信しました。"}

    def take(self, session, busy=False):
        with self.lock:
            command = self.pending
            if not command or command["session"] != session:
                return None
            self.pending = None
            if self.clock() - command["queued_at"] > 30 or busy:
                self.last_result = {"id": command["id"], "ok": False, "message": "生成中または期限切れのため変更しませんでした。"}
                return None
            return command

    def finish(self, command, ok, message):
        with self.lock:
            self.last_result = {"id": command["id"], "ok": ok, "message": message}

    def defer(self, command):
        with self.lock:
            if self.pending:
                raise ValueError("別の送信を処理中です。")
            self.pending = command

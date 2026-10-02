import importlib.util
import json
import struct
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

app, root = map(Path, sys.argv[1:3])
spec = importlib.util.spec_from_file_location("checkpoint_fixture", app / "server.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
s.DATA_ROOT = root / "data"
s.DATA_ROOT.mkdir(parents=True, exist_ok=True)
s.LORA_DB_PATH = s.DATA_ROOT / "test.sqlite3"
s.LORA_CONFIG_PATH = s.DATA_ROOT / "config.json"
s.LEGACY_LORA_STORE_PATH = s.DATA_ROOT / "legacy.json"
s.init_lora_db()
folders = [root / "Checkpoints", root / "Lora", root / "workflows"]
for folder in folders:
    folder.mkdir(exist_ok=True)
for folder, name in [(folders[0], "Checkpoint A"), (folders[0], "Checkpoint B"), (folders[1], "LoRA")]:
    p = folder / (name + ".safetensors")
    if not p.exists():
        header = json.dumps({"__metadata__": {"modelspec.title": name}}).encode()
        p.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 16)
        s.merge_metadata_changes([str(p.resolve())], s.sanitize_metadata_input({
            "author": "既存の作者", "base_model": "Anima", "strength_min": 0.5, "strength_max": 1.0,
            "notes": "既存のメモ", "triggers": ["original trigger"], "favorite": True, "rating": 4,
        }))
wf = folders[2] / "Workflow.json"
if not wf.exists():
    wf.write_text(json.dumps({"nodes": [{"id": 1, "type": "KSampler"}], "links": []}))
s.save_lora_config({"watch_dirs": [str(p) for p in folders]})
server = ThreadingHTTPServer(("127.0.0.1", 0), s.build_handler(app / "web"))
print(json.dumps({"port": server.server_address[1]}), flush=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
sys.stdin.readline()
server.shutdown()
server.server_close()

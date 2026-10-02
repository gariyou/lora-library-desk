import importlib.util
import json
from pathlib import Path
import struct
import sys
import threading
from http.server import ThreadingHTTPServer

app, root = map(Path, sys.argv[1:3])
spec = importlib.util.spec_from_file_location("rating_server", app / "server.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
s.DATA_ROOT = root / "data"
s.DATA_ROOT.mkdir(parents=True, exist_ok=True)
s.LORA_DB_PATH = s.DATA_ROOT / "test.sqlite3"
s.LORA_CONFIG_PATH = s.DATA_ROOT / "config.json"
s.LEGACY_LORA_STORE_PATH = s.DATA_ROOT / "legacy.json"
models = root / "Lora"
models.mkdir(exist_ok=True)
for name in ["Alpha", "Beta", "Delta", "Epsilon", "Gamma", "Zeta"]:
    p = models / (name + ".safetensors")
    if not p.exists():
        header = json.dumps({"__metadata__": {"modelspec.title": name}}).encode()
        p.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 16)
workflows = root / "workflows"
workflows.mkdir(exist_ok=True)
wf = workflows / "Workflow.json"
if not wf.exists():
    wf.write_text(json.dumps({"nodes": [{"id": 1, "type": "KSampler"}], "links": []}))
s.init_lora_db()
s.save_lora_config({"watch_dirs": [str(models), str(workflows)]})
server = ThreadingHTTPServer(("127.0.0.1", 0), s.build_handler(app / "web"))
print(json.dumps({"port": server.server_address[1], "root": str(root)}), flush=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
sys.stdin.readline()
server.shutdown()
server.server_close()


import importlib.util
import json
import pathlib
import struct
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

app = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('selection_server', app / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
with tempfile.TemporaryDirectory(prefix='lora-selection-e2e-') as td:
    root = pathlib.Path(td)
    s.DATA_ROOT = root / 'data'
    s.DATA_ROOT.mkdir()
    s.LORA_DB_PATH = s.DATA_ROOT / 'test.sqlite3'
    s.LORA_CONFIG_PATH = s.DATA_ROOT / 'config.json'
    s.LEGACY_LORA_STORE_PATH = s.DATA_ROOT / 'legacy.json'
    models = root / 'Lora'
    models.mkdir()
    for name in ['Alpha', 'Beta', 'Delta', 'Epsilon', 'Gamma', 'Zeta']:
        header = json.dumps({'__metadata__': {'modelspec.title': name}}).encode()
        (models / (name + '.safetensors')).write_bytes(struct.pack('<Q', len(header)) + header + b'\0' * 16)
    workflows = root / 'workflows'
    workflows.mkdir()
    (workflows / 'Workflow.json').write_text(json.dumps({'nodes': [{'id': 1, 'type': 'KSampler'}], 'links': []}))
    s.init_lora_db()
    s.save_lora_config({'watch_dirs': [str(models), str(workflows)]})
    http = ThreadingHTTPServer(('127.0.0.1', 0), s.build_handler(app / 'web'))
    print(json.dumps({'port': http.server_address[1], 'root': str(root)}), flush=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    sys.stdin.readline()
    http.shutdown()
    http.server_close()

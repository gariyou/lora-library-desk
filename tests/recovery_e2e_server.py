import hashlib
import os
import importlib.util
import json
import pathlib
import struct
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

app = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery_e2e', app / 'server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
with tempfile.TemporaryDirectory(prefix='bridge-recovery-e2e-') as td:
    root = pathlib.Path(td)
    s.DATA_ROOT = root / 'data'
    s.DATA_ROOT.mkdir()
    s.LORA_DB_PATH = s.DATA_ROOT / 'test.sqlite3'
    s.LORA_CONFIG_PATH = s.DATA_ROOT / 'config.json'
    s.LEGACY_LORA_STORE_PATH = s.DATA_ROOT / 'legacy.json'
    downloads = root / 'Downloads'
    downloads.mkdir()
    models = root / 'Models' / 'Lora'
    checkpoints = root / 'Models' / 'StableDiffusion'
    s.get_default_downloads_dir = lambda: downloads
    s.PENDING_IMPORT_SETTLE_SECONDS = 1.5
    header = json.dumps({'__metadata__': {'ss_network_module': 'networks.lora', 'modelspec.title': 'Direct folder fixture'}}).encode()
    direct = downloads / 'direct.safetensors'
    direct.write_bytes(struct.pack('<Q', len(header)) + header + b'\0' * 16)
    (downloads / 'ordinary.json').write_text('{"setting": true}')
    history_bytes = b''
    if os.environ.get('E2E_HISTORY_IDENTITY') == '1':
        raw = json.dumps({'anima.transformer.weight': {'dtype': 'F32', 'shape': [4], 'data_offsets': [0, 16]}}).encode()
        history_bytes = struct.pack('<Q', len(raw)) + raw + b'\0' * 16
        (root/'history-fixture.bin').write_bytes(history_bytes)
        version = {'id': 3292162, 'modelId': 2560956, 'name': 'History Version', 'model': {'name': 'Fixture Suite', 'type': 'Checkpoint'},
                   'baseModel': 'Anima', 'trainedWords': [], 'files': [{'name': 'published-other-name.safetensors',
                   'sizeKB': len(history_bytes)/1024, 'hashes': {'SHA256': hashlib.sha256(history_bytes).hexdigest().upper()}}]}
        def fixture_fetch(url, *args):
            if url != 'https://civitai.red/api/v1/model-versions/3292162':
                raise OSError('Unexpected external request in isolated test')
            return version
        s.fetch_json_url = fixture_fetch
        s.resolve_preview_media_url = lambda payload: ''
    s.init_lora_db()
    s.save_lora_config({'watch_dirs': [str(models), str(checkpoints)]})
    http = ThreadingHTTPServer(('127.0.0.1', 0), s.build_handler(app / 'web'))
    print(json.dumps({'port': http.server_address[1], 'root': str(root), 'models': str(models), 'direct': str(direct), 'checkpoints': str(checkpoints)}), flush=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    sys.stdin.readline()
    http.shutdown()
    http.server_close()

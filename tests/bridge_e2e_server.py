import importlib.util, tempfile, pathlib, threading, sys
from http.server import ThreadingHTTPServer
p=pathlib.Path(__file__).resolve().parents[1]/'server.py'
spec=importlib.util.spec_from_file_location('bridge_e2e_server',p);s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
with tempfile.TemporaryDirectory(prefix='lora-bridge-e2e-') as td:
 root=pathlib.Path(td);s.DATA_ROOT=root/'data';s.DATA_ROOT.mkdir();s.LORA_DB_PATH=s.DATA_ROOT/'db.sqlite3';s.LORA_CONFIG_PATH=s.DATA_ROOT/'config.json';s.LEGACY_LORA_STORE_PATH=s.DATA_ROOT/'legacy.json'
 s.init_lora_db();s.save_lora_config({'watch_dirs':[str(root/'Lora'),str(root/'StableDiffusion'),str(root/'ComfyUI'/'user'/'default'/'workflows')]})
 server=ThreadingHTTPServer(('127.0.0.1',0),s.build_handler(p.parent/'web'))
 print(server.server_address[1],flush=True)
 threading.Thread(target=server.serve_forever,daemon=True).start()
 sys.stdin.readline()
 server.shutdown()
 server.server_close()

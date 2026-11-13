import json
import os
import socket
import threading
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

import numpy as np
from PIL import Image
from flask import Flask, send_from_directory, jsonify, request, Response
from werkzeug.serving import make_server

try:
    from .storage_audit import storage_bp
except ImportError:
    from storage_audit import storage_bp

# ---------- Paths ----------

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_SITE_DIR = os.path.join(_MODULE_DIR, "site")
_CONFIG_PATH = os.path.join(_MODULE_DIR, "config.json")
_WORKFLOWS_DIR = os.path.join(_MODULE_DIR, "workflows")

_DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8189,
    "keep_history": 50,
    "comfy_url": "",
    "comfy_input_dir": "",
}

# ---------- Config ----------

def load_config():
    cfg = dict(_DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in cfg:
                if k in data:
                    cfg[k] = data[k]
    except Exception:
        pass
    cfg["host"] = str(cfg.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        cfg["port"] = int(cfg["port"])
    except Exception:
        cfg["port"] = 8189
    if not (1 <= cfg["port"] <= 65535):
        cfg["port"] = 8189
    try:
        cfg["keep_history"] = int(cfg["keep_history"])
    except Exception:
        cfg["keep_history"] = 50
    cfg["keep_history"] = max(1, min(1000, cfg["keep_history"]))
    cfg["comfy_url"] = str(cfg.get("comfy_url") or "").strip()
    cfg["comfy_input_dir"] = str(cfg.get("comfy_input_dir") or "").strip()
    return cfg

def save_config(patch):
    cfg = load_config()
    if isinstance(patch, dict):
        h = patch.get("host")
        if isinstance(h, str) and h.strip():
            cfg["host"] = h.strip()
        for key in ("port", "keep_history"):
            try:
                v = int(patch.get(key))
            except Exception:
                continue
            if key == "port" and 1 <= v <= 65535:
                cfg[key] = v
            elif key == "keep_history":
                cfg[key] = max(1, min(1000, v))
        for key in ("comfy_url", "comfy_input_dir"):
            v = patch.get(key)
            if isinstance(v, str):
                cfg[key] = v.strip()
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[FlaskImageViewer] config save failed: {e}", flush=True)
    return cfg

# ---------- Global state ----------

_SERVER_LOCK = threading.Lock()
_SERVER = None
_ACTUAL_HOST = None
_ACTUAL_PORT = None
_IMAGE_DIR = None
_IMAGE_REGISTRY = []
_REGISTRY_LOCK = threading.Lock()
_PROMPT_BY_ID = {}
_COMPLETED_PROMPTS = set()

def _ensure_dirs():
    global _IMAGE_DIR
    if _IMAGE_DIR is None:
        _IMAGE_DIR = os.path.join(tempfile.gettempdir(), "comfyui_flask_viewer")
        os.makedirs(_IMAGE_DIR, exist_ok=True)
    return _IMAGE_DIR

def _ensure_workflows():
    os.makedirs(_WORKFLOWS_DIR, exist_ok=True)

# ---------- ComfyUI helpers ----------

def _comfy_base_url():
    cfg = load_config()
    if cfg["comfy_url"]:
        return cfg["comfy_url"].rstrip("/")
    try:
        from server import PromptServer
        port = getattr(PromptServer.instance, "port", 8188) or 8188
        return f"http://127.0.0.1:{port}"
    except Exception:
        return "http://127.0.0.1:8188"

def _comfy_input_dir():
    cfg = load_config()
    if cfg["comfy_input_dir"]:
        p = cfg["comfy_input_dir"]
        os.makedirs(p, exist_ok=True)
        return p
    try:
        from folder_paths import get_input_directory
        p = get_input_directory()
        os.makedirs(p, exist_ok=True)
        return p
    except Exception:
        p = os.path.join(_MODULE_DIR, "_uploads")
        os.makedirs(p, exist_ok=True)
        return p

def _http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _http_post_json(url, payload, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _http_get_bytes(url, timeout=30):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

# ---------- Workflow injection ----------

def _set_node_text(node, text):
    if not isinstance(node, dict) or text is None:
        return False
    inputs = node.setdefault("inputs", {})
    for key in ("text", "text_g", "t5xxl", "clip_l", "prompt", "positive", "negative"):
        if key in inputs and isinstance(inputs[key], str):
            inputs[key] = text
            return True
    return False

def _inject_workflow(graph, prompt, negative_prompt, width, height,
                     seed, steps, cfg, image_filenames):
    pos_ids, neg_ids = [], []
    sampler_types = {
        "KSampler", "KSamplerAdvanced", "SamplerCustom",
        "SamplerCustomAdvanced", "KSampler (Efficient)",
        "KSampler SDXL (Eff.)",
    }

    def _get(nid):
        nid = str(nid)
        if nid in graph:
            return graph[nid]
        for k in graph:
            if str(k) == nid:
                return graph[k]
        return None

    for node in graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") not in sampler_types:
            continue
        inp = node.setdefault("inputs", {})

        for slot, bucket in (("positive", pos_ids), ("negative", neg_ids)):
            link = inp.get(slot)
            if isinstance(link, (list, tuple)) and link:
                nid = str(link[0])
                if nid not in bucket:
                    bucket.append(nid)

        if seed is not None:
            if "noise_seed" in inp:
                inp["noise_seed"] = int(seed)
            elif "seed" in inp:
                inp["seed"] = int(seed)
        if steps:
            try: inp["steps"] = int(steps)
            except (TypeError, ValueError): pass
        if cfg:
            try: inp["cfg"] = float(cfg)
            except (TypeError, ValueError): pass

    if prompt is not None:
        for nid in pos_ids:
            _set_node_text(_get(nid), prompt)
    if negative_prompt is not None:
        for nid in neg_ids:
            _set_node_text(_get(nid), negative_prompt)

    if not pos_ids or not neg_ids:
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            if "TextEncode" not in node.get("class_type", ""):
                continue
            title = (node.get("_meta", {}).get("title") or "").upper()
            if "NEGATIVE" in title and negative_prompt is not None:
                _set_node_text(node, negative_prompt)
            elif ("POSITIVE" in title or "PROMPT" in title) and prompt is not None:
                _set_node_text(node, prompt)

    remaining_images = list(image_filenames)
    latent_types = {"EmptyLatentImage", "EmptySD3LatentImage", "EmptyFluxLatentImage"}
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        inp = node.setdefault("inputs", {})
        if ct in latent_types:
            if width:
                try: inp["width"] = int(width)
                except (TypeError, ValueError): pass
            if height:
                try: inp["height"] = int(height)
                except (TypeError, ValueError): pass
        elif ct == "LoadImage" and remaining_images:
            inp["image"] = remaining_images.pop(0)

    return graph

def _workflow_has_unfilled_load_image(graph):
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            if not node.get("inputs", {}).get("image"):
                return True
    return False

# ---------- Network helpers ----------

def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None

def _display_url(host, port):
    if host in ("0.0.0.0", ""):
        ip = _lan_ip() or "127.0.0.1"
        return f"http://{ip}:{port}/"
    return f"http://{host}:{port}/"

# ---------- Flask app ----------

def _create_app():
    app = Flask(__name__, static_folder=_SITE_DIR, static_url_path="/static")
    app.register_blueprint(storage_bp)

    @app.route("/")
    def index():
        return send_from_directory(_SITE_DIR, "index.html")

    @app.route("/ping")
    def ping():
        return jsonify({"status": "ok", "images": len(_IMAGE_REGISTRY)})

    @app.route("/api/images")
    @app.route("/images")
    def images():
        with _REGISTRY_LOCK:
            return jsonify(list(_IMAGE_REGISTRY))

    @app.route("/image/<path:filename>")
    def image(filename):
        return send_from_directory(_ensure_dirs(), filename)

    @app.route("/latest")
    def latest():
        with _REGISTRY_LOCK:
            if not _IMAGE_REGISTRY:
                return ("No images", 404)
            item = _IMAGE_REGISTRY[-1]
        return send_from_directory(_ensure_dirs(), item["filename"])

    @app.route("/api/config", methods=["GET", "POST"])
    def api_config():
        if request.method == "POST":
            try:
                data = request.get_json(force=True, silent=True) or {}
            except Exception:
                data = {}
            return jsonify(save_config(data))
        return jsonify(load_config())

    @app.route("/api/status")
    def api_status():
        return jsonify(server_status())

    @app.route("/api/restart", methods=["POST"])
    def api_restart():
        cfg = load_config()

        def _do():
            time.sleep(0.4)
            stop_server()
            time.sleep(0.5)
            start_server(cfg["host"], cfg["port"])

        threading.Thread(target=_do, daemon=True).start()
        return jsonify({"ok": True, "host": cfg["host"], "port": cfg["port"]})

    @app.route("/api/workflows", methods=["GET"])
    def api_workflows():
        _ensure_workflows()
        names = []
        try:
            for n in sorted(os.listdir(_WORKFLOWS_DIR)):
                if n.endswith(".json") and os.path.isfile(os.path.join(_WORKFLOWS_DIR, n)):
                    names.append(n)
        except Exception:
            pass
        return jsonify(names)

    @app.route("/api/generate", methods=["POST"])
    def api_generate():
        wf_name = (request.form.get("workflow") or "default.json").strip()
        if "/" in wf_name or "\\" in wf_name or ".." in wf_name:
            return jsonify({"error": "invalid workflow name"}), 400
        if not wf_name.endswith(".json"):
            wf_name += ".json"
        wf_path = os.path.join(_WORKFLOWS_DIR, wf_name)
        if not os.path.isfile(wf_path):
            return jsonify({"error": f"workflow not found: {wf_name}"}), 400
        try:
            with open(wf_path, "r", encoding="utf-8") as f:
                graph = json.load(f)
        except Exception as e:
            return jsonify({"error": f"failed to load workflow: {e}"}), 500

        prompt_text = request.form.get("prompt", "")
        neg_text = request.form.get("negative_prompt", "")
        width = request.form.get("width")
        height = request.form.get("height")
        steps = request.form.get("steps")
        cfg = request.form.get("cfg")
        try:
            seed = int(request.form.get("seed") or 0)
        except ValueError:
            seed = 0
        if seed <= 0:
            seed = int(time.time_ns() % (2 ** 32))

        print(f"[FlaskImageViewer] generate: workflow={wf_name} prompt={prompt_text!r} neg={neg_text!r}", flush=True)

        input_dir = _comfy_input_dir()
        saved_images = []
        for f in request.files.getlist("images"):
            if not f or not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                ext = ".png"
            unique = f"{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}_in{ext}"
            f.save(os.path.join(input_dir, unique))
            saved_images.append(unique)

        graph = _inject_workflow(
            graph, prompt_text, neg_text, width, height,
            seed, steps, cfg, saved_images,
        )

        # DEBUG: show the encoder text nodes after injection
        try:
            for nid, n in graph.items():
                if isinstance(n, dict) and "TextEncode" in n.get("class_type", ""):
                    title = n.get("_meta", {}).get("title") or ""
                    for k in ("text", "text_g", "t5xxl", "clip_l"):
                        if k in n.get("inputs", {}):
                            print(
                                f"[FlaskImageViewer] node {nid} ({title}) {k} = "
                                f"{n['inputs'][k]!r}",
                                flush=True,
                            )
        except Exception:
            pass

        if _workflow_has_unfilled_load_image(graph):
            return jsonify({
                "error": "this workflow needs an input image — upload one or pick txt2img",
            }), 400

        try:
            resp = _http_post_json(
                f"{_comfy_base_url()}/prompt",
                {"prompt": graph, "client_id": str(uuid.uuid4())},
            )
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = str(e)
            print(f"[FlaskImageViewer] ComfyUI rejected prompt: {body}", flush=True)
            return jsonify({"error": f"ComfyUI rejected prompt: {body}"}), 500
        except Exception as e:
            return jsonify({"error": f"failed to reach ComfyUI: {e}"}), 500

        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            return jsonify({"error": "no prompt_id returned"}), 500
        _PROMPT_BY_ID[prompt_id] = prompt_text
        return jsonify({"prompt_id": prompt_id, "seed": seed})

    @app.route("/api/generate/status/<prompt_id>", methods=["GET"])
    def api_generate_status(prompt_id):
        if prompt_id in _COMPLETED_PROMPTS:
            with _REGISTRY_LOCK:
                items = [it for it in _IMAGE_REGISTRY if it.get("prompt_id") == prompt_id]
            return jsonify({
                "done": True,
                "images": [{"filename": it["filename"],
                            "url": f"/image/{it['filename']}"} for it in items],
            })
        try:
            hist = _http_get_json(f"{_comfy_base_url()}/history/{prompt_id}", timeout=15)
        except Exception as e:
            return jsonify({"done": False, "error": str(e)})

        if prompt_id not in hist:
            return jsonify({"done": False})

        entry = hist[prompt_id]
        status = entry.get("status") or {}
        status_str = status.get("status_str", "")
        outputs = entry.get("outputs") or {}

        if status_str and status_str != "success" and not outputs:
            _COMPLETED_PROMPTS.add(prompt_id)
            return jsonify({"done": True, "images": [], "error": status_str})

        new_items = []
        prompt_for_entry = _PROMPT_BY_ID.get(prompt_id, "")
        for node_out in outputs.values():
            for img in node_out.get("images", []):
                filename = img.get("filename")
                if not filename:
                    continue
                subfolder = img.get("subfolder", "")
                itype = img.get("type", "output")
                view_url = (
                    f"{_comfy_base_url()}/view?filename={urllib.parse.quote(filename)}"
                    f"&subfolder={urllib.parse.quote(subfolder)}&type={urllib.parse.quote(itype)}"
                )
                try:
                    data = _http_get_bytes(view_url, timeout=30)
                except Exception:
                    continue
                _ensure_dirs()
                unique = (
                    f"{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"
                    f"_web_{len(new_items)}.png"
                )
                with open(os.path.join(_ensure_dirs(), unique), "wb") as f:
                    f.write(data)
                item = {
                    "id": uuid.uuid4().hex,
                    "filename": unique,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "prompt": prompt_for_entry,
                    "source": "web",
                    "index": len(new_items),
                    "batch": 1,
                    "prompt_id": prompt_id,
                }
                with _REGISTRY_LOCK:
                    _IMAGE_REGISTRY.append(item)
                new_items.append(item)

        cfg_now = load_config()
        with _REGISTRY_LOCK:
            excess = len(_IMAGE_REGISTRY) - int(cfg_now["keep_history"])
            if excess > 0:
                for it in _IMAGE_REGISTRY[:excess]:
                    try:
                        os.remove(os.path.join(_ensure_dirs(), it["filename"]))
                    except OSError:
                        pass
                del _IMAGE_REGISTRY[:excess]

        _COMPLETED_PROMPTS.add(prompt_id)
        _PROMPT_BY_ID.pop(prompt_id, None)
        return jsonify({
            "done": True,
            "images": [{"filename": it["filename"],
                        "url": f"/image/{it['filename']}"} for it in new_items],
        })

    return app

# ---------- Server thread ----------

class ServerThread(threading.Thread):
    def __init__(self, server):
        super().__init__(daemon=True, name="ComfyUIFlaskViewer")
        self.server = server

    def run(self):
        try:
            self.server.serve_forever()
        except Exception as e:
            print(f"[FlaskImageViewer] serve_forever failed: {e}", flush=True)

    def shutdown(self):
        try:
            self.server.shutdown()
        except Exception as e:
            print(f"[FlaskImageViewer] shutdown error: {e}", flush=True)

# ---------- Server control ----------

def _bind_server(host, port, app):
    try:
        srv = make_server(host, port, app, threaded=True)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    try:
        actual = srv.server_address[1]
    except Exception:
        actual = getattr(srv, "server_port", None) or port
    return srv, actual

def start_server(host, port):
    global _SERVER, _ACTUAL_HOST, _ACTUAL_PORT
    with _SERVER_LOCK:
        if _SERVER is not None and _SERVER.is_alive():
            return _display_url(_ACTUAL_HOST, _ACTUAL_PORT), "running"
        _SERVER = None
        _ACTUAL_HOST = None
        _ACTUAL_PORT = None
        host = (host or "").strip() or "127.0.0.1"
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 8189
        if not (1 <= port <= 65535):
            port = 8189
        _ensure_workflows()
        app = _create_app()
        candidates = [port]
        for p in range(port + 1, min(port + 21, 65536)):
            candidates.append(p)
        candidates.append(0)
        last_err = None
        for p in candidates:
            srv, res = _bind_server(host, p, app)
            if srv is None:
                last_err = res
                continue
            thread = ServerThread(srv)
            thread.start()
            _SERVER = thread
            _ACTUAL_HOST = host
            _ACTUAL_PORT = res
            url = _display_url(host, res)
            print(f"[FlaskImageViewer] Serving on {url}", flush=True)
            return url, "running"
        return _display_url(host, port), f"error: no bindable port ({last_err})"

def stop_server():
    global _SERVER, _ACTUAL_HOST, _ACTUAL_PORT
    with _SERVER_LOCK:
        if _SERVER is None:
            return "not running"
        try:
            _SERVER.shutdown()
        except Exception as e:
            print(f"[FlaskImageViewer] shutdown error: {e}", flush=True)
        _SERVER = None
        _ACTUAL_HOST = None
        _ACTUAL_PORT = None
        print("[FlaskImageViewer] Server stopped.", flush=True)
        return "stopped"

def server_status():
    with _SERVER_LOCK:
        running = _SERVER is not None and _SERVER.is_alive()
        host = _ACTUAL_HOST
        port = _ACTUAL_PORT
    return {
        "running": running,
        "host": host,
        "port": port,
        "url": _display_url(host, port) if running else None,
        "images": len(_IMAGE_REGISTRY),
        "config": load_config(),
    }

# ---------- Save helper (node path) ----------

def _to_pil(tensor_slice):
    arr = tensor_slice.detach().cpu().numpy()
    if arr.dtype != np.uint8:
        arr = (arr.clip(0, 1) * 255.0).astype(np.uint8)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    elif arr.shape[-1] == 1:
        arr = np.repeat(arr, 3, axis=-1)
    elif arr.shape[-1] == 4:
        arr = arr[..., :3]
    return Image.fromarray(arr)

def save_one_input(image_tensor, source_label, prompt_text, keep_history):
    if image_tensor is None:
        return
    _ensure_dirs()
    if image_tensor.ndim == 3:
        image_tensor = image_tensor.unsqueeze(0)
    batch = int(image_tensor.shape[0])
    for i in range(batch):
        img = _to_pil(image_tensor[i])
        filename = f"{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}_{source_label}_b{i}.png"
        img.save(os.path.join(_ensure_dirs(), filename))
        item = {
            "id": uuid.uuid4().hex,
            "filename": filename,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "prompt": prompt_text or "",
            "source": source_label,
            "index": i,
            "batch": batch,
        }
        with _REGISTRY_LOCK:
            _IMAGE_REGISTRY.append(item)
    with _REGISTRY_LOCK:
        excess = len(_IMAGE_REGISTRY) - int(keep_history)
        if excess > 0:
            for it in _IMAGE_REGISTRY[:excess]:
                try:
                    os.remove(os.path.join(_ensure_dirs(), it["filename"]))
                except OSError:
                    pass
            del _IMAGE_REGISTRY[:excess]

# ---------- Nodes ----------

class FlaskImageViewer:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"image1": ("IMAGE",)},
            "optional": {
                "image2": ("IMAGE",),
                "image3": ("IMAGE",),
                "image4": ("IMAGE",),
                "image5": ("IMAGE",),
                "image6": ("IMAGE",),
                "prompt_text": ("STRING", {"default": "", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("url", "status")
    CATEGORY = "image/viewer"
    FUNCTION = "run"
    OUTPUT_NODE = True

    def run(self, image1, image2=None, image3=None, image4=None,
            image5=None, image6=None, prompt_text=""):
        cfg = load_config()
        url, status = start_server(cfg["host"], cfg["port"])
        if status == "running":
            for label, tensor in [("image1", image1), ("image2", image2),
                                  ("image3", image3), ("image4", image4),
                                  ("image5", image5), ("image6", image6)]:
                try:
                    save_one_input(tensor, label, prompt_text, cfg["keep_history"])
                except Exception as e:
                    status = f"error saving {label}: {type(e).__name__}: {e}"
                    print(f"[FlaskImageViewer] {status}", flush=True)
                    break
        ui_text = f"{status} — {url}"
        return {"ui": {"text": [ui_text]}, "result": (url, status)}


class FlaskViewerStop:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    CATEGORY = "image/viewer"
    FUNCTION = "run"
    OUTPUT_NODE = True

    def run(self):
        status = stop_server()
        return {"ui": {"text": [status]}, "result": (status,)}
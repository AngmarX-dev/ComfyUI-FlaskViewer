import threading
import time

from .flask_image_viewer import (
    FlaskImageViewer, FlaskViewerStop,
    load_config, save_config, server_status,
    start_server, stop_server,
)

NODE_CLASS_MAPPINGS = {
    "FlaskImageViewer": FlaskImageViewer,
    "FlaskViewerStop": FlaskViewerStop,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "FlaskImageViewer": "Flask Image Viewer",
    "FlaskViewerStop": "Flask Viewer Stop",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

# ---------- Control routes on ComfyUI's own server ----------

try:
    from aiohttp import web
    from server import PromptServer
    _routes = PromptServer.instance.routes

    @_routes.get("/flask_viewer/config")
    async def _fv_get_config(request):
        return web.json_response(load_config())

    @_routes.post("/flask_viewer/config")
    async def _fv_post_config(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        return web.json_response(save_config(data or {}))

    @_routes.get("/flask_viewer/status")
    async def _fv_get_status(request):
        return web.json_response(server_status())

    @_routes.post("/flask_viewer/start")
    async def _fv_start(request):
        cfg = load_config()
        url, status = start_server(cfg["host"], cfg["port"])
        resp = {"status": status, "url": url}
        resp.update(server_status())
        return web.json_response(resp)

    @_routes.post("/flask_viewer/stop")
    async def _fv_stop(request):
        msg = stop_server()
        resp = {"ok": True, "message": msg}
        resp.update(server_status())
        return web.json_response(resp)

    @_routes.post("/flask_viewer/restart")
    async def _fv_restart(request):
        cfg = load_config()

        def _do():
            time.sleep(0.3)
            stop_server()
            time.sleep(0.5)
            start_server(cfg["host"], cfg["port"])

        threading.Thread(target=_do, daemon=True).start()
        return web.json_response({"ok": True, "message": "restarting"})

except Exception as _e:
    print(f"[FlaskImageViewer] control routes skipped: {_e}", flush=True)
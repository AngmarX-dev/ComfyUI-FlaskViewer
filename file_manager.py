import os
import shutil
import string
import time
import uuid
from PIL import Image, UnidentifiedImageError
from flask import Blueprint, abort, jsonify, request, send_file
from werkzeug.utils import secure_filename

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 250


def _managed_roots(module_dir, image_dir):
    roots = {}
    try:
        import folder_paths
        roots["input"] = folder_paths.get_input_directory()
        roots["output"] = folder_paths.get_output_directory()
        roots["temp"] = folder_paths.get_temp_directory()
    except Exception:
        roots["input"] = os.path.join(module_dir, "_uploads")
        roots["output"] = os.path.join(module_dir, "_output")
        roots["temp"] = os.path.join(module_dir, "_temp")
    roots["viewer"] = image_dir()
    return {key: os.path.realpath(os.path.abspath(path)) for key, path in roots.items()
            if isinstance(path, str) and path.strip()}


def _safe_target(root_path, relative_path=""):
    if not isinstance(relative_path, str) or "\x00" in relative_path:
        abort(400, description="Invalid path")
    normalized = relative_path.replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part]
    if any(part in (".", "..") for part in parts):
        abort(400, description="Path traversal is not allowed")
    candidate = os.path.realpath(os.path.join(root_path, *parts))
    try:
        if os.path.commonpath([root_path, candidate]) != root_path:
            abort(403, description="Path is outside the selected library")
    except ValueError:
        abort(403, description="Invalid path")
    return candidate, "/".join(parts)


def _format_entry(name, full_path):
    try:
        stat = os.stat(full_path, follow_symlinks=False)
    except OSError:
        return None
    if os.path.islink(full_path):
        return None
    is_dir = os.path.isdir(full_path)
    return {"name": name, "path": name, "type": "directory" if is_dir else "file",
            "size": None if is_dir else stat.st_size, "modified": int(stat.st_mtime),
            "extension": "" if is_dir else os.path.splitext(name)[1].lower()}


def _system_volumes():
    volumes = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            mount = letter + ":\\"
            if not os.path.exists(mount):
                continue
            try:
                usage = shutil.disk_usage(mount)
                volumes.append({"name": mount, "total": usage.total, "used": usage.used, "free": usage.free})
            except OSError:
                pass
    else:
        try:
            usage = shutil.disk_usage(os.path.abspath(os.sep))
            volumes.append({"name": os.path.abspath(os.sep), "total": usage.total,
                            "used": usage.used, "free": usage.free})
        except OSError:
            pass
    return volumes


def create_file_manager_blueprint(module_dir, image_dir):
    bp = Blueprint("flask_viewer_library", __name__)

    @bp.get("/api/library/roots")
    def library_roots():
        roots = _managed_roots(module_dir, image_dir)
        labels = {"input": "ComfyUI Input", "output": "ComfyUI Output",
                  "temp": "ComfyUI Temp", "viewer": "FlaskViewer History"}
        return jsonify([{"id": key, "label": labels.get(key, key), "available": os.path.isdir(path)}
                        for key, path in roots.items()])

    @bp.get("/api/library/storage")
    def library_storage():
        return jsonify({"volumes": _system_volumes(), "sampled_at": int(time.time())})

    @bp.get("/api/library/browse")
    def library_browse():
        roots = _managed_roots(module_dir, image_dir)
        root_id = (request.args.get("root") or "output").strip()
        if root_id not in roots:
            abort(404, description="Unknown library location")
        root_path = roots[root_id]
        os.makedirs(root_path, exist_ok=True)
        target, normalized = _safe_target(root_path, request.args.get("path", ""))
        if not os.path.isdir(target):
            abort(404, description="Directory not found")
        query = (request.args.get("q") or "").strip().casefold()
        try:
            limit = max(1, min(MAX_PAGE_SIZE, int(request.args.get("limit", DEFAULT_PAGE_SIZE))))
            offset = max(0, int(request.args.get("offset", 0)))
        except (TypeError, ValueError):
            abort(400, description="Invalid pagination")
        entries = []
        try:
            with os.scandir(target) as iterator:
                for item in iterator:
                    if item.is_symlink() or (query and query not in item.name.casefold()):
                        continue
                    entry = _format_entry(item.name, item.path)
                    if entry is not None:
                        entries.append(entry)
        except PermissionError:
            abort(403, description="Access denied")
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].casefold()))
        total = len(entries)
        page = entries[offset:offset + limit]
        for entry in page:
            entry["path"] = "/".join(filter(None, [normalized, entry["name"]]))
        return jsonify({"root": root_id, "path": normalized,
                        "parent": "/".join(normalized.split("/")[:-1]) if normalized else None,
                        "entries": page, "total": total, "limit": limit, "offset": offset})

    def resolve_file():
        roots = _managed_roots(module_dir, image_dir)
        root_id = (request.args.get("root") or "").strip()
        if root_id not in roots:
            abort(404, description="Unknown library location")
        target, _ = _safe_target(roots[root_id], request.args.get("path", ""))
        if not os.path.isfile(target) or os.path.islink(target):
            abort(404, description="File not found")
        return target

    @bp.get("/api/library/preview")
    def library_preview():
        target = resolve_file()
        if os.path.splitext(target)[1].lower() not in IMAGE_EXTENSIONS:
            abort(415, description="Preview is available for image files only")
        return send_file(target, conditional=True)

    @bp.get("/api/library/download")
    def library_download():
        target = resolve_file()
        return send_file(target, as_attachment=True, download_name=os.path.basename(target), conditional=True)

    @bp.post("/api/library/upload")
    def library_upload():
        target_dir = _managed_roots(module_dir, image_dir).get("input")
        if not target_dir:
            abort(503, description="ComfyUI input directory is unavailable")
        os.makedirs(target_dir, exist_ok=True)
        uploads = request.files.getlist("files")
        if not uploads:
            abort(400, description="No files were uploaded")
        saved = []
        for upload in uploads:
            if not upload or not upload.filename:
                continue
            filename = secure_filename(upload.filename)
            extension = os.path.splitext(filename)[1].lower()
            if not filename or extension not in UPLOAD_EXTENSIONS:
                abort(415, description="Only supported image files can be uploaded")
            upload.stream.seek(0, os.SEEK_END)
            size = upload.stream.tell()
            upload.stream.seek(0)
            if size > MAX_UPLOAD_BYTES:
                abort(413, description="The upload request is limited to 64 MB")
            try:
                with Image.open(upload.stream) as image:
                    image.verify()
            except (UnidentifiedImageError, OSError, ValueError):
                abort(415, description="The uploaded file is not a valid image")
            upload.stream.seek(0)
            base, ext = os.path.splitext(filename)
            while True:
                destination = os.path.join(target_dir, filename)
                try:
                    output = open(destination, "xb")
                    break
                except FileExistsError:
                    filename = f"{base}_{uuid.uuid4().hex[:8]}{ext}"
            with output:
                shutil.copyfileobj(upload.stream, output)
            saved.append({"name": filename, "size": os.path.getsize(destination)})
        return jsonify({"ok": True, "saved": saved}), 201

    return bp

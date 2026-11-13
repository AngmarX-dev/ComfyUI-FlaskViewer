import ctypes
import heapq
import os
import shutil
import stat
import string
import threading
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
_SCAN_LOCK = threading.Lock()
_SCAN_JOBS = {}
_SCAN_ACTIVE = None


def _scan_roots():
    if os.name != "nt":
        return [os.path.abspath(os.sep)]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetLogicalDrives.restype = ctypes.c_uint32
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    mask = kernel32.GetLogicalDrives()
    roots = []
    for index, letter in enumerate(string.ascii_uppercase):
        if not (mask & (1 << index)):
            continue
        drive = letter + ":\\"
        if kernel32.GetDriveTypeW(drive) == 3:
            roots.append(drive)
    return roots


def _is_reparse_point(entry_stat):
    attributes = getattr(entry_stat, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & flag)


def _scan_worker(job_id, roots, cancel_event):
    result = {"volumes": [], "totals": {"files": 0, "folders": 0, "bytes": 0},
              "largest_files": [], "top_folders": [], "extensions": [],
              "errors": [], "error_count": 0}
    largest = []
    folder_totals = {}
    extension_totals = {}
    error_samples = []

    def record_error(path, exc):
        result["error_count"] += 1
        if len(error_samples) < 500:
            error_samples.append({"path": path, "error": type(exc).__name__})

    for root in roots:
        if cancel_event.is_set():
            break
        volume = {"root": root, "files": 0, "folders": 0, "bytes": 0,
                  "status": "scanning"}
        try:
            usage = shutil.disk_usage(root)
            volume.update({"capacity": usage.total, "used": usage.used, "free": usage.free})
        except OSError as exc:
            record_error(root, exc)
        stack = [(root, "[root files]")]
        scanned_dirs = 0
        while stack and not cancel_event.is_set():
            current, top_name = stack.pop()
            scanned_dirs += 1
            try:
                with os.scandir(current) as iterator:
                    for entry in iterator:
                        if cancel_event.is_set():
                            break
                        try:
                            st = entry.stat(follow_symlinks=False)
                            if entry.is_symlink() or _is_reparse_point(st):
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                bucket = entry.name if current == root else top_name
                                volume["folders"] += 1
                                result["totals"]["folders"] += 1
                                folder_totals[(root, bucket)] = folder_totals.get((root, bucket), 0)
                                stack.append((entry.path, bucket))
                            elif entry.is_file(follow_symlinks=False):
                                size = int(st.st_size)
                                volume["files"] += 1
                                volume["bytes"] += size
                                result["totals"]["files"] += 1
                                result["totals"]["bytes"] += size
                                bucket_key = (root, top_name)
                                folder_totals[bucket_key] = folder_totals.get(bucket_key, 0) + size
                                ext = os.path.splitext(entry.name)[1].lower() or "[no extension]"
                                if len(ext) > 32:
                                    ext = "[long extension]"
                                if ext not in extension_totals and len(extension_totals) >= 256:
                                    ext = "[other]"
                                item = extension_totals.setdefault(ext, {"extension": ext, "files": 0, "bytes": 0})
                                item["files"] += 1
                                item["bytes"] += size
                                candidate = (size, entry.path)
                                if len(largest) < 50:
                                    heapq.heappush(largest, candidate)
                                elif candidate > largest[0]:
                                    heapq.heapreplace(largest, candidate)
                        except OSError as exc:
                            record_error(entry.path, exc)
            except OSError as exc:
                record_error(current, exc)
            if scanned_dirs % 32 == 0:
                with _SCAN_LOCK:
                    job = _SCAN_JOBS.get(job_id)
                    if job:
                        job["current_path"] = current
                        job["files"] = result["totals"]["files"]
                        job["folders"] = result["totals"]["folders"]
                        job["bytes"] = result["totals"]["bytes"]
                        job["error_count"] = result["error_count"]
        volume["status"] = "cancelled" if cancel_event.is_set() else "complete"
        result["volumes"].append(volume)

    result["largest_files"] = [
        {"path": path, "size": size} for size, path in sorted(largest, reverse=True)
    ]
    result["top_folders"] = [
        {"root": root, "folder": folder, "bytes": size}
        for (root, folder), size in sorted(folder_totals.items(), key=lambda item: item[1], reverse=True)[:50]
    ]
    result["extensions"] = sorted(extension_totals.values(), key=lambda item: item["bytes"], reverse=True)[:40]
    result["errors"] = error_samples
    with _SCAN_LOCK:
        job = _SCAN_JOBS.get(job_id)
        if job:
            job.update({"status": "cancelled" if cancel_event.is_set() else "completed",
                        "current_path": None, "files": result["totals"]["files"],
                        "folders": result["totals"]["folders"], "bytes": result["totals"]["bytes"],
                        "error_count": result["error_count"], "result": result,
                        "finished_at": int(time.time())})


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

    @bp.post("/api/library/scan")
    def library_scan_start():
        global _SCAN_ACTIVE
        roots = _scan_roots()
        if not roots:
            abort(503, description="No supported local volumes were detected")
        with _SCAN_LOCK:
            if _SCAN_ACTIVE and _SCAN_JOBS.get(_SCAN_ACTIVE, {}).get("status") == "running":
                abort(409, description="A storage scan is already running")
            for old_id in list(_SCAN_JOBS):
                if len(_SCAN_JOBS) >= 5 and _SCAN_JOBS[old_id].get("status") != "running":
                    _SCAN_JOBS.pop(old_id, None)
            job_id = uuid.uuid4().hex
            cancel_event = threading.Event()
            _SCAN_JOBS[job_id] = {"id": job_id, "status": "running", "roots": roots,
                                  "started_at": int(time.time()), "finished_at": None,
                                  "current_path": None, "files": 0, "folders": 0,
                                  "bytes": 0, "error_count": 0, "result": None,
                                  "cancel_event": cancel_event}
            _SCAN_ACTIVE = job_id
        worker = threading.Thread(target=_scan_worker, args=(job_id, roots, cancel_event),
                                  daemon=True, name="FlaskViewerStorageScan")
        worker.start()
        return jsonify({"job_id": job_id, "status": "running", "roots": roots}), 202

    @bp.get("/api/library/scan/<job_id>")
    def library_scan_status(job_id):
        with _SCAN_LOCK:
            job = _SCAN_JOBS.get(job_id)
            if not job:
                abort(404, description="Scan job not found")
            return jsonify({key: value for key, value in job.items() if key != "cancel_event"})

    @bp.post("/api/library/scan/<job_id>/cancel")
    def library_scan_cancel(job_id):
        with _SCAN_LOCK:
            job = _SCAN_JOBS.get(job_id)
            if not job:
                abort(404, description="Scan job not found")
            job["cancel_event"].set()
        return jsonify({"ok": True, "status": "cancelling"})

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

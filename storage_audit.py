"""Read-only, background full-volume storage audit for ComfyUI FlaskViewer."""
import ctypes
import os
import threading
import time
import heapq
from collections import defaultdict
from flask import Blueprint, jsonify, request, send_from_directory

storage_bp = Blueprint("storage_audit", __name__)
_LOCK = threading.RLock()
_JOBS = {}
_ACTIVE_JOB = None
_MAX_TOP_FILES = 500
_MAX_EXTENSIONS = 200

def _format_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"

def _fixed_drives():
    """Return fixed Windows drive roots; portable fallback for non-Windows."""
    roots = []
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetDriveTypeW.restype = ctypes.c_uint
        mask = kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65+i)}:\\"
                try:
                    # DRIVE_FIXED = 3; ignore removable/network by default.
                    if kernel32.GetDriveTypeW(root) == 3:
                        roots.append(root)
                except Exception:
                    roots.append(root)
    else:
        roots = ["/"]
    result = []
    for root in roots:
        try:
            st = os.stat(root)
            if os.path.isdir(root) and st:
                result.append(root)
        except OSError:
            pass
    return result

def _drive_info(root):
    try:
        st = os.statvfs(root) if hasattr(os, "statvfs") else None
        if st:
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
        else:
            import shutil
            usage = shutil.disk_usage(root)
            total, free = usage.total, usage.free
        return {"root": root, "total_bytes": total, "free_bytes": free,
                "used_bytes": total-free,
                "used_percent": round((total-free)*100/total, 2) if total else 0}
    except Exception as exc:
        return {"root": root, "error": str(exc)}

def _is_reparse_or_link(path, entry=None):
    try:
        if entry is not None and entry.is_symlink():
            return True
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
            kernel32.GetFileAttributesW.restype = ctypes.c_uint32
            attrs = kernel32.GetFileAttributesW(str(path))
            if attrs != -1 and (attrs & 0x400):  # FILE_ATTRIBUTE_REPARSE_POINT
                return True
        return False
    except Exception:
        return True

def _scan_worker(job_id, roots):
    job = _JOBS[job_id]
    job.update(status="running", started_at=time.time())
    top_dirs = defaultdict(int)
    ext_sizes = defaultdict(int)
    largest = []
    errors = []
    error_count = 0
    def record_error(path, exc):
        nonlocal error_count
        error_count += 1
        if len(errors) < 5000:
            errors.append({"path": path, "error": str(exc)[:300]})
    scanned_bytes = 0
    files = folders = 0
    started = time.monotonic()
    try:
        for root in roots:
            if job.get("cancel_requested"):
                break
            stack = [root]
            while stack:
                if job.get("cancel_requested"):
                    break
                current = stack.pop()
                try:
                    with os.scandir(current) as entries:
                        for entry in entries:
                            if job.get("cancel_requested"):
                                break
                            path = entry.path
                            try:
                                if _is_reparse_or_link(path, entry):
                                    continue
                                if entry.is_dir(follow_symlinks=False):
                                    folders += 1
                                    stack.append(path)
                                    if os.path.normcase(current) == os.path.normcase(root):
                                        top_dirs[path] += 0
                                elif entry.is_file(follow_symlinks=False):
                                    size = entry.stat(follow_symlinks=False).st_size
                                    files += 1
                                    scanned_bytes += size
                                    rel = os.path.relpath(path, root)
                                    first = rel.split(os.sep, 1)[0]
                                    if os.sep in rel:
                                        top_dirs[os.path.join(root, first)] += size
                                    ext = os.path.splitext(entry.name)[1].lower() or "[no extension]"
                                    ext_sizes[ext] += size
                                    record = (size, path, entry.name, ext, entry.stat(follow_symlinks=False).st_mtime)
                                    if len(largest) < _MAX_TOP_FILES:
                                        heapq.heappush(largest, record)
                                    elif size > largest[0][0]:
                                        heapq.heapreplace(largest, record)
                            except (PermissionError, FileNotFoundError, OSError) as exc:
                                errors.append({"path": path, "error": str(exc)[:300]})
                            except Exception as exc:
                                errors.append({"path": path, "error": str(exc)[:300]})
                except (PermissionError, FileNotFoundError, NotADirectoryError, OSError) as exc:
                    record_error(current, exc)
                with _LOCK:
                    job.update(files=files, folders=folders, scanned_bytes=scanned_bytes,
                               errors_count=error_count, current_path=current,
                               elapsed_seconds=round(time.monotonic()-started, 1))
        result_files = [
            {"size_bytes": x[0], "size_human": _format_bytes(x[0]), "path": x[1],
             "name": x[2], "extension": x[3], "modified": x[4]}
            for x in sorted(largest, reverse=True)
        ]
        result_dirs = [
            {"path": p, "size_bytes": n, "size_human": _format_bytes(n)}
            for p, n in sorted(top_dirs.items(), key=lambda kv: kv[1], reverse=True)
        ]
        result_ext = [
            {"extension": e, "size_bytes": n, "size_human": _format_bytes(n)}
            for e, n in sorted(ext_sizes.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_EXTENSIONS]
        ]
        job["results"] = {"largest_files": result_files, "top_folders": result_dirs,
                          "extensions": result_ext, "errors": errors,
                          "errors_truncated": error_count > len(errors), "total_errors": error_count}
        job["status"] = "cancelled" if job.get("cancel_requested") else "completed"
    except Exception as exc:
        job["status"] = "failed"
        job["fatal_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        job["finished_at"] = time.time()
        job["elapsed_seconds"] = round(time.monotonic()-started, 1)
        with _LOCK:
            global _ACTIVE_JOB
            if _ACTIVE_JOB == job_id:
                _ACTIVE_JOB = None

@storage_bp.get("/storage")
def storage_page():
    return send_from_directory(os.path.join(os.path.dirname(__file__), "site"), "storage.html")

@storage_bp.get("/api/storage/drives")
def storage_drives():
    return jsonify([_drive_info(root) for root in _fixed_drives()])

@storage_bp.post("/api/storage/scan")
def storage_start():
    global _ACTIVE_JOB
    payload = request.get_json(silent=True) or {}
    available = _fixed_drives()
    requested = payload.get("roots")
    if not isinstance(requested, list) or not requested:
        roots = available
    else:
        roots = [r for r in requested if isinstance(r, str) and r in available]
    if not roots:
        return jsonify({"error": "No valid fixed drives selected"}), 400
    with _LOCK:
        if _ACTIVE_JOB and _JOBS.get(_ACTIVE_JOB, {}).get("status") == "running":
            return jsonify({"error": "A storage scan is already running", "job_id": _ACTIVE_JOB}), 409
        job_id = str(time.time_ns())
        _JOBS[job_id] = {"job_id": job_id, "status": "queued", "roots": roots,
                         "files": 0, "folders": 0, "scanned_bytes": 0,
                         "errors_count": 0, "current_path": "", "cancel_requested": False}
        _ACTIVE_JOB = job_id
    thread = threading.Thread(target=_scan_worker, args=(job_id, roots),
                              daemon=True, name="StorageAudit-"+job_id)
    thread.start()
    return jsonify({"job_id": job_id, "status": "queued", "roots": roots}), 202

@storage_bp.get("/api/storage/status/<job_id>")
def storage_status(job_id):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown scan job"}), 404
        public = {k: v for k, v in job.items() if k != "cancel_requested"}
        public["scanned_bytes_human"] = _format_bytes(job.get("scanned_bytes", 0))
        return jsonify(public)

@storage_bp.post("/api/storage/cancel/<job_id>")
def storage_cancel(job_id):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown scan job"}), 404
        if job.get("status") not in ("queued", "running"):
            return jsonify({"error": "Scan is not active"}), 409
        job["cancel_requested"] = True
    return jsonify({"ok": True, "status": "cancelling"})

@storage_bp.get("/api/storage/results/<job_id>")
def storage_results(job_id):
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown scan job"}), 404
        if job.get("status") not in ("completed", "cancelled"):
            return jsonify({"error": "Scan has not finished"}), 409
        return jsonify(job.get("results", {}))

# ComfyUI FlaskViewer

A ComfyUI custom node and local Flask web app for viewing generated images, submitting saved workflows, and auditing disk usage.

## Features

- Live image gallery with filtering, sorting, lightbox, and slideshow.
- Optional workflow submission through the ComfyUI API.
- Storage Audit dashboard at **/storage**.
- Background, read-only scans of all selected fixed volumes (Windows) or the root filesystem (non-Windows).
- Drive capacity overview, largest files, top-level folder totals, extension totals, progress, cancellation, and access-error reporting.

## Storage Audit

Open the FlaskViewer URL and select **Storage Audit**, or navigate directly to `/storage`.

1. Select one or more fixed drives, or choose **Scan all fixed drives**.
2. The scan runs in a background thread; the gallery server remains responsive.
3. Inspect the largest files, top-level folders, extension breakdown, and inaccessible paths.
4. A running scan can be cancelled.

### Safety and accuracy

The scanner is strictly read-only. It does not delete, move, rename, or modify user files. It does not follow symbolic links or Windows reparse points. Only fixed Windows volumes are listed by default; removable and network drives are excluded. Access-denied locations are recorded as errors.

The file-size sum is an enumeration, not a forensic measurement of allocated disk clusters. It can differ from Windows Explorer because of filesystem metadata, hard links, sparse/compressed files, inaccessible system locations, and virtual storage. The dashboard retains the 500 largest files, up to 200 extension groups, and at most 5,000 access-error records per scan.

A full scan can take a long time and may generate disk I/O. Run locally and avoid exposing the FlaskViewer server to an untrusted network: the existing application is a local utility and does not implement user authentication.

## Install

1. Place this repository in `ComfyUI/custom_nodes/ComfyUI-FlaskViewer`.
2. Restart ComfyUI.
3. Run the **Flask Image Viewer** node once, or use the extension panel's Start control.
4. Open the reported Flask URL, then select **Storage Audit**.

The plugin uses the Python environment provided by ComfyUI and its existing Flask/Werkzeug dependencies.

## API

- `GET /api/storage/drives`
- `POST /api/storage/scan` with JSON `{"roots":["C:\\"]}`; omit roots or pass an empty list to scan all fixed drives.
- `GET /api/storage/status/<job_id>`
- `GET /api/storage/results/<job_id>`
- `POST /api/storage/cancel/<job_id>`

The scan endpoint returns HTTP 202 and a job ID. Only one scan can run at a time.

## License

See [LICENSE](LICENSE).

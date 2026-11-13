# ComfyUI-FlaskViewer

A ComfyUI custom node with a companion Flask interface for image history, generation, and local file-library browsing.

## Features
- Live image gallery with prompt search, source filters, sorting, lightbox, slideshow, and downloads.
- Generate images from saved API-format ComfyUI workflows.
- Integrated File Library for ComfyUI input, output, temp, and FlaskViewer image-history folders.
- Search and sort directory contents, navigate folders, preview supported images, and download files.
- Upload image files directly to the ComfyUI input directory.
- View used/free/total capacity for detected system volumes and run a cancellable, read-only recursive scan of local fixed drives for largest files, top-level folder totals, extension usage, and access errors.
- ComfyUI sidebar panel and node-based server controls remain available.

## Install
1. Place this repository in `ComfyUI/custom_nodes/ComfyUI-FlaskViewer`.
2. Restart ComfyUI.
3. Add the **Flask Image Viewer** node, or open its sidebar panel and start the server.
4. Open the URL printed by the node or sidebar (default preferred port: `8189`).

## File Library
Open **File Library** from the gallery toolbar or visit `/library`.

The browser is restricted to known ComfyUI directories (input, output, temp) and FlaskViewer image history. It does not expose arbitrary filesystem paths. Symbolic links are skipped. Uploads accept image files only and save into ComfyUI input.

### API
- `GET /api/library/roots` — available managed directories.
- `GET /api/library/storage` — filesystem capacity for detected volumes.
- `POST /api/library/scan` — start one background scan across detected local fixed drives.
- `GET /api/library/scan/<job_id>` — read progress and final results.
- `POST /api/library/scan/<job_id>/cancel` — request cancellation.
- `GET /api/library/browse?root=output&path=&q=&limit=100&offset=0` — browse a managed directory.
- `GET /api/library/preview?root=output&path=subdir/image.png` — inline image preview.
- `GET /api/library/download?root=output&path=subdir/file.ext` — download a file.
- `POST /api/library/upload` — multipart upload to ComfyUI input; field name `files`.

## Safety
Storage scans are read-only and cancellable. They do not delete, move, rename, or modify files. The initial file manager is non-destructive: it does not delete, move, or rename existing files. Client-supplied paths are resolved and checked against the selected managed root. Symlinks are not followed.

The FlaskViewer server has no authentication layer. Keep it bound to `127.0.0.1` unless you understand the implications of exposing it to a trusted network. Do not expose it directly to the public internet.

## Configuration
Existing `config.json` fields: `host`, `port`, `keep_history`, `comfy_url`, and `comfy_input_dir`.

## Validation
Run Python syntax compilation and the included GitHub Actions checks. Runtime verification should be performed inside the target ComfyUI installation because ComfyUI directory helpers and lifecycle are supplied by the host application.

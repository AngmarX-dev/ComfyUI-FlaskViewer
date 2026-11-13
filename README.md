# ComfyUI-FlaskViewer

A ComfyUI custom node with a companion web studio for image generation and output review.

## Features
- Browser-based generation using saved API-format ComfyUI workflows.
- Positive and negative prompts, checkpoint override, dimensions, seed, steps, CFG, sampler, scheduler, denoise, latent batch size, filename prefix, and img2img uploads.
- Live output gallery with prompt search, source filters, sorting, lightbox, slideshow, and downloads.
- ComfyUI sidebar panel and node-based server controls.
- Typed `FlaskViewer Parameters` outputs for prompt, negative prompt, steps, CFG, seed, dimensions, sampler, scheduler, denoise, batch size, checkpoint, and filename prefix.
- `FlaskViewer Output` image pass-through node that publishes generated images to the web gallery.
- Local-only default binding and bounded image history.

## Install
1. Place this repository in `ComfyUI/custom_nodes/ComfyUI-FlaskViewer`.
2. Restart ComfyUI.
3. Add the **Flask Image Viewer** node, or open its sidebar panel and start the server.
4. Open the URL printed by the node or sidebar (default preferred port: `8189`).

## Generation
The web studio submits the selected API-format workflow to the local ComfyUI `/prompt` endpoint. Parameters are applied only to compatible nodes present in the selected workflow:
- Text is injected into positive and negative conditioning nodes linked to the sampler.
- Sampler, scheduler, steps, CFG, and denoise are applied to compatible sampler inputs.
- Width, height, and batch size are applied to compatible latent nodes.
- Checkpoint override applies to `CheckpointLoaderSimple`; filename prefix applies to `SaveImage`.
- Input uploads are saved into ComfyUI's input directory and assigned to `LoadImage` nodes.

A workflow that does not contain a compatible node/input will ignore that particular override. Model-specific custom workflows may require editing their API-format JSON.

### Continuous Studio-to-ComfyUI flow
The Studio is event-driven: each accepted Generate submission is posted immediately to ComfyUI's `/prompt` API. ComfyUI owns the execution queue, so requests are processed by its normal queue rather than by a CPU-spinning infinite loop. The page polls each submitted job for completion and refreshes the gallery automatically.

The included `workflows/default.json` demonstrates the node graph:
- `FlaskViewer Parameters` feeds the positive/negative encoders, sampler controls, and latent dimensions/batch size.
- `FlaskViewer Output` receives the decoded `IMAGE`, publishes it to the FlaskViewer gallery, and passes the image tensor through.

For a custom API workflow, add both nodes and connect their sockets as appropriate. The web Studio also injects its current values into the parameter node before submission, so its values take precedence over the node's saved widget defaults.

## Safety and network
The FlaskViewer server has no authentication layer. Keep it bound to `127.0.0.1` unless you understand the implications of exposing it to a trusted network. Do not expose it directly to the public internet. Uploads are limited by the server request-size cap and accepted image formats.

## Configuration
Existing `config.json` fields: `host`, `port`, `keep_history`, `comfy_url`, and `comfy_input_dir`.

## Validation
Run Python syntax compilation and JavaScript syntax checks through GitHub Actions. Runtime verification must be performed inside the target ComfyUI installation because ComfyUI directory helpers and lifecycle are supplied by the host application.

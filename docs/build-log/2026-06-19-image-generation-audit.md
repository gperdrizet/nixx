# 2026-06-19: image generation audit

Two bugs diagnosed and fixed via a full audit of the image generation system.

## what got built

**Bug 1: SDXL/SDXL Turbo/SDXL img2img edit failed immediately on every job**

Root cause was a transitive import error in diffusers. `AutoPipelineForText2Image` and `AutoPipelineForImage2Image` both import `diffusers/pipelines/auto_pipeline.py`, which has a direct (non-lazy) import of `NucleusMoEImagePipeline`. That pipeline module tries to import `Qwen3VLForConditionalGeneration` from transformers - a class that doesn't exist in the installed transformers version. The entire `from diffusers import AutoPipeline*` line raises `RuntimeError` before any model weights are touched. Jobs failed within 1 second.

Confirmed by inspecting the full traceback from journalctl. The user's benchmark of SDXL working "on the same card" was correct - it works when called directly, but fails when called through the nixx-image service because the venv has a newer diffusers that ships the broken `nucleusmoe_image` pipeline.

Fix: replace `AutoPipelineForText2Image` with `StableDiffusionXLPipeline` and `AutoPipelineForImage2Image` with `StableDiffusionXLImg2ImgPipeline` in `app.py`. Direct pipeline classes don't trigger the auto-discovery scan that imports all pipeline modules.

Files changed: `src/nixx/image_service/app.py` (`_load_sdxl`, `_load_sdxl_turbo`, `_load_sdxl_edit`).

**Bug 2: first `/gen-model` or `/image-model` command shows "set to: undefined"**

Root cause was a startup race condition in `server.py`. When nixx-image wasn't running, the model-switch endpoints fired `systemctl start nixx-image` and immediately POSTed to `http://127.0.0.1:8090/generate-model`. systemd acknowledges the start command quickly but the FastAPI app inside nixx-image takes several seconds to bind. The immediate POST got connection refused, propagated as a 502 response with `{"detail": "..."}`. The PWA read `d.model` which was `undefined` on that shape and displayed "Image generate model set to: undefined". On the second attempt, the service was already up, so it worked.

Fix:
- `server.py`: extracted `_start_and_wait_image_service()` at module level. It checks health first; if not up, starts the service via systemctl and polls `/health` every 1.5 s until ready (45 s timeout). Both `set_generate_model` and `set_edit_model` now call this before any POST.
- `web/index.html`: added `r.ok` check in both `/gen-model` and `/image-model` handlers so a non-200 response shows `d.detail` instead of trying to display `d.model`.

Files changed: `src/nixx/server.py`, `src/nixx/web/index.html`.

**Previous session fix (2026-06-15): auto-revert on SDXL failure**

Also noted in context: `_run_generate_sdxl` and `_run_edit_sdxl` now revert `_active_generate_model` to `sd21` (or `_active_edit_model` to `ip2p`) when a job fails, preventing a stuck model state from poisoning all subsequent jobs.

## commit

`8278645` - "fix: replace AutoPipeline with direct classes; fix model-switch startup race"

## current state

- sd14, sd21: working
- sdxl, sdxl_turbo: should work now (AutoPipeline removed); model offload enabled
- sdxl_edit: should work now
- ip2p, magic_brush: working
- kontext: CPU mode, ~2-3 hours per job
- model switch from PWA (`/gen-model`, `/image-model`): fixed, now waits for service startup

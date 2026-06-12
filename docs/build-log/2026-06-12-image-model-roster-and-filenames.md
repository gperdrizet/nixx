# 2026-06-12: image model roster and filenames

## What got built

### Descriptive filenames for generated and edited images

Previously, output images were named after the raw job UUID (e.g. `0e1f0c9f...png`). Now `filename`
is a required argument on both `generate_image` and `edit_image` tools. The model provides 1-3
lowercase hyphen-separated words describing the subject (e.g. `red-cat`, `dog.1` for a revision).
The image service sanitizes the slug and appends a 6-char job suffix: `red-cat-a3f9b2.png`.

The endpoint computes the output path before spawning the background thread and returns it in the
POST response so the tool can report the real filename immediately rather than guessing after the
fact.

### Multi-model image service

FLUX.1 Schnell was removed. The GTX 1070 (8 GB VRAM) doesn't have enough headroom to run Schnell
(6.9 GiB model) with inference activations. Replaced with an explicit roster of SD-family models:

**Generation** (switch with `/gen-model` or `POST /v1/image/generate-model`):
- `sd21` (default) - SD 2.1 Base, GPU only, ~3.5 GiB
- `sd14` - SD 1.4, GPU only, ~2 GiB
- `sdxl` - SDXL, model offload, ~6.9 GiB
- `sdxl_turbo` - SDXL Turbo, model offload, 4-step

**Editing** (switch with `/image-model` or `POST /v1/image/edit-model`):
- `ip2p` (default) - InstructPix2Pix, GPU only, ~1.7 GiB
- `magic_brush` - MagicBrush, GPU only, ~1.7 GiB (IP2P fine-tune on real editing data)
- `sdxl_edit` - SDXL img2img, sequential offload (describe target image, not the change)
- `kontext` - FLUX.1 Kontext [dev], CPU only, ~24 GiB, ~2-3 hours

Each model has its own loader that unloads all others before loading, so only one generation model
and one edit model are resident at a time. `torch.cuda.empty_cache()` is called in every job
finally block to prevent VRAM leaks on failure.

### Chat commands

Added `/gen-model [sd14|sd21|sdxl|turbo]` to both the PWA and the TUI.
`/image-model` updated: aliases are now `ip2p`, `mb` (MagicBrush), `xe` (SDXL img2img), `kontext`.

### Admin dashboard

- Model labels in service table corrected (no longer shows "Schnell + Kontext [dev]").
- Config panel now shows active generation and edit models after intent frequency.
- Full model roster table (all 8 models with hardware and size) shown in the config panel.
- Both model-switch commands added to the TUI cheatsheet.

### Tool instruction fix

Tool result messages were telling the model to "report back" after a job, which caused her to
promise async notifications she can't deliver. Wording corrected to "use `image_status` when the
user asks".

## What didn't work

- `enable_model_cpu_offload()` on FLUX.1 Schnell does not help - the transformer itself requires
  ~6.85 GiB fully on GPU during the denoising loop, leaving no room for activations on 8 GB VRAM.
  This is a fundamental model size constraint, not a configuration issue.
- Multiple OOM failures left CUDA memory fragmented across jobs because there was no
  `torch.cuda.empty_cache()` in error paths. Fixed.

## Files changed

- `src/nixx/image_service/app.py` - full model roster, loaders, dispatchers, endpoints
- `src/nixx/tools/image_tools.py` - filename argument, updated tool descriptions
- `src/nixx/server.py` - `/v1/image/generate-model` GET/POST, updated edit-model validation
- `src/nixx/web/index.html` - `/gen-model` command, updated `/image-model` aliases and help
- `src/nixx/tui/app.py` - `_image_model()` and `_gen_model()` worker methods and dispatch
- `src/nixx/admin.py` - correct service label, image_models in config API response
- `src/nixx/admin_web/index.html` - cheatsheet commands, models panel in config card
- `docs/project-state.md` - model roster, API routes, commands, tools, pitfalls

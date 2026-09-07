# Nixx: project state

Current snapshot of the project as it stands. Update this at the end of any session where
something meaningful changes. Read this at the start of a session to get up to speed fast.

---

## What nixx is

Self-hosted personal knowledge base and memory system for a single user. Local-first, no cloud,
no auth (Tailscale for remote access). The point is persistent, unified context across all
workspaces and conversations - nixx remembers everything across sessions.

---

## Processes and ports

| Process | Command / binary | Port | User | Notes |
|---|---|---|---|---|
| nixx API server | `nixx serve` via pipx | 8000 | siderealyear | FastAPI + Uvicorn. Serves API + PWA at `/app`. |
| nixx-admin | `nixx-admin` via pipx | 8001 | siderealyear | Admin dashboard, binds to 0.0.0.0:8001. Proxied at `https://nixx.perdrizet.org/admin`. |
| nixx-image | `nixx-image` via dedicated venv | 8090 | siderealyear | On-demand SD/SDXL image service. `Restart=no` - stays dead until explicitly started. Auto-shuts-down after 10 min idle (never while a job is running). |
| LLM backend | promptly gateway (`promptlyapi.com/v1`) | 8502* | llama | Experimental OpenAI-compatible API we run; nixx's LLM endpoint. Served model + context change over time (auto-discovered at runtime). *promptly proxies a local `llama-server` on 8502. |
| Embed server | llama.cpp `llama-server` | 8082 | llama | mxbai-embed-large-v1-f16.gguf |
| PostgreSQL | Docker container | 5432 | postgres | `student-postgres` container; starts automatically with Docker. Managed via `~/postgreSQL-server/docker-compose.yml`. |
| pgadmin | Docker container | 8088 | - | `dpage/pgadmin4`, binds to Tailscale IP (100.64.0.2:8088). DB browser. |
| SearXNG | Docker container | 8888 | - | `services/searxng/`, `docker compose up -d`. Binds to 127.0.0.1. |
| Grafana | Docker container | 3000 | - | Metrics dashboard. Paired with `postgres-exporter` on port 9187. |
| postgres-exporter | Docker container | 9187 | - | Exports PostgreSQL metrics for Grafana. |

All services run under `nixx.target` but **restarting the target does not cascade to individual
services**. To pick up code changes: `sudo systemctl restart nixx-server`.

---

## Models

**LLM** is provided by the **promptly gateway** (`promptlyapi.com/v1`), an experimental
OpenAI-compatible inference API we run. nixx does not hardcode the model or context window -
promptly's loaded model and context change over time. nixx auto-discovers the current model
from `/v1/models` and context length from `/props` at startup (and retries on `/health`);
`gpt-oss-20b` / `8192` are fallback defaults only. promptly currently proxies a local
llama.cpp on port 8502 (managed in the llama.cpp repo).

The **embedding** model runs locally: `mxbai-embed-large-v1-f16.gguf` in `/opt/models/`
(owned by `llama:llama`), served at port 8082, 1024-dimensional vectors.

Image models are HuggingFace repos cached in `/mnt/fast_scratch/huggingface_transformers_cache` (fast NVMe).

GPU assignment (PCI bus order): device 0 = GTX 1070 (8 GB), devices 1-2 = Tesla P100
(16 GB each). promptly's local llama.cpp runs on the two P100s; the embedding server is
pinned to the GTX 1070 via `CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES=0`.
nixx-image also targets the GTX 1070 - verify its `CUDA_VISIBLE_DEVICES` ordering, since
without `CUDA_DEVICE_ORDER=PCI_BUS_ID` CUDA enumerates fastest-first (device 0 = a P100).

**Generation models** (switch with `/gen-model` in TUI or `POST /v1/image/generate-model`):

| ID | Repo | Hardware | VRAM | Notes |
|---|---|---|---|---|
| `sd14` | `CompVis/stable-diffusion-v1-4` | GPU only | ~2 GiB | SD 1.x baseline |
| `sd21` | `stabilityai/stable-diffusion-2-1-base` | GPU only | ~3.5 GiB | **default** |
| `sdxl` | `stabilityai/stable-diffusion-xl-base-1.0` | model offload | ~6.9 GiB | Higher quality, slower |
| `sdxl_turbo` | `stabilityai/sdxl-turbo` | model offload | ~6.9 GiB | 4-step distilled |

**Editing models** (switch with `/image-model` in TUI or `POST /v1/image/edit-model`):

| ID | Repo | Hardware | VRAM | Notes |
|---|---|---|---|---|
| `ip2p` | `timbrooks/instruct-pix2pix` | GPU only | ~1.7 GiB | **default**, ~1-2 min |
| `magic_brush` | `osunlp/MagicBrush` | GPU only | ~1.7 GiB | IP2P fine-tune, real edits |
| `sdxl_edit` | `stabilityai/stable-diffusion-xl-base-1.0` | sequential offload | ~6.9 GiB | img2img; describe target, not change |
| `kontext` | `black-forest-labs/FLUX.1-Kontext-dev` | CPU only | ~24 GiB | Gated, ~2-3 hours |

All models lazy-load on first request; only one generation model and one edit model can be resident at a time (loading a new one unloads the current one).

Output filenames are model-provided descriptive slugs + 6-char job ID suffix (e.g. `red-cat-a3f9b2.png`). `_safe_filename()` sanitizes and truncates to 60 chars.

---

## Installation

nixx is installed into pipx: `pipx install --editable .`
Stable binary at `~/.local/bin/nixx`. Editable so Python file changes are live - the running
server process still needs a restart, but no reinstall is needed.

---

## Database tables

PostgreSQL database: `nixx`. All tables in public schema.

| Table | Purpose |
|---|---|
| `buffer` | Append-only transcript of all messages. Has `tsvector` column + GIN index for full-text search. Role values: `user`, `assistant`, `marker` (session boundary). |
| `summaries` | Episodic memory. LLM-generated summaries of buffer ranges. Has `embedding vector(1024)`, `tags TEXT[]`, `entities JSONB`, `start_buffer_id`, `end_buffer_id`. |
| `sources` | Semantic memory units - named slices of the buffer or ingested documents. Has `name`, `type`, `summary`, `start_id`, `end_id`. |
| `memories` | Embedded chunks for semantic recall. Has `embedding vector(1024)`, `source_id FK → sources`, `metadata JSONB`. |
| `source_projects` | Maps sources to project names. PK is `(source_id, project)`. |
| `source_edges` | Knowledge graph edges between sources. Has `relation TEXT`, `weight FLOAT`, `activations INT`, `last_activated`. PK is `(from_id, to_id)`. |
| `state` | Persistent server state (key/value). Stores `intent`, `project_dir`, `tool_usage` (JSON). Survives restarts. |

Schema is initialised on server startup via `init_schema()` in `memory/db.py`. Migrations are
applied inline with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

Useful queries: see [queries.md](queries.md).

---

## Service files

Unit files live in `scripts/`, symlinked into `/etc/systemd/system/`:

| File | Description |
|---|---|
| `scripts/nixx.target` | Groups all nixx services |
| `scripts/nixx-server.service` | API server, `User=siderealyear`, uses `~/.local/bin/nixx serve`, reads `/home/siderealyear/nixx/.env`. `NoNewPrivileges` and `PrivateTmp` removed (were blocking sudo). |
| `scripts/nixx-admin.service` | Admin dashboard, `User=siderealyear`, binds to Tailscale IP 100.64.0.2:8001. |
| `scripts/nixx-embed.service` | Embed server, `User=llama`, model at `/opt/models/mxbai-embed-large-v1-f16.gguf` |
| `/etc/systemd/system/nixx-image.service` | On-demand image service. Not in `scripts/` - managed directly in `/etc/systemd/system/`. `Restart=no`, `CUDA_VISIBLE_DEVICES=1`, `HF_HOME=/mnt/fast_scratch/huggingface_transformers_cache`. Venv: `~/.local/share/pipx/venvs/nixx-image/`. |

All services are enabled for auto-boot. Docker starts first, which brings up `student-postgres` (restart policy: unless-stopped). `nixx-server` and `nixx-embed` start after Docker. `llamacpp` starts independently. Allow ~60 seconds after boot for llamacpp to load the model before the first inference request.

---

## Source layout

```
src/nixx/
  __init__.py
  cli.py          — argparse CLI: serve, status, chat subcommands
  config.py       — NixxConfig (pydantic-settings, NIXX_ prefix, reads .env)
  prompts.py      — SYSTEM_PROMPT and INTENT_DERIVATION_PROMPT
  server.py       — FastAPI app factory, all API routes
  llm/
    __init__.py
    openai_client.py  — OpenAIClient: chat(), embed(), streaming; wraps OpenAI-compat API
  memory/
    __init__.py
    db.py         — asyncpg schema, all SQL helpers
    store.py      — MemoryStore: high-level embed+save+recall methods
  ingest/
    __init__.py
    chunker.py    — Text → chunks splitter
    pipeline.py   — IngestPipeline: read → chunk → embed → index
    reader.py
    handlers/
      base.py     — IngestHandler ABC
      file.py     — FileHandler (default fallback)
      web.py      — WebHandler (BeautifulSoup, matches URLs with ://)
      registry.py — HandlerRegistry: first match wins
  tools/
    __init__.py
    base.py       — ToolResult, BaseTool ABC
    file_tools.py — ReadFileTool, WriteFileTool, EditFileTool, ListDirTool, DeleteFileTool
    memory_tools.py — SearchTranscriptTool, ViewTranscriptTool
    permissions.py — Directory permission helpers (is_path_allowed, get_project_dir, set_project_dir)
    planning.py   — ReadPlanTool, WritePlanTool, get_current_plan
    run_python.py — RunPythonTool (sandboxed subprocess, unshare -rn for network isolation)
    shadow.py     — shadow_backup() — auto-snapshot before file modifications
    web_search.py — WebSearchTool (SearXNG JSON API, requires X-Forwarded-For header)
    read_webpage.py — ReadWebpageTool (httpx + BeautifulSoup, 8000 char limit)
    image_tools.py — GenerateImageTool (SD 1.4/2.1/SDXL/SDXL Turbo), EditImageTool (IP2P/MagicBrush/SDXL img2img/Kontext). Both auto-start nixx-image via `sudo systemctl start nixx-image` if not running. Filename is a required model-provided argument.
    registry.py   — ToolRegistry: registers tools, builds OpenAI tool defs, executes calls
  image_service/
    __init__.py
    app.py        — FastAPI image service: /generate (SD family), /edit (IP2P/MagicBrush/SDXL/Kontext), /generate-model (GET/POST), /edit_model (GET/POST), /jobs, /health. Lazy-loads models on first request, shuts down after 10 min idle.
  tui/
    __init__.py
    app.py        — NixxApp (Textual): full chat UI
```

---

## Config (NixxConfig)

All settings read from `.env` with `NIXX_` prefix. Key fields:

| Setting | Default | Notes |
|---|---|---|
| `host` / `port` | `127.0.0.1` / `8000` | API server bind |
| `llm_base_url` | `http://localhost:8080` | Overridden in .env to `https://promptlyapi.com/v1` (promptly gateway) |
| `llm_model` | `gpt-oss-20b` | Fallback only; real model auto-fetched from the gateway's `/v1/models` at startup |
| `llm_context_length` | `8192` | Fallback only; auto-fetched from the gateway's `/props` at startup (overrides .env value) |
| `max_history_tokens` | `16384` | Max tokens of conversation history per request, independent of context length. Prevents slow prefill on long sessions. |
| `llm_request_timeout` | `600.0` | Seconds to wait for first token from LLM (covers prefill). Used as `read` timeout in split `httpx.Timeout`. |
| `embedding_base_url` | `http://localhost:8082` | |
| `embedding_dimensions` | `1024` | Must match the model |
| `summary_interval` | `1000` | Words between episodic summary prompts |
| `intent_interval` | `5` | Messages between auto intent derivation |
| `intent_lookback` | `10` | Messages analyzed for intent |
| `recall_threshold` | `0.4` | Minimum cosine similarity for episodic recall injection |
| `searxng_url` | `http://localhost:8888` | Base URL for SearXNG container |
| `scratch_dir` | `~/nixx_scratch` | Tool read/write sandbox |
| `database_url` | `postgresql://nixx:changeme@localhost/nixx` | Overridden in .env |

**Pitfall**: `NixxConfig()` instantiation creates directories on disk. Never instantiate it
at test module scope.

---

## API routes

All routes on the nixx server (port 8000):

```
GET  /health                     — {status, model, context_length}
GET  /v1/debug/context          — last assembled system message + recall hits + token usage
POST /v1/chat/completions        — OpenAI-compatible, streaming + non-streaming, tool loop
POST /v1/ingest                  — ingest file path or URL → sources + memories
POST /v1/sources                 — create source from buffer range
GET  /v1/sources                 — list sources (optional ?name= filter)
GET  /v1/sources/{id}
GET  /v1/sources/{id}/content    — all memory chunks for a source
GET  /v1/buffer/session          — buffer entries since last session marker
POST /v1/buffer/clear            — write session marker (start new session)
GET  /v1/episodic/status         — summary due? current word count, interval, recall_threshold
POST /v1/episodic/config         — update interval_words, recall_enabled, recall_threshold at runtime
POST /v1/episodic/summary        — create summary now (no body required)
POST /v1/episodic/search         — vector search summaries (body: {query, top_k})
GET  /v1/episodic/transcript     — buffer entries for a range (?start_id=&end_id=)
GET  /v1/episodic/summaries      — list all summaries
GET  /v1/intent                  — get current intent + messages_since_derivation
POST /v1/intent                  — set intent manually (body: {intent})
DELETE /v1/intent                — clear intent
POST /v1/intent/derive           — trigger intent derivation immediately
GET  /v1/project                 — get scratch_dir + current project directory
POST /v1/project                 — set project directory (body: {directory})
DELETE /v1/project               — clear project directory
GET  /v1/image/jobs              — proxy to nixx-image /jobs (returns empty list if service is down)
GET  /v1/image/preview           — serve image file from scratch_dir (?path=absolute_path)
POST /v1/image/crop              — crop image to square (body: {path, x, y, size}) → {cropped_path}
GET  /v1/image/generate-model   — proxy to nixx-image /generate-model → {model}
POST /v1/image/generate-model   — set active generation model (body: {model: 'sd14'|'sd21'|'sdxl'|'sdxl_turbo'})
GET  /v1/image/edit-model        — proxy to nixx-image /edit_model → {model}
POST /v1/image/edit-model        — set active edit model (body: {model: 'ip2p'|'magic_brush'|'sdxl_edit'|'kontext'})
GET  /v1/files                   — list scratch directory (optional ?subdir=)
GET  /v1/files/download          — download a file (?path=relative/path)
DELETE /v1/files                 — delete a file (?path=relative/path)

---

## PWA (web client)

Served by nixx-server at `/app`. Static files in `src/nixx/web/` - live from source because nixx is installed with `pipx install --editable .`. No reinstall needed for static file changes; `sudo systemctl restart nixx-server` required for `.py` changes.

Public HTTPS endpoint: `https://nixx.perdrizet.org` (nginx on gatekeeper VPS, proxies over Tailscale to 100.64.0.2:8000). Protected with HTTP basic auth (`/etc/nginx/nixx.htpasswd` on gatekeeper). Admin dashboard proxied at `/admin` → 100.64.0.2:8001. Nginx config: `/etc/nginx/conf.d/nixx.conf` on gatekeeper.

Installed as a PWA on Android (Chrome → Add to home screen → Install). Manifest at `/app/manifest.json` with 192×512 PNG icons (`icon-192.png`, `icon-512.png`) generated at `src/nixx/web/`. True standalone mode requires HTTPS + PNG icons ≥ 192×192 - SVG-only manifests are not installable by Chrome.

Status bar is a single centered line: `recall · intent · context N% · summary N%`. `recall` and `intent` are clickable toggle pills (green=on, muted=off). Context and summary show fill % in green/yellow/red. Tab bar is at the bottom (mobile nav pattern). Admin tab uses a relative iframe src `/admin`.

Service worker (`sw.js`) caches only the app shell (`/app/`, manifest, icon). API calls are never cached. If the PWA shows stale content after an update: hard-reload in browser, or bump `CACHE` version in `sw.js` to force SW replacement.

Key classes:
- `NixxApp` — main Textual app
- `ChatInput(TextArea)` — multi-line input; `Enter`=send, `Shift+Enter`=newline
- `Message(Static)` — focusable message bubbles; `Enter`=edit, `Backspace`=rewind, `y`=yank to clipboard
- `ContextBar` — token usage gauge (format: `context ████░░ XX% (n/total tok)`)
- `SummaryBar` — summary word-count progress gauge (format: `summary ████░░ XX% (n/interval wds)`)
- `IntentBar` — current intent string; always visible, shows `intent: -` when no intent is set

Layout (bottom of screen, stacked): context-row → summary-row → IntentBar → toggles-row → input-row.

Toggles row has two switches: **recall** (`Ctrl+R`) and **intent** (`Ctrl+I`). Labels color green (on)
or red (off). Both sync from server state on the `_update_summary_bar` polling cycle.

When an episodic summary is due (auto-triggered by word count, or via `/summary`), it runs
immediately in the background - no user input needed. A `Summary created` system message appears
inline with the LLM-derived tags and entities.

PWA slash commands: `/help`, `/clear`, `/summary`, `/search "q"`, `/context`, `/recall`, `/intent-toggle`, `/intent [text|clear]`, `/project [dir|clear]`, `/threshold [0.0-1.0]`, `/interval [n]`, `/image-model [ip2p|mb|xe|kontext]` (switch edit model; mb=MagicBrush, xe=SDXL img2img), `/gen-model [sd14|sd21|sdxl|turbo]` (switch generation model).

Crop modal: when `edit_image` is called with a non-square image, the server emits a `crop_needed` SSE event before `[PAUSE]`. The PWA shows a canvas with the image and a draggable square crop box. Confirm crops server-side via `POST /v1/image/crop` and resumes with the patched path. Cancel resumes with the original path (diffusers auto-center-crops).

Tool call events: when nixx calls a tool mid-stream, a dim `▸ tool_name` line is appended
inline inside the streaming assistant message (not a separate system message).

---

## Memory system

### Episodic memory (automatic)

1. Every message pair is written to `buffer`.
2. Server tracks unsummarized word count. When it exceeds `summary_interval`, the TUI auto-triggers
   a summary (no user input needed).
3. LLM generates summary, extracts entities, and derives tags in a single call. Stored in `summaries`
   with embedding.
4. Recall: on each chat turn, last user message is embedded → cosine similarity search over
   `summaries` → top 3 results above `recall_threshold` injected into system prompt as context block.

### Semantic memory (deliberate)

- `/v1/sources`: manually mark a buffer range as a named source; generates LLM summary + indexes
  verbatim chunks into `memories`.
- `/v1/ingest`: ingest external files or URLs; chunks text, embeds each chunk, stores in
  `sources` + `memories`.
- Recall: same vector search path as episodic, but over `memories`.

### Intent

Auto-derived every `intent_interval` (5) messages by asking LLM to reflect on who it's being
in the conversation (virtue-ethics framing, not task-level). Injected into system prompt as
`## Current intent` block when `intent_enabled` is true. Default intent on cold start:
`"Understand the user's goals and assist them."` Persisted across restarts in the `state` table
(key `intent`). Can be set/cleared manually via API or `/intent` TUI command. Clearing resets
to the default (not null).

---

## Tools (ToolRegistry)

LLM-callable tools, accessible within `scratch_dir` (`~/nixx_scratch`) and an optional
project directory. The project directory is set via `/project <dir>` in the TUI or
`POST /v1/project`. It is persisted in the `state` table (key `project_dir`).

| Tool | Description |
|---|---|
| `read_file` | Read a file from scratch_dir or project directory |
| `write_file` | Write a file (auto shadow backup before overwrite) |
| `edit_file` | Find-and-replace edit (old_string must appear exactly once, auto shadow backup) |
| `list_dir` | List scratch_dir, a subdirectory, or project directory |
| `delete_file` | Delete a file (auto shadow backup) |
| `read_plan` | Read the current plan (.plan.md in scratch_dir) |
| `write_plan` | Write/replace the current plan (injected into system prompt automatically) |
| `run_python` | Execute Python in a sandboxed subprocess (unshare -rn for network isolation, 30s timeout) |
| `search_transcript` | Full-text search over buffer |
| `view_transcript` | Retrieve buffer entries by ID range |
| `web_search` | SearXNG JSON API, top 5 results (title/URL/snippet). Requires `X-Forwarded-For: 127.0.0.1` header. SearXNG container must be running. |
| `read_webpage` | Fetch URL, strip HTML, return up to 8000 chars of text |
| `generate_image` | Generate image via SD 1.4/2.1/SDXL/SDXL Turbo. Active model set with `/gen-model`. `filename` is a required argument (1-3 words, lowercase, hyphen-separated, dotted revisions). Output to `~/nixx_scratch/images/<filename>-<job6>.png`. Auto-starts nixx-image. |
| `edit_image` | Edit image via IP2P, MagicBrush, SDXL img2img, or Kontext. Active model set with `/image-model`. Same `filename` convention. Auto-starts nixx-image. |
| `image_status` | Check status of a generation or editing job. Use this when the user asks for a progress update. |

Shadow backups are stored at `~/.nixx/shadows/` with timestamps, preserving directory structure.

Tool calls are signalled to the TUI via a `{"tool_call": {"name": "..."}}` SSE event emitted
before execution. The TUI renders a dim inline `calling tool: <name>` message.

---

## Key pitfalls

- `NixxConfig()` creates directories - never instantiate at test module scope.
- `sudo systemctl restart nixx.target` does NOT restart individual services.
- `.venv/` is the project virtualenv (used for dev/tests). The running server uses the pipx venv at
  `~/.local/share/pipx/venvs/nixx/`.
- The `llm_base_url` default in config.py (port 8080) is a placeholder. `.env` overrides it to
  the promptly gateway (`https://promptlyapi.com/v1`).
- DB table for episodic summaries is `summaries` (not `episodic_summaries`).
- `pre-commit` hook requires venv activated. Bypass with `git -c core.hooksPath=/dev/null commit`.
- `llm_model` and `llm_context_length` in `.env`/config are fallbacks only. At startup nixx fetches
  the current model from the gateway's `/v1/models` and context length from `/props` (both proxied by
  promptly). The running values can be verified at `GET /health`. If the gateway is unreachable when
  nixx-server starts, the fetches fall back to the config values; the `/health` route retries them.
- `/props` returns `n_ctx` under `default_generation_settings`, not at the top level.
- nixx-image has `Restart=no` by design - it stays dead after idle shutdown. `sudo systemctl start nixx-image` to bring it up. Tools do this automatically.
- Completed image jobs (both generate and edit) are appended to `~/nixx_scratch/image_jobs.jsonl` for persistence across service restarts. Admin metrics reads this file first, then merges live in-memory jobs.
- `tool_usage` is persisted in the `state` table (key `tool_usage`, JSON). Loaded on startup, saved after every tool call. No longer lost on restart.
- SD 1.4 and SD 2.1 Base load with `.to("cuda")`, float16, no offloading. SD 2.1 is the default generation model.
- SDXL and SDXL Turbo use `enable_model_cpu_offload()` (peak VRAM ~3-4 GiB during inference).
- IP2P and MagicBrush use `.to("cuda")`, float16. Both are IP2P-based pipelines. 50 steps, image_guidance_scale=1.5, guidance_scale=7.5. Input resized to nearest multiple of 8.
- SDXL img2img uses `enable_sequential_cpu_offload()` (peak VRAM ~3-4 GiB). Input/output must be multiples of 64.
- Kontext runs fully on CPU - no CUDA calls at all. Flux transformer allocates ~6.77 GiB in a single attention tensor, larger than GTX 1070's 8 GB. VAE slicing+tiling enabled. Takes ~2-3 hours.
- `transformers` pinned to `<4.52` in the nixx-image venv. 4.52+ requires `torch.float8_e8m0fnu` which needs torch 2.7; image venv has torch 2.6+cu124.
- HF Kontext model is gated - `HF_READ_TOKEN` must be in `.env` and `huggingface-cli login` must have been run once to persist the token to `~/.cache/huggingface/token`.
- `NoNewPrivileges=true` and `PrivateTmp=true` removed from nixx-server and nixx-admin service units - they blocked `sudo systemctl start nixx-image` from within the process.
- Sudoers rule for image service: `/etc/sudoers.d/nixx-admin` allows `NOPASSWD` for `systemctl start/stop nixx-image`.
- SearXNG requires `X-Forwarded-For: 127.0.0.1` header on requests, and `format=json` must be
  explicitly listed in `settings.yml` under `search.formats`. If the container is restarted after
  changing `settings.yml`, use `docker compose down && docker compose up -d` (not just `restart`)
  to ensure the bind-mount file is re-read.

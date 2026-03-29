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
| nixx API server | `nixx serve` via pipx | 8000 | siderealyear | FastAPI + Uvicorn |
| LLM server | llama.cpp `llama-server` | 8502 | llama | gpt-oss-20b-mxfp4.gguf |
| Embed server | llama.cpp `llama-server` | 8082 | llama | mxbai-embed-large-v1-f16.gguf |
| pgweb | `/usr/local/bin/pgweb` | 8081 | siderealyear | DB browser |
| PostgreSQL | system service | 5432 | postgres | nixx DB: `postgresql://nixx:...@localhost/nixx` |
| SearXNG | Docker container | 8888 | - | `services/searxng/`, `docker compose up -d` |

All services run under `nixx.target` but **restarting the target does not cascade to individual
services**. To pick up code changes: `sudo systemctl restart nixx-server`.

---

## Models

Both models live in `/opt/models/`, owned by `llama:llama`:

- **LLM**: `gpt-oss-20b-mxfp4.gguf` — served at port 8502, OpenAI-compatible API
- **Embeddings**: `mxbai-embed-large-v1-f16.gguf` — served at port 8082, 1024-dimensional vectors

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
| `state` | Persistent server state (key/value). Currently stores `intent`. Survives restarts. |

Schema is initialised on server startup via `init_schema()` in `memory/db.py`. Migrations are
applied inline with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

Useful queries: see [queries.md](queries.md).

---

## Service files

Unit files live in `scripts/`, symlinked into `/etc/systemd/system/`:

| File | Description |
|---|---|
| `scripts/nixx.target` | Groups all nixx services |
| `scripts/nixx-server.service` | API server, `User=siderealyear`, uses `~/.local/bin/nixx serve`, reads `/home/siderealyear/nixx/.env` |
| `scripts/nixx-embed.service` | Embed server, `User=llama`, model at `/opt/models/mxbai-embed-large-v1-f16.gguf` |
| `scripts/nixx-pgweb.service` | pgweb browser, `User=siderealyear`, `--listen=8081` |

Services are not enabled for auto-boot. Start manually with `sudo systemctl start nixx.target`.

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
    file_tools.py — ReadFileTool, WriteFileTool, ListDirTool, DeleteFileTool
    memory_tools.py — SearchTranscriptTool, ViewTranscriptTool
    web_search.py — WebSearchTool (SearXNG JSON API, requires X-Forwarded-For header)
    read_webpage.py — ReadWebpageTool (httpx + BeautifulSoup, 8000 char limit)
    registry.py   — ToolRegistry: registers tools, builds OpenAI tool defs, executes calls
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
| `llm_base_url` | `http://localhost:8080` | Overridden in .env to port 8502 |
| `llm_model` | `gpt-oss-20b` | |
| `llm_context_length` | `8192` | Auto-fetched from LLM `/props` at startup (overrides .env value). Fallback if fetch fails. |
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
```

---

## TUI (app.py)

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

TUI slash commands: `/help`, `/context`, `/summary`, `/search "q"`, `/transcript <id> [end]`,
`/clear`, `/recall`, `/interval [n]`, `/intent [text]`, `/intent-bar` (toggle IntentBar),
`/threshold [0.0-1.0]` (view or set recall similarity threshold).

Tool call events: when nixx calls a tool mid-stream, a dim `calling tool: <name>` system
message appears inline in the chat.

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

Auto-derived every `intent_interval` (5) messages by asking LLM to analyze recent exchange.
Injected into system prompt as `## Current Intent` block when `intent_enabled` is true.
Default intent on cold start: `"Understand the user's goals and assist them."` Persisted across
restarts in the `state` table (key `intent`). Can be set/cleared manually via API or `/intent`
TUI command. Clearing resets to the default (not null).

---

## Tools (ToolRegistry)

LLM-callable tools, sandboxed to `scratch_dir` (`~/nixx_scratch`):

| Tool | Description |
|---|---|
| `read_file` | Read a file from scratch_dir |
| `write_file` | Write a file to scratch_dir |
| `list_dir` | List scratch_dir or a subdirectory |
| `delete_file` | Delete a file from scratch_dir |
| `search_transcript` | Full-text search over buffer |
| `view_transcript` | Retrieve buffer entries by ID range |
| `web_search` | SearXNG JSON API, top 5 results (title/URL/snippet). Requires `X-Forwarded-For: 127.0.0.1` header. SearXNG container must be running. |
| `read_webpage` | Fetch URL, strip HTML, return up to 8000 chars of text |

Tool calls are signalled to the TUI via a `{"tool_call": {"name": "..."}}` SSE event emitted
before execution. The TUI renders a dim inline `calling tool: <name>` message.

---

## Key pitfalls

- `NixxConfig()` creates directories - never instantiate at test module scope.
- `sudo systemctl restart nixx.target` does NOT restart individual services.
- `.venv/` is the project virtualenv (used for dev/tests). The running server uses the pipx venv at
  `~/.local/share/pipx/venvs/nixx/`.
- The `llm_base_url` default in config.py (port 8080) is wrong for this machine. The real LLM
  port (8502) is set in `.env`.
- DB table for episodic summaries is `summaries` (not `episodic_summaries`).
- `pre-commit` hook requires venv activated. Bypass with `git -c core.hooksPath=/dev/null commit`.
- `llm_context_length` in `.env` is overridden at startup by the `/props` fetch. The running
  value can be verified at `GET /health` (`context_length` field) or from server logs at startup.
- `/props` returns `n_ctx` under `default_generation_settings`, not at the top level.
- SearXNG requires `X-Forwarded-For: 127.0.0.1` header on requests and `format=json` must be
  explicitly listed in `settings.yml` under `search.formats`. If the container is restarted after
  changing settings.yml, use `docker compose down && docker compose up -d` (not just `restart`) to
  ensure the bind-mount file is re-read correctly.
- `wg-quick@wg0` uses a hostname endpoint (`perdrizet.org:51820`). If it starts before DNS is
  available it fails silently and stays down. Fixed by
  `/etc/systemd/system/wg-quick@wg0.service.d/wait-for-network.conf` with
  `After=network-online.target`. If the tunnel is down, `gpt.perdrizet.org` will be unreachable
  and all LLM calls will 504. Check with `sudo systemctl status wg-quick@wg0`.

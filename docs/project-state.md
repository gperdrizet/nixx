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
| `llm_context_length` | `8192` | Token budget for context truncation |
| `embedding_base_url` | `http://localhost:8082` | |
| `embedding_dimensions` | `1024` | Must match the model |
| `summary_interval` | `1000` | Words between episodic summary prompts |
| `intent_interval` | `10` | Messages between auto intent derivation |
| `intent_lookback` | `10` | Messages analyzed for intent |
| `scratch_dir` | `~/nixx_scratch` | Tool read/write sandbox |
| `database_url` | `postgresql://nixx:changeme@localhost/nixx` | Overridden in .env |

**Pitfall**: `NixxConfig()` instantiation creates directories on disk. Never instantiate it
at test module scope.

---

## API routes

All routes on the nixx server (port 8000):

```
GET  /health
GET  /v1/debug/context          — last assembled system message + recall hits + token usage
POST /v1/chat/completions        — OpenAI-compatible, streaming + non-streaming, tool loop
POST /v1/ingest                  — ingest file path or URL → sources + memories
POST /v1/sources                 — create source from buffer range
GET  /v1/sources                 — list sources (optional ?name= filter)
GET  /v1/sources/{id}
GET  /v1/sources/{id}/content    — all memory chunks for a source
GET  /v1/buffer/session          — buffer entries since last session marker
POST /v1/buffer/clear            — write session marker (start new session)
GET  /v1/episodic/status         — summary due? current word count, interval
POST /v1/episodic/config         — update interval_words, recall_enabled at runtime
POST /v1/episodic/summary        — create summary now (body: {tags: []})
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
- `ContextBar` — token usage gauge
- `SummaryBar` — summary word-count progress gauge

Tag input bar (`#tag-row`): appears when episodic summary is due. When auto-triggered (by
word-count threshold), **focus stays in the chat input** - user must Tab to the tag bar. When
triggered manually via `/summary`, tag bar auto-focuses. Typing a message while the tag bar is
open dismisses it and defers the summary.

TUI slash commands: `/help`, `/context`, `/summary`, `/search "q"`, `/transcript <id> [end]`,
`/clear`, `/recall`, `/interval [n]`, `/intent [text]`.

---

## Memory system

### Episodic memory (automatic)

1. Every message pair is written to `buffer`.
2. Server tracks unsummarized word count. When it exceeds `summary_interval`, TUI prompts for tags.
3. On confirmation: LLM generates a summary + extracts entities. Stored in `summaries` with embedding.
4. Recall: on each chat turn, last user message is embedded → cosine similarity search over
   `summaries` → top 3 injected into system prompt as context block.

### Semantic memory (deliberate)

- `/v1/sources`: manually mark a buffer range as a named source; generates LLM summary + indexes
  verbatim chunks into `memories`.
- `/v1/ingest`: ingest external files or URLs; chunks text, embeds each chunk, stores in
  `sources` + `memories`.
- Recall: same vector search path as episodic, but over `memories`.

### Intent

Auto-derived every `intent_interval` (10) messages by asking LLM to analyze recent exchange.
Injected into system prompt as `## Current Intent` block. Can be set/cleared manually via API
or `/intent` TUI command.

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

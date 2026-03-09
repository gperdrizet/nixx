# Nixx architecture

## System diagram

Processes, modules, and communication paths as they exist today.

```text
                ┌─────────────────────────────────────────┐
                │  nixx chat · Python · Textual         │
                │                                         │
                │  NixxApp                                │
                │    _history  list[dict]  (ephemeral)    │
                │    ScrollableContainer · Input          │
                └───────────────────┬─────────────────────┘
                                    │  POST /v1/chat/completions  (HTTP + SSE)
                                    │  GET  /v1/debug/context
                                    │  GET  /v1/sources
                                    │  GET  /v1/sources/{id}
                                    │  GET  /v1/sources/{id}/content
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  nixx serve · Python · Uvicorn + FastAPI                          │
│                                                                     │
│  ┌───────────────────────────┐    ┌──────────────────────────────┐  │
│  │ POST /v1/chat/completions │    │ prompts.py                   │  │
│  │ POST /v1/completions      │    │ SYSTEM_PROMPT                │  │
│  │ POST /v1/sources          │───▶│                              │  │
│  │ POST /v1/ingest           │    └──────────────────────────────┘  │
│  │ GET  /v1/sources          │                                      │
│  │ GET  /v1/sources/{id}     │                                      │
│  │ GET  /v1/sources/{id}/... │                                      │
│  │ GET  /v1/debug/context    │                                      │
│  │ GET  /health              │                                      │
│  └─────────────┬─────────────┘                                      │
│                │              ┌──────────────────────────────────┐  │
│                └─────────────▶│ MemoryStore                      │  │
│                               │ remember · recall · format_ctx   │  │
│                               │ create_source · ingest           │  │
│                               └──────────┬───────────────────┬───┘  │
│  ┌──────────────────────────┐            │ embed             │ SQL  │
│  │ OllamaClient             │◀───────────┘           asyncpg │      │
│  │ chat_stream · embed      │                       pgvector │      │
│  └──────────────┬───────────┘                                │      │
└─────────────────│────────────────────────────────────────────┬─│────┘
                  │  HTTP /api/chat + /api/embed               │ │
                  ▼                                            ▼ ▼
  ┌───────────────────────────────┐   ┌────────────────────────────────────────┐
  │  Ollama · port 11434          │   │  PostgreSQL + pgvector · port 5432     │
  │                               │   │                                        │
  │  qwen2.5-coder:7b             │   │  buffer   (append-only message tape)   │
  │    inference                  │   │  sources  (meaningful named units)     │
  │  mxbai-embed-large            │   │  memories (embedded recall index)      │
  │    1024-d embeddings          │   │                                        │
  └───────────────────────────────┘   └────────────────────────────────────────┘
```

---

## Data and state diagram

Where data lives, what it contains, and how it flows between structures.

```text
  IN-PROCESS · nixx chat
  ──────────────────────────────────────────────────────────────────────
  NixxApp._history

  ┌──────────────────────────────────────────────────────────────────┐
  │  [ {role: user,      content: what are we building?},            │
  │    {role: assistant, content: nixx is a personal...},            │
  │    {role: user,      content: <current turn>}  ]                 │
  │                                                                  │
  │  ephemeral · lost on exit · NOT the source of truth              │
  └────────────────────────────┬─────────────────────────────────────┘
                               │  sent in full every turn
                               │  each new message also written to buffer
                               ▼
  assembled by server per turn
  ┌──────────────────────────────────────────────────────────────────┐
  │  system ── SYSTEM_PROMPT + recalled memory bullets               │
  │  user    ── turn 1                                               │
  │  asst.   ── turn 1 response                                      │
  │  ...                                                             │
  │  user    ── current turn                                         │
  └────────────────────────────┬─────────────────────────────────────┘
                               │  sent to Ollama · response written to buffer
                               ▼

  PERSISTENT · PostgreSQL
  ──────────────────────────────────────────────────────────────────────

  Three tiers: buffer → sources → memories.

  The "tape recorder" is the buffer - everything goes in. A source is a meaningful unit
  marked explicitly from the buffer by the user (or proposed by nixx). Not every buffer
  entry becomes a source. Documents, web pages, repos, and buffer sections are all sources.
  Only sources feed into the recall index.

  buffer · persistent append-only tape

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  column       type            constraint   notes                        │
  │  ──────────── ─────────────── ──────────── ──────────────────────────── │
  │  id           bigint          PK           auto-increment               │
  │  role         text                         user | assistant | system    │
  │  content      text                         verbatim text                │
  │  origin       text                         tui | vscode | api           │
  │  created_at   timestamptz                  auto                         │
  └────────────────────────────────────┬────────────────────────────────────┘
                                       │
                      /source "name"   │  user marks a buffer range as a source
                      nixx proposes    │  at a natural decision or milestone point
                      not everything   │  needs to be sourced - tape always has more
                      documents, repos,│  web pages also enter here directly
                      code gen, etc.   │
                                       ▼
  sources · meaningful units that feed memory

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  column       type            constraint   notes                        │
  │  ──────────── ─────────────── ──────────── ──────────────────────────── │
  │  id           bigint          PK           auto-increment               │
  │  name         text                         user-provided label          │
  │  type         text                         buffer | document | repo | web│
  │  summary      text                         LLM-generated summary        │
  │  start_id     bigint          FK buffer    first buffer row (if buffer) │
  │  end_id       bigint          FK buffer    last buffer row  (if buffer) │
  │  created_at   timestamptz                  auto                         │
  └────────────────────────────────────┬────────────────────────────────────┘
                                       │  chunk verbatim transcript → embed
                                       │  each chunk → store in memories
                                       │  summary is for display only
                                       ▼
  memories · embedded recall index

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  column       type            constraint   notes                        │
  │  ──────────── ─────────────── ──────────── ──────────────────────────── │
  │  id           bigint          PK           auto-increment               │
  │  content      text                         verbatim chunk or doc chunk  │
  │  embedding    vector(1024)                 from mxbai-embed-large       │
  │  source_id    bigint          FK sources   which source this came from  │
  │  metadata     jsonb                        {chunk: i, total_chunks: N}  │
  │  created_at   timestamptz                  auto                         │
  └─────────────────────────────────────────────────────────────────────────┘
       ▲  recalled per turn (cosine similarity search via pgvector HNSW index)
       │  content = verbatim transcript chunks + document chunks
```

## Design principles

1. **API-first**: OpenAI compatibility enables frontend flexibility
2. **Local-first**: All data and inference on user's hardware
3. **Privacy-focused**: Encrypted storage, no external dependencies
4. **Modular**: Swappable components (vector DBs, LLM backends, frontends)
5. **Generalizable**: Config-driven for any user's setup
6. **Extensible**: Modify, add, improve

Nixx becomes the unified workflow, therefore all projects are nixx.

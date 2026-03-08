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
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  nixx serve · Python · Uvicorn + FastAPI                          │
│                                                                     │
│  ┌───────────────────────────┐    ┌──────────────────────────────┐  │
│  │ POST /v1/chat/completions │    │ prompts.py                   │  │
│  │ GET  /v1/debug/context    │───▶│ SYSTEM_PROMPT                │  │
│  │ GET  /health              │    └──────────────────────────────┘  │
│  └─────────────┬─────────────┘                                      │
│                │              ┌──────────────────────────────────┐  │
│                └─────────────▶│ MemoryStore                      │  │
│                               │ remember · recall · format_ctx   │  │
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
  │  qwen2.5-coder:7b             │   │  memories                              │
  │    inference                  │   │    id · content · embedding(1024d)     │
  │  mxbai-embed-large            │   │    source · metadata · created_at      │
  │    1024-d embeddings          │   │                                        │
  └───────────────────────────────┘   │  conversations  ┐ schema exists,       │
                                      │  messages       ┘ not yet written to   │
                                      └────────────────────────────────────────┘
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
  │  lives for one nixx chat session only · lost on exit             │
  └────────────────────────────┬─────────────────────────────────────┘
                               │  sent in full every turn
                               ▼
  assembled by server per turn
  ┌──────────────────────────────────────────────────────────────────┐
  │                                                                  │
  │  system ── SYSTEM_PROMPT  (from prompts.py)                      │
  │            + recalled memory bullets  if similarity >= 0.5       │
  │              ↑                                                   │
  │              └── top-5 rows from memories, queried by            │
  │                  cosine similarity to all user turns             │
  │                                                                  │
  │  user    ── turn 1                                               │
  │  asst.   ── turn 1 response                                      │
  │  ...                                                             │
  │  user    ── current turn  ◀── also used as the recall query      │
  │                                                                  │
  └────────────────────────────┬─────────────────────────────────────┘
                               │  sent to Ollama · qwen2.5-coder:7b
                               ▼  response streamed back token by token


  PERSISTENT · PostgreSQL
  ──────────────────────────────────────────────────────────────────────
  memories

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  column       type            constraint   notes                        │
  │  ──────────── ─────────────── ──────────── ──────────────────────────── │
  │  id           bigint          PK           auto-increment               │
  │  content      text                         verbatim message text        │
  │  embedding    vector(1024)                 from mxbai-embed-large       │
  │  source       text                         conversation | document      │
  │  metadata     jsonb                        {}  arbitrary tags           │
  │  created_at   timestamptz                  auto                         │
  └─────────────────────────────────────────────────────────────────────────┘
       ▲  recalled per turn (cosine similarity search via pgvector)
       │
       │  stored after each turn
       │  NOTE: non-streaming path only · TUI turns not saved yet
       │

  conversations  ┐
  messages       ┘  schema exists · not yet written to
```

---

## Planned architecture

Buffer + sources redesign. The "tape recorder" is the buffer - everything goes in. A source is a
meaningful unit marked explicitly from the buffer by the user (or proposed by nixx). Not every
buffer entry becomes a source. Documents, web pages, repos, and buffer sections are all sources.
Only sources feed into the recall index.

Three tiers: buffer → sources → memories.

```text
  IN-PROCESS · nixx chat  (unchanged)
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
  assembled by server per turn (unchanged)
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
  │  summary      text                         nixx-generated summary       │
  │  start_id     bigint          FK buffer    first buffer row (if buffer) │
  │  end_id       bigint          FK buffer    last buffer row  (if buffer) │
  │  created_at   timestamptz                  auto                         │
  └────────────────────────────────────┬────────────────────────────────────┘
                                       │  embed summary → store in memories
                                       │  raw buffer rows stay in buffer
                                       ▼
  memories · embedded recall index

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  column       type            constraint   notes                        │
  │  ──────────── ─────────────── ──────────── ──────────────────────────── │
  │  id           bigint          PK           auto-increment               │
  │  content      text                         source summary or doc chunk  │
  │  embedding    vector(1024)                 from mxbai-embed-large       │
  │  source_id    bigint          FK sources   which source this came from  │
  │  metadata     jsonb                        {chunk:} or other tags       │
  │  created_at   timestamptz                  auto                         │
  └─────────────────────────────────────────────────────────────────────────┘
       ▲  recalled per turn (cosine similarity search via pgvector)
       │  content = source summaries + document chunks  (not raw messages)
```

## Design principles

1. **API-first**: OpenAI compatibility enables frontend flexibility
2. **Local-first**: All data and inference on user's hardware
3. **Privacy-focused**: Encrypted storage, no external dependencies
4. **Modular**: Swappable components (vector DBs, LLM backends, frontends)
5. **Generalizable**: Config-driven for any user's setup
6. **Extensible**: Modify, add, improve

Nixx becomes the unified workflow, therefore all projects are nixx.

# Nixx architecture

## System diagram

Processes, modules, and communication paths as they exist today.

```text
                ┌─────────────────────────────────────────┐
                │  nixx chat · Python · Textual           │
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
                                    │  GET  /v1/buffer/session
                                    │  POST /v1/buffer/clear
                                    │  GET  /v1/episodic/status
                                    │  POST /v1/episodic/summary
                                    │  POST /v1/episodic/search
                                    │  GET  /v1/episodic/transcript
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  nixx serve · Python · Uvicorn + FastAPI                            │
│                                                                     │
│  ┌───────────────────────────┐    ┌──────────────────────────────┐  │
│  │ POST /v1/chat/completions │    │ prompts.py                   │  │
│  │ POST /v1/completions      │    │ SYSTEM_PROMPT                │  │
│  │ POST /v1/sources          │───▶│                              │  │
│  │ POST /v1/ingest           │    └──────────────────────────────┘  │
│  │ GET  /v1/sources          │                                      │
│  │ GET  /v1/sources/{id}     │                                      │
│  │ GET  /v1/sources/{id}/... │                                      │
│  │ GET  /v1/buffer/session   │                                      │
│  │ POST /v1/buffer/clear     │                                      │
│  │ GET  /v1/episodic/status  │                                      │
│  │ POST /v1/episodic/summary │                                      │
│  │ POST /v1/episodic/search  │                                      │
│  │ GET  /v1/episodic/trans.. │                                      │
│  │ GET  /v1/debug/context    │                                      │
│  │ GET  /health              │                                      │
│  └─────────────┬─────────────┘                                      │
│                │              ┌──────────────────────────────────┐  │
│                └─────────────▶│ MemoryStore                      │  │
│                               │ remember · recall · format_ctx   │  │
│                               │ create_source · ingest           │  │
│                               │ create_episode_summary           │  │
│                               │ recall_episodic · check_summary  │  │
│                               └──────────┬───────────────────┬───┘  │
│  ┌──────────────────────────┐            │ embed             │ SQL  │
│  │ OpenAIClient (chat)      │◀───┐      │           asyncpg │      │
│  │ chat_stream              │    │      │          pgvector │      │
│  └──────────────┬───────────┘    │      │                   │      │
│  ┌──────────────────────────┐    │      │                   │      │
│  │ OpenAIClient (embed)     │◀───┴──────┘                   │      │
│  │ embed                    │                                │      │
│  └──────────────┬───────────┘                                │      │
└─────────────────│────────────────────────────────────────────┬─│────┘
                  │ HTTP /v1/chat/completions                  │ │
                  │      /v1/embeddings                        │ │
                  │ (OpenAI-compatible protocol)               │ │
        ┌─────────┴──────────┐                                 │ │
        ▼                    ▼                                 ▼ ▼
  ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────────────────┐
  │ llama.cpp (remote) │ │ llama.cpp (local)  │ │ PostgreSQL + pgvector · 5432   │
  │ gpt.perdrizet.org  │ │ localhost:8082     │ │ (student-postgres container)   │
  │                    │ │                    │ │                                │
  │ gpt-oss-20b        │ │ mxbai-embed-large  │ │ Episodic:                      │
  │ chat + completions │ │ embeddings         │ │   buffer    (transcript + FTS) │
  └────────────────────┘ └────────────────────┘ │   summaries (embedded chunks)  │
                                                │ Semantic:                      │
                                                │   sources   (curated units)    │
                                                │   memories  (embedded chunks)  │
                                                └────────────────────────────────┘
```

---

## Memory model

Nixx has two separate memory systems, modeled after human cognition.

### Episodic memory (automatic, uncurated)

Everything that happens in conversation. Contains the full record of all exchanges,
searchable two ways:

- **Keyword search**: PostgreSQL full-text search (`tsvector` + GIN index) against the
  raw buffer. For "what was that class you mentioned" style queries where you remember
  a specific word or phrase.
- **Semantic search**: vector similarity search on LLM-generated summaries of conversation
  windows. For "what have we discussed about memory systems" style queries where you have
  a topic but not exact words.

Episodic memory is automatic. Every message goes into the buffer. Every N user messages
(configurable via `NIXX_SUMMARY_INTERVAL`, default 10), nixx prompts for tags, generates
a summary with entity extraction, embeds it, and stores it. The user can defer the prompt
with `/skip` or trigger one manually with `/summary`.

### Semantic memory (deliberate, curated)

Knowledge that has been explicitly selected for long-term retention. Two paths in:

- **`/source "name"`**: user marks a section of conversation as a named source. The
  transcript is chunked, each chunk is embedded, and the chunks are stored in the
  `memories` table for vector recall.
- **`/ingest`**: external documents (files, web pages) are read, chunked, summarized,
  embedded, and stored.

Semantic memory is deliberate. Nothing enters it automatically - the user decides what
to keep and how to label it.

### How recall works

On each chat turn, the server:
1. Embeds the user's latest message
2. Searches `memories` (semantic) by cosine similarity
3. Formats relevant hits as context injected into the system prompt
4. Sends the augmented prompt + conversation history to the LLM

Episodic search (`/search` in the TUI) queries both the buffer (full-text) and summaries
(vector) and returns combined results.

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
  │  system  ── SYSTEM_PROMPT + recalled memory bullets              │
  │  user    ── turn 1                                               │
  │  asst.   ── turn 1 response                                      │
  │  ...                                                             │
  │  user    ── current turn                                         │
  └────────────────────────────┬─────────────────────────────────────┘
                               │  sent to LLM backend · response written to buffer
                               ▼

  PERSISTENT · PostgreSQL
  ──────────────────────────────────────────────────────────────────────

  Two memory systems, four tables.

  ═══════════════════════════════════════════════════════════════════
  EPISODIC MEMORY · automatic, uncurated, everything goes in
  ═══════════════════════════════════════════════════════════════════

  buffer · persistent append-only transcript

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  column       type            constraint   notes                        │
  │  ──────────── ─────────────── ──────────── ──────────────────────────── │
  │  id           bigint          PK           auto-increment               │
  │  role         text                         user | assistant | marker    │
  │  content      text                         verbatim text                │
  │  origin       text                         tui | vscode | api           │
  │  tsv          tsvector                     generated, for full-text     │
  │  created_at   timestamptz                  auto                         │
  │                                                                         │
  │  Indexes: buffer_tsv_gin (GIN on tsv)                                   │
  └────────────────────────────────────┬────────────────────────────────────┘
                                       │
              every N user messages     │  LLM summarizes + extracts entities
              nixx prompts for tags     │  summary is embedded via mxbai-embed-large
              user can /skip or         │  tags provided by user
              trigger with /summary     │
                                       ▼
  summaries · embedded episodic summaries

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  column           type            constraint   notes                    │
  │  ──────────────── ─────────────── ──────────── ──────────────────────── │
  │  id               bigint          PK           auto-increment           │
  │  content          text                         LLM-generated summary    │
  │  embedding        vector(1024)                 from mxbai-embed-large   │
  │  tags             text[]                       user-provided tags       │
  │  entities         jsonb                        extracted named entities │
  │  start_buffer_id  bigint          FK buffer    first buffer row covered │
  │  end_buffer_id    bigint          FK buffer    last buffer row covered  │
  │  created_at       timestamptz                  auto                     │
  │                                                                         │
  │  Indexes: summaries_embedding_hnsw (HNSW on embedding)                  │
  └─────────────────────────────────────────────────────────────────────────┘
       ▲  episodic recall: vector search on summaries + full-text on buffer
       │  entities stored as: {tools: [...], people: [...], concepts: [...],
       │                       files: [...], urls: [...]}

  ═══════════════════════════════════════════════════════════════════
  SEMANTIC MEMORY · deliberate, curated, user-selected knowledge
  ═══════════════════════════════════════════════════════════════════

  sources · meaningful units marked by the user or ingested from documents

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
              /source "name"           │  each chunk → store in memories
              /ingest <file|url>       │  summary is for display only
                                       ▼
  memories · embedded semantic recall index

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  column       type            constraint   notes                        │
  │  ──────────── ─────────────── ──────────── ──────────────────────────── │
  │  id           bigint          PK           auto-increment               │
  │  content      text                         verbatim chunk or doc chunk  │
  │  embedding    vector(1024)                 from mxbai-embed-large       │
  │  source_id    bigint          FK sources   which source this came from  │
  │  metadata     jsonb                        {chunk: i, total_chunks: N}  │
  │  created_at   timestamptz                  auto                         │
  │                                                                         │
  │  Indexes: memories_embedding_hnsw (HNSW on embedding)                   │
  └─────────────────────────────────────────────────────────────────────────┘
       ▲  semantic recall: cosine similarity search via pgvector
       │  recalled per turn and injected into the system prompt
```

## Design principles

1. **API-first**: OpenAI compatibility enables frontend flexibility
2. **Local-first**: All data and inference on user's hardware
3. **Privacy-focused**: Encrypted storage, no external dependencies
4. **Modular**: Swappable components (vector DBs, LLM backends, frontends)
5. **Generalizable**: Config-driven for any user's setup
6. **Extensible**: Modify, add, improve

Nixx becomes the unified workflow, therefore all projects are nixx.

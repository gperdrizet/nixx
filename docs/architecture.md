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
                                    │  GET  /v1/buffer/session
                                    │  POST /v1/buffer/clear
                                    │  GET  /v1/episodic/status
                                    │  POST /v1/episodic/config
                                    │  POST /v1/episodic/summary
                                    │  POST /v1/episodic/search
                                    │  GET  /v1/episodic/transcript
                                    │  GET  /v1/intent
                                    │  POST /v1/intent
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  nixx serve · Python · Uvicorn + FastAPI                            │
│                                                                     │
│  ┌───────────────────────────┐    ┌──────────────────────────────┐  │
│  │ POST /v1/chat/completions │    │ prompts.py                   │  │
│  │ POST /v1/ingest           │    │ SYSTEM_PROMPT                │  │
│  │ POST /v1/sources          │───▶│                              │  │
│  │ GET  /v1/sources          │    └──────────────────────────────┘  │
│  │ GET  /v1/sources/{id}     │                                      │
│  │ GET  /v1/sources/{id}/... │    ┌──────────────────────────────┐  │
│  │ GET  /v1/buffer/session   │    │ ToolRegistry                 │  │
│  │ POST /v1/buffer/clear     │    │ read_file · write_file       │  │
│  │ GET  /v1/episodic/status  │    │ list_dir · delete_file       │  │
│  │ POST /v1/episodic/config  │    │ search_transcript            │  │
│  │ POST /v1/episodic/summary │    │ view_transcript              │  │
│  │ POST /v1/episodic/search  │    └──────────────────────────────┘  │
│  │ GET  /v1/episodic/trans.. │                                      │
│  │ GET  /v1/episodic/summ..  │                                      │
│  │ GET  /v1/intent           │                                      │
│  │ POST /v1/intent           │                                      │
│  │ DELETE /v1/intent         │                                      │
│  │ POST /v1/intent/derive    │                                      │
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
│  │ OpenAIClient (chat)      │◀───┐       │           asyncpg │      │
│  │ chat_stream              │    │       │          pgvector │      │
│  └──────────────┬───────────┘    │       │                   │      │
│  ┌──────────────────────────┐    │       │                   │      │
│  │ OpenAIClient (embed)     │◀───┴───────┘                   │      │
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

Episodic memory is automatic. Every message goes into the buffer. When the amount of
unsummarized buffer content reaches N words (configurable via `NIXX_SUMMARY_INTERVAL`,
default 1000), nixx prompts for tags, generates a summary with entity extraction, embeds
it, and stores it. The user can trigger a summary manually at any time with `/summary`.

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
2. Searches `summaries` (episodic) by cosine similarity, filtering hits below a 0.4 threshold
3. Formats relevant summaries as a context block injected into the system prompt
4. Appends the current intent block (if set) to the system prompt
5. Sends the augmented prompt + conversation history to the LLM

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
  │  system  ── SYSTEM_PROMPT + ## Current Intent + recalled summaries│
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
              when unsummarized word   │  LLM summarizes + extracts entities
              count reaches N words    │  summary is embedded via mxbai-embed-large
              (NIXX_SUMMARY_INTERVAL)  │  tags provided by user
              or /summary at any time  │
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
  │  type         text                         buffer|document|repo|web     │
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
       │  queryable via /v1/sources endpoints (not auto-injected per turn)
```

## Intent system

Nixx tracks the user's current work intent as a persistent string, stored in server
process state. Intent is:

- Derived automatically every N messages (`NIXX_INTENT_INTERVAL`, default 10), by asking
  the LLM to infer what the user is working on from the last N messages
  (`NIXX_INTENT_LOOKBACK`, default 10)
- Settable and clearable manually via `POST /v1/intent` and `DELETE /v1/intent`
- Injected into every system prompt as a `## Current Intent` block

This makes nixx context-aware across context window boundaries - the intent string
persists even when the conversation history is truncated.

---

## TUI slash commands

All slash commands are processed locally before sending to the LLM:

| Command | Description |
|---------|--------------|
| `/help` | List available commands |
| `/context` | Show current system prompt context (recalled summaries + intent) |
| `/summary` | Trigger an episodic summary immediately |
| `/search [query]` | Search the episodic buffer and summaries |
| `/transcript <id> [end_id]` | View buffer entries from id to end_id |
| `/clear` | Clear the in-process conversation history |
| `/interval [n]` | Show or set the summary word-count threshold |
| `/recall` | Toggle episodic recall injection on/off |
| `/intent [text]` | Show or set the current intent string |

---

## Design principles

1. **API-first**: OpenAI compatibility enables frontend flexibility
2. **Local-first**: All data and inference on user's hardware
3. **Privacy-focused**: No external dependencies, all data stays on your hardware
4. **Modular**: Swappable components (vector DBs, LLM backends, frontends)
5. **Generalizable**: Config-driven for any user's setup
6. **Extensible**: Modify, add, improve

Nixx becomes the unified workflow, therefore all projects are nixx.

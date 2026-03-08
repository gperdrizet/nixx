# Build log: March 7, 2026 - TUI and memory architecture

## What got built

A lot happened today. Started with the server already running from a previous session
and ended with a working chat TUI, a three-tier persistent memory system, and a live
end-to-end test of the full pipeline.

### TUI: nixx chat

Built the initial `nixx chat` terminal UI using Textual. Key pieces:

- `NixxApp` with `ScrollableContainer` for messages and an `Input` widget at the bottom
- `Message` widget with three CSS classes: `user`, `assistant`, `system`
- Streaming token-by-token display via `_stream_response` worker + SSE parsing
- `_history` list maintained in-process for the context window

Two bugs found and fixed immediately on first launch:

1. **Kitty keyboard protocol** - VS Code's integrated terminal sends space as `\x1b[32u`.
   Textual parses this as `key='space'` but `character=None`, so `Input._on_key` drops it
   as non-printable. Fixed with an `on_key` interceptor that calls `insert_text_at_cursor(" ")`.

2. **Identity confusion** - the assistant was calling itself "George" because old smoke-test
   memories had no role separation. Fixed by introducing `src/nixx/prompts.py` with a
   `SYSTEM_PROMPT` constant always prepended as a system message.

### Slash commands

- `/context` - shows recall hits with similarity scores, bar visualization, and injected context block
- `/source "name"` - crystallizes a buffer range into a source (see below)

### Memory architecture redesign

Replaced the flat `memories` bag with a three-tier model designed around the idea of a
"tape recorder" - everything goes into the buffer, but only explicitly marked sections
become sources that feed memory.

**Previous schema**: single `memories` table, raw messages written directly, no role column,
no grouping. Only written on non-streaming path (bug: TUI turns never persisted).

**New schema**:

```
buffer   - append-only tape of all messages, streaming and non-streaming
           role, content, origin (tui|vscode|api), created_at

sources  - named, meaning-bounded units explicitly extracted from buffer
           (or ingested directly for documents/repos/web)
           name, type, summary, start_id, end_id, created_at

memories - embedded recall index
           content (source summary or document chunk), embedding (1024d),
           source_id FK, metadata, created_at
```

The key design insight: eliminate "session" as a concept entirely. The buffer is always
running. A source is not time-bounded - it's bounded by decision, milestone, or meaning.
Not everything in the buffer needs to become a source.

Naming went through a few iterations: topic → clip → source. "Source" won because it
unifies the concept - buffer sections, documents, web pages, and repos are all sources
that feed memory. The word makes the tiered structure legible.

The `/source "name"` command triggers the pipeline:
1. Finds the buffer range since the last source
2. Fetches those rows and builds a transcript
3. Asks the LLM to summarise in 3-5 sentences
4. Writes a row to `sources`
5. Embeds the summary and writes it to `memories`

Streaming persistence bug also fixed - both the streaming and non-streaming paths now
write user + assistant turns to `buffer` after each exchange.

### API additions

- `POST /v1/sources` - create a source from a buffer range (or with explicit start/end IDs)
- `GET /v1/debug/context` - now returns base prompt, injected context block, and raw recall hits with scores

### Schema migration

`init_schema` applies migrations inline on startup - adds `source_id` to `memories`,
drops the old `source TEXT` column if present. Old `conversations` and `messages` tables
left in place (empty, harmless).

### Docs

- `docs/architecture/README.md` - added "planned architecture" diagram showing the
  buffer → sources → memories tiers, with full column listings for each table
- `docs/stack.md` - new file, full tech stack reference with summaries and documentation
  links for every library in the project
- `docs/index.md` - updated tech stack section to link to stack.md

## What was discussed but deferred

- **Projects** - grouping sources and memories into named projects with a graph component.
  Decision: design after using the buffer+sources system for a few weeks against real data.
- **Verbose mode** - toggle that exposes recall hits, tool use, and agent selection inline
  during conversation (not just on `/context`)
- **Edit / Rewind** - mutate `_history` and re-run from a specific message
- **Clear** - clear displayed messages without clearing `_history` or buffer
- **Copy** - copy last assistant output to clipboard
- **Personality work** - editing `SYSTEM_PROMPT` for tone and identity. The first-person
  confusion from recalled summaries noted as a specific issue to fix.

## End state

- 36/36 tests passing, ruff clean, mypy clean
- Live smoke test confirmed: buffer writes on both paths, `/v1/sources` full pipeline
  (buffer range → LLM summary → embed → index), recall working across fresh sessions
- `ctrl+p` intercepted by VS Code - Textual command palette needs `ctrl+\` instead

---

## Part 2: knowledge ingestion and handler registry

### What got built

#### nixx ingest

A full knowledge ingestion pipeline added as `src/nixx/ingest/`:

- `nixx ingest <path|url> [--name "label"]` CLI subcommand
- `POST /v1/ingest` endpoint
- `reader.py` → read local files or fetch URLs, strip HTML via BeautifulSoup
- `chunker.py` → split text into overlapping chunks at paragraph boundaries
- `pipeline.py` → `IngestPipeline`: read → chunk → summarize → save source → embed each chunk → index

Default chunk size set to 800 chars after hitting `mxbai-embed-large`'s 512-token context
limit on code-heavy pages (HTML-stripped docs produce ~1 char/token, so 1500 chars failed).

Live tested:
- `nixx ingest README.md --name "nixx README"` → 6 chunks, source_id 3
- `nixx ingest https://docs.pydantic.dev/latest/concepts/pydantic_settings/ --name "pydantic-settings docs"` → 119 chunks, source_id 6

Ingested sources land in the same `memories` table as conversation summaries. Recall
treats both identically.

#### Handler registry

Refactored ingest to use a handler registry (`src/nixx/ingest/handlers/`):

- `IngestHandler` ABC: `name`, `can_handle(source)`, `read(source)`, `chunk(text)`
- `WebHandler` - matches `http://`/`https://`, strips HTML
- `FileHandler` - fallback, matches anything without `://`
- `HandlerRegistry` - ordered list, first match wins; plugin auto-discovery via `importlib`

To add a new ingest type, drop a `.py` file in `~/.config/nixx/handlers/` (configurable
as `NIXX_HANDLERS_DIR`). Any `IngestHandler` subclass found there is loaded at server
startup and takes priority over built-ins. No config changes or code edits required.

`reader.py` kept as a backward-compat shim that delegates to the default registry.

#### Commits

- `428c14d` - Add knowledge ingestion: nixx ingest <file|url>
- `de3904c` - Fix chunk size: 1500→800 chars
- `6a3c496` - Refactor ingest: handler registry with plugin auto-discovery

53 tests passing, ruff/mypy clean.

### What was discussed and planned

**Next features, in order:**

1. **Source editor** - `nixx sources` CLI: list all sources with id/name/type/date,
   `nixx sources rm <id>` to delete a source and all its memory embeddings. Needed soon -
   data already accumulating that we may want to clean up.

2. **Phone access** - API is already OpenAI-compatible. Expose via Tailscale, pick a
   mobile client that supports custom endpoints (Enchanted, Open WebUI mobile, etc.).
   Voice needs Whisper (STT) + TTS step on response, both available via Ollama.

3. **File creation/editing** - sandboxed to a `workspace_dir` (default `~/nixx-workspace/`).
   Requires adding a `tools` block to the chat endpoint for structured tool calls.

**Longer-horizon: self-modification**

The plugin handler system built today is the right mental model - "drop a file in a
directory, behavior changes." The full loop for nixx writing her own ingest handlers would
be: workspace write → handler file → registry reload endpoint (trivial) → self-test.
The hard constraint is model reasoning quality on novel Python at 7B scale.

Path: workspace → tool-calling → handler reload → self-test. Each step builds on the last.

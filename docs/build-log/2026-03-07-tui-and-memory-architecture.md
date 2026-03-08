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

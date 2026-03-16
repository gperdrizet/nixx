# Build log: March 15, 2026 - Episodic memory and identity

## What got built

Major session: implemented the episodic memory system, gave nixx a personality, and
fixed several bugs found during live testing. 977 lines added across 11 files.

### Episodic memory system

Redesigned the memory model from a single tier (sources → memories) into two distinct
systems:

- **Episodic** (automatic): buffer is the transcript. Periodic LLM-generated summaries
  with vector embeddings, user-provided tags, and entity extraction. Full-text search
  on buffer via tsvector + GIN. Vector search on summary embeddings via HNSW.
- **Semantic** (deliberate): unchanged. User-curated sources and ingested documents,
  chunked and embedded for vector recall.

New `summaries` table: content, embedding (1024d), tags (text[]), entities (jsonb),
start/end buffer IDs, created_at. Foreign keys to buffer with ON DELETE SET NULL.

### Summary creation flow

Every N words of conversation (default 500, configurable at runtime), the TUI prompts
for tags. The LLM summarizes the unsummarized buffer range and extracts key entities
in a single call. The summary is embedded and stored for vector recall.

Initially used message count (10 messages) as the trigger, but switched to word count
after observing that long LLM responses contain more content than many short exchanges.

Entity extraction prompt went through several iterations: started too broad (dumping
every noun), tightened to "specific names and important topics, up to 5 per category
in order of importance." Dropped the "concepts" category (renamed to "topics") and
added instructions to omit empty categories. Summary style changed from meta-description
("The discussion focused on...") to direct content notes ("PostgreSQL supports...").

### Prompt injection swap

Chat completions now inject the top 3 episodic summaries (similarity >= 0.4) instead
of semantic memories. Summaries are formatted with explicit instructions that the LLM
may ignore them if not relevant.

### System prompt and identity

Gave nixx a personality via a rewritten system prompt:

- Sharp, direct, dry humor. Quietly passionate about hard problems.
- First person. No hedging, no filler, no "great question."
- Has opinions, voices them once, then follows the user's lead.
- Framed as a post-doc in the user's research operation.
- Personality references: Acid Burn (Hackers), Leeloo (5th Element), Seven of Nine (Voyager).

Added an explicit "honesty and limits" section to combat hallucination: don't fabricate,
don't offer capabilities you lack (no web browsing, no code execution, no file access),
don't invent citations or URLs.

### Separate tag input widget

Found and fixed a race condition where the summary tag prompt could hijack user messages.
The `_awaiting_tags` flag on the main input created a window where a message being typed
would get consumed as tags. Replaced with a dedicated `#tag-input` widget that appears
below the message area with a yellow warning border. The chat input is never intercepted.
Empty Enter on the tag input defers the summary (replacing `/skip`).

### New TUI commands

- `/summary` - manually trigger summary creation
- `/summaries` - list all episodic summaries with tags, entities, buffer range
- `/search "query"` - search episodic memory (vector on summaries + full-text on buffer)
- `/clear` - clear conversation (same as Ctrl+L)
- `/interval [words]` - show or set the summary word-count threshold at runtime

### New server endpoints

- `GET /v1/episodic/status` - check if summary is due
- `POST /v1/episodic/summary` - create a summary with tags
- `POST /v1/episodic/search` - search episodic memory
- `GET /v1/episodic/transcript` - fetch buffer entries by range
- `GET /v1/episodic/summaries` - list all summaries
- `POST /v1/episodic/config` - update summary interval at runtime

### Bug fixes

- **Foreign key violation on summary creation**: `start_buffer_id` referenced a
  theoretical ID that no longer existed in the buffer. Fixed by using actual IDs
  from fetched entries.
- **Summary prompt re-appearing after /skip**: no tracking of skip state. Added
  word-count-based deferral that waits for interval more words before re-prompting.
- **Entities returned as JSON string**: asyncpg returns JSONB as string, TUI
  was calling `.items()` on it. Added `json.loads()` parsing.

### Documentation

- `docs/architecture.md` - complete rewrite with new system diagram, episodic/semantic
  memory model section, four-table data diagram
- `docs/stack.md` - PostgreSQL section updated with four tables across two systems
- `.github/copilot-instructions.md` - architecture diagram updated
- `docs/index.md` - "three-tier" → "two-tier"

## What was discussed but deferred

- **Text wrapping in TUI input** - would require swapping Input for TextArea, significant
  refactor. Lumped with spell checking as TUI "nice-to-haves."
- **Spell checking** - terminal limitation, no good solution without GUI frontend.
- **Model upgrade** - discussed options for the P100 (16 GB). Mistral Small 22B or
  Qwen 2.5 32B with partial offload are candidates. Deferred to a future session.

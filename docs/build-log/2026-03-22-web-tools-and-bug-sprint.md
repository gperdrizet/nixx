# Build log: March 22, 2026 - web tools and bug sprint

## What got built

Two sessions today. The first added new features; the second was almost entirely debugging
the things the first session broke.

### Web search and read_webpage tools

Added two new LLM-callable tools:

- `web_search` - queries DuckDuckGo via the HTML endpoint (`https://html.duckduckgo.com/html/`),
  scrapes results with BeautifulSoup, returns top 5 with title/URL/snippet.
- `read_webpage` - fetches a URL via httpx, strips HTML with BeautifulSoup, truncates at
  8000 chars. Used as a follow-up to `web_search` when nixx needs to read a full page.

Both live in `src/nixx/tools/` and are registered in `ToolRegistry` at startup. System
prompt updated to mention `web_search`.

### Recall similarity threshold

Added `recall_threshold: float = 0.4` to `NixxConfig` (env: `NIXX_RECALL_THRESHOLD`).
Wired through `recall_episodic_for_prompt()` so low-relevance memories don't get injected
into the context. Exposed via `GET /v1/episodic/status` and configurable at runtime via
`POST /v1/episodic/config`. TUI `/threshold [0.0-1.0]` command to view or set it live.

### Intent bar

Added `IntentBar(Static)` widget to the TUI, sitting between the status row and the tag row.
Shows the current intent string. Toggled with `/intent-bar`. Initially hidden; appears after
a response when an intent is set.

### Context length auto-fetch

Server lifespan now fetches `/props` from the LLM server at startup and sets
`config.llm_context_length` from the returned `n_ctx`. Means the token budget is always
correct even if `.env` has an outdated value.

---

## What broke and how it was fixed

### Context length still showing 8192 after restart

The auto-fetch code ran but the JSON path was wrong. The `/props` response has `n_ctx` nested
under `default_generation_settings`, not at the top level. `json.get("n_ctx")` always returned
`None`, so the fallback value (8192 from `.env`) was used.

Fix: `.get("default_generation_settings", {}).get("n_ctx")`.

Also switched from `logger.info()` to `print(flush=True)` for the startup message - Python
logging wasn't piped to journald at INFO level, so the fetch result was invisible. The print
now appears in the logs between "Waiting for application startup" and "Application startup
complete", confirming the fetch ran and what value it set.

Added `context_length` to the `GET /health` response so it's easy to verify the runtime value
without making a chat request.

### Intent bar not showing on startup

`on_mount` never called `_fetch_and_show_intent_bar()`. The widget existed but never got
populated until after the first chat response. Added the call to `on_mount` alongside the
existing `_restore_session` and `_update_summary_bar` workers.

### Blank "Error:" on failed chat

Exception paths in both server and TUI used `str(exc)` directly. Some exception types
(e.g. bare `StopAsyncIteration`, certain httpx internals) produce an empty string. Both
sides now fall back to `f"({type(exc).__name__})"` when `str(exc)` is empty, so the error
message is always non-blank. The in-stream error chunk handler in the TUI also got the same
treatment - it now extracts `message` with a fallback to `type` or `repr(err)`.

### Tool call flag in chat

The server executed tools silently with no client-visible signal. Added a `tool_call` SSE
event emitted immediately before each tool execution:

```json
{"tool_call": {"name": "web_search"}}
```

The TUI handles it by adding a dim system message: `calling tool: web_search`. The message
appears inline in the chat flow between the user turn and the eventual assistant response.

---

## State at end of session

- Server running, context length confirmed 32768 at startup.
- All 42 tests still passing (no new tests added today - tool stubs and threshold tests
  are a reasonable next task).
- `NIXX_LLM_CONTEXT_LENGTH=8192` in `.env` is now a no-op - the startup fetch overrides it.
  Could be removed from `.env` to avoid confusion, but leaving it as a fallback value is fine.

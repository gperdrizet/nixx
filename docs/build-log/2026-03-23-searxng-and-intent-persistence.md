# Build log: March 23, 2026 - SearXNG and intent persistence

## What got built

Two main areas: replacing the broken DuckDuckGo web search with a self-hosted SearXNG
instance, and making intent persist across server restarts.

### SearXNG web search

The DuckDuckGo HTML scraper (`web_search.py`) stopped working - DDG was returning a CAPTCHA
challenge page (`cc=botnet`) to this server's IP. The `.result` CSS selector found nothing
in the challenge HTML, so every search silently returned no results.

Replaced with a local SearXNG container:

- `services/searxng/docker-compose.yml` - binds port 8888 on localhost only
- `services/searxng/settings.yml` - enables google, brave, bing, duckduckgo, wikipedia;
  disables image/news/video engines; sets `search.formats: [html, json]`
- `services/searxng/limiter.toml` - disables bot detection for local use

Config: `NIXX_SEARXNG_URL` (default `http://localhost:8888`) in `NixxConfig`. Passed through
`ToolRegistry` to `WebSearchTool`.

`web_search.py` was rewritten to use the SearXNG JSON API
(`GET /search?q=...&format=json`). Adding `X-Forwarded-For: 127.0.0.1` is required - the
SearXNG botdetection middleware blocks requests that don't appear to come from localhost.

Verified end-to-end: "search for the latest Minecraft update" returns real results.

#### Debugging notes (recorded for future reference)

The fix took several iterations. Key lessons:

- `format=json` returns 403 unless explicitly opt-in in `settings.yml` under
  `search.formats`. The default only enables HTML.
- `docker compose restart` does not re-read the bind-mounted settings file if the file was
  replaced on disk (inode changed). Use `down && up -d` to force re-mount.
- The running editor (VS Code) caches file contents - `read_file` showed a stale version of
  `settings.yml` that differed from what was actually on disk. Always verify with `cat` or
  `sed` when something seems inconsistent.
- A fresh `async with httpx.AsyncClient(...)` per request doesn't carry cookies, but
  SearXNG does not require cookie pre-seeding - the `X-Forwarded-For` header alone is
  sufficient once `pass_ip` is configured.

### Intent persistence

Intent was stored only in `app.state` and lost on every server restart. Two problems: cold
starts had no intent at all, and iteratively refined intent was discarded on restart.

**`state` table** added to the DB schema (key/value, `TEXT PRIMARY KEY`). Two helpers in
`db.py`: `get_state(pool, key)` and `set_state(pool, key, value)`. The old `kv_store` name
was briefly used but immediately renamed to `state` - `kv_store` reads too much like the
attention KV cache. An inline migration in `init_schema` copies any existing `kv_store` rows
and drops the table.

**`DEFAULT_INTENT`** constant added to `server.py`:
`"Understand the user's goals and assist them."` Used as the starting intent on cold starts
(nothing in `state`) and when the user clears intent via `DELETE /v1/intent`.

On startup, intent is loaded from `state` (key `intent`) with fallback to `DEFAULT_INTENT`.
Every write path persists to `state`: `set_intent`, `clear_intent`, and `_derive_intent`.

**`intent_interval`** default changed from 10 to 5 to make derivation observable in normal
use.

---

## What broke and how it was fixed

### Old DDG code left in web_search.py

The rewrite prepended the new SearXNG class, but the old DDG implementation was still
appended below it in the same file. Python uses the last definition when a class has
duplicate methods, so all search calls were hitting the old broken DDG code (which still
referenced the deleted `_HEADERS` constant, causing `NameError`).

Fix: truncated the file to the first 85 lines, removing the old class entirely.

---

## State at end of session

- SearXNG container running at port 8888, `restart: unless-stopped`.
- `web_search` tool working via SearXNG JSON API.
- Intent persisted in `state` table; survives restarts; defaults to generic string on cold
  start.
- All uncommitted changes: `config.py`, `memory/db.py`, `server.py` (intent persistence
  + `state` table + `intent_interval=5`). The SearXNG commit was pushed; the intent work
  is not yet committed.

# Quickstart guide

Everything you need to start using nixx from a cold boot.

---

## Prerequisites

All services must be running before you open the TUI. Check with:

```bash
systemctl status nixx.target nixx-server nixx-embed nixx-pgweb llamacpp
```

If anything is stopped, start the full stack:

```bash
sudo systemctl start llamacpp nixx.target
```

`llamacpp` is intentionally outside `nixx.target` because it is managed separately
(see [infrastructure.md](infrastructure.md)). Allow ~30-60 seconds for the LLM server
to load the model before the first inference request.

---

## Service map

| Service | What it does | Port | Managed by |
|---|---|---|---|
| `llamacpp.service` | LLM inference (gpt-oss-20b) | 8502 | systemd, manual start |
| `nixx-embed.service` | Embeddings (mxbai-embed-large) | 8082 | `nixx.target` |
| `nixx-server.service` | nixx API server (FastAPI) | 8000 | `nixx.target` |
| `nixx-pgweb.service` | DB browser | 8081 | `nixx.target` |
| PostgreSQL | Database | 5432 | system service (auto) |
| SearXNG | Web search | 8888 | Docker, manual |

SearXNG is optional (enables the `web_search` tool). Start it when needed:

```bash
cd ~/nixx/services/searxng && docker compose up -d
```

---

## Open the TUI

```bash
nixx chat
```

The TUI connects to the nixx API server at `http://localhost:8000`. If the server is not
yet running, the TUI will fail immediately with a connection error - start the services first.

---

## TUI layout

```
┌─────────────────────────────────────────────────┐
│  conversation history                           │
│                                                 │
│  You: ...                                       │
│  nixx: ...                                      │
│                                                 │
├─────────────────────────────────────────────────┤
│  context ████░░░░ 18% (11942/65536 tok)         │
│  summary ████░░░░ 45% (447/1000 wds)            │
│  intent: understand the architecture            │
│  [recall: on] [intent: on]                      │
├─────────────────────────────────────────────────┤
│  > input                                        │
└─────────────────────────────────────────────────┘
```

- **context bar** - tokens used of the LLM context window
- **summary bar** - words accumulated since the last episodic summary
- **intent bar** - the current auto-derived intent
- **recall / intent toggles** - `Ctrl+R` toggles episodic recall, `Ctrl+I` toggles intent injection

Input: `Enter` sends, `Shift+Enter` inserts a newline.

---

## Slash commands

| Command | What it does |
|---|---|
| `/help` | Print all commands |
| `/clear` | Start a new session (writes a session boundary to the DB) |
| `/summary` | Trigger an episodic summary now |
| `/search "query"` | Search episodic memory (full-text + vector) |
| `/transcript <id> [end]` | Show raw buffer entries |
| `/context` | Show the assembled system prompt + current recall hits |
| `/recall` | Toggle episodic recall on/off |
| `/interval [n]` | View or set the summary word interval |
| `/threshold [0.0-1.0]` | View or set the recall similarity threshold |
| `/intent [text]` | View or set the current intent |
| `/intent-toggle` | Toggle intent injection on/off |
| `/intent-bar` | Toggle the intent bar visibility |
| `/project [dir\|clear]` | Show, set, or clear the project directory |

---

## Memory

### Episodic (automatic)

Every message is saved to the `buffer` table. When the word count since the last summary
reaches the interval (default 1000 words), nixx automatically generates a summary - no
user action needed. The summary is tagged, entity-extracted, and embedded for future recall.

On each turn, your message is embedded and the most relevant past summaries are injected
into the system prompt silently. Use `/context` to see exactly what was injected.

### Semantic (deliberate)

Two ways to build long-term knowledge:

**Mark a conversation section** as a named source via the API:
```bash
curl -s http://localhost:8000/v1/sources \
  -H "Content-Type: application/json" \
  -d '{"name": "my-source", "start_id": 10, "end_id": 50}'
```

**Ingest an external file or URL**:
```bash
curl -s http://localhost:8000/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"source": "/home/siderealyear/some-notes.md"}'

curl -s http://localhost:8000/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"source": "https://example.com/article"}'
```

---

## File tools

nixx can read and write files within the scratch directory (`~/nixx_scratch`, always
accessible) and an optional project directory.

nixx cannot create directories outside scratch - you create the directory first, then
grant access:

```bash
mkdir ~/atlasforge
```

Then in the TUI:

```
/project /home/siderealyear/atlasforge
```

Once set, nixx can use `read_file`, `write_file`, `edit_file`, `list_dir`, and
`delete_file` tools within both directories. It can also create subdirectories inside
them as a side effect of writing files. Use `/project` with no argument to see the
current project directory, or `/project clear` to unset it.

---

## Checking server health

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Returns `{status, model, context_length}`. If the LLM server is unreachable, `status`
will reflect that. You can also check what's currently loaded in the context:

```bash
curl -s http://localhost:8000/v1/debug/context | python3 -m json.tool
```

---

## Stopping everything

```bash
sudo systemctl stop nixx.target
sudo systemctl stop llamacpp
```

SearXNG (if running): `cd ~/nixx/services/searxng && docker compose down`

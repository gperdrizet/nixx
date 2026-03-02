# Build Log - March 2, 2026: first working server

## Today's work

Got nixx talking. The Phase I API server is running and responding to real requests.

### What got built

Started the session by bootstrapping the workspace instructions and thinking through
the development environment — devcontainer (no), branching strategy (flat, feature
branches off main), and the production stack shape.

**Database decision**: Already run a production-grade PostgreSQL instance on this
machine for students. Rather than spin up a second one, provisioned a `nixx` role
and database inside the existing server. Three-tier database strategy:

- Tier 1 (default): SQLite — zero config, just works for anyone trying nixx
- Tier 2: docker-compose with bundled PostgreSQL on port 5433
- Tier 3 (my setup): bring-your-own PostgreSQL via `DATABASE_URL`

SQLAlchemy handles all three transparently. No database-specific code paths.

**Deployment scaffolding**:

- `.env.example` with all config fields documented
- `docker-compose.yml` for the self-contained stack (postgres + chromadb + nixx-server),
  `network_mode: host` so the container can reach Ollama on localhost
- `scripts/init-db.sh` reads password from `.env` — no credentials in version control

**API server** (`src/nixx/server.py`):

- `/health`
- `/v1/chat/completions` — blocking and streaming (SSE)
- `/v1/completions` — blocking and streaming (SSE)
- OpenAI-compatible response envelopes throughout

**Ollama client** (`src/nixx/llm/client.py`):

- Async over httpx
- `chat` / `chat_stream` and `generate` / `generate_stream`
- Thin wrapper — just translates Ollama's format to/from the server layer

### Bumps

- `requires-python = ">=3.13"` but Python 3.12 is what's actually installed.
  Nothing in the codebase needs 3.13, so dropped the requirement. Easy fix, annoying
  to hit first.

- FastAPI rejects `StreamingResponse | dict[str, Any]` as a return type annotation
  for response model generation. Fixed with `response_model=None` on both routes.

- `NixxConfig` (pydantic-settings) reads the entire `.env` file and rejects unknown
  fields. `NIXX_POSTGRES_PASSWORD` wasn't a declared field — added it.

- Ollama wasn't installed (`command not found`). Used the official installer
  (`curl -fsSL https://ollama.com/install.sh | sh`) which detected CUDA and set up
  the systemd service automatically. Updated README with this as step 1. Service is
  not enabled - start it when needed for now.

### First real response

```text
$ curl -X POST http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"messages": [{"role": "user", "content": "hello"}]}'

{"id":"chatcmpl-cd1aae...","object":"chat.completion","created":1772470557,
"model":"qwen2.5-coder:7b","choices":[{"index":0,"message":{"role":"assistant",
"content":"Hello! How can I assist you today?"},"finish_reason":"stop"}],
"usage":{"prompt_tokens":30,"completion_tokens":10,"total_tokens":40}}
```

Valid OpenAI-compatible JSON. Any editor using Continue.dev can point at
`http://localhost:8000` and it'll just work.

## Next Steps

1. `nixx.cli` — minimal entry point (start server, check status)
2. Tests — smoke tests for server and config at minimum
3. Pre-commit hooks + CI workflow (ruff, mypy, pytest on push)
4. Memory system — ChromaDB integration, conversation persistence

## Thoughts

The three-tier database strategy was worth thinking through carefully. The default
SQLite path means anyone can clone and run without touching a database config.
The bring-your-own path means I don't have to run redundant infrastructure.
The docker-compose path gives the middle ground. One `DATABASE_URL` env var
covers all three — clean.

The server being OpenAI-compatible from day one is already paying off. No custom
client code needed to test it — just curl. The memory layer will come later, but
having requests flowing through nixx's own endpoint is the right foundation.

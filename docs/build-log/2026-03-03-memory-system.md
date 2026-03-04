# Build log: March 3, 2026 - memory system

## What got built

Second full session. Picked up from a working API server and got the core memory system running end-to-end, plus test infrastructure and CI.

### Test suite (32 tests)

Wrote tests before finishing the memory system - enforced discipline early. Four test modules:

- `test_config.py` - NixxConfig reads defaults, env vars override, directories are created
- `test_llm_client.py` - Payload building (pure unit tests, no network), `chat()` / `generate()` via `respx` mock
- `test_server.py` - FastAPI endpoints via `httpx.ASGITransport` (no real server), Ollama mocked out
- `test_cli.py` - Argument parser, `_serve` / `_status` with mocked uvicorn and httpx

Key fixture pattern: `monkeypatch.chdir(tmp_path)` on every test that touches `NixxConfig` - the constructor creates directories relative to cwd, so without this tests pollute the project directory.

### CI and pre-commit

`.github/workflows/ci.yml` - two jobs (lint+typecheck, pytest) on push/PR to main.
`.pre-commit-config.yaml` - ruff (with `--fix`), black, mypy on every commit.

Lesson learned: when pre-commit auto-fixes a file, the staged version is stale. Need to `git add` again before re-committing. Pre-commit prints the filename - `git diff <file>` shows exactly what changed before re-staging.

### CLI

`src/nixx/cli.py` - minimal but complete. Two subcommands:

- `nixx serve` - starts the API server (thin wrapper over uvicorn)
- `nixx status` - hits `/health` and prints a rich table

This makes the `nixx` entry point in `pyproject.toml` actually work.

### Memory system

The big one. Stack: pgvector on the existing `student-postgres` container + Ollama embeddings.

**Why pgvector over ChromaDB**: Already have Postgres running, no second service, SQL joins, ACID transactions. ChromaDB is fine for demos but gets swapped out later anyway.

**Why Ollama embeddings**: Zero new dependencies, same GPU-local service already running for inference. Model: `mxbai-embed-large` (1024d, BERT-large based, strong retrieval quality).

**pgvector setup**: `postgres:16-alpine` doesn't include pgvector. Swapped the `fullstack-sql` compose file to `pgvector/pgvector:pg16` (drop-in replacement), recreated the container, data volume survived. Enabled extension with `CREATE EXTENSION IF NOT EXISTS vector;` in the nixx database.

**Schema** (`src/nixx/memory/db.py`):
- `conversations` - session container
- `messages` - individual turns
- `memories` - content + 1024-d embedding vector, HNSW index for cosine similarity search

**`MemoryStore`** (`src/nixx/memory/store.py`): thin layer combining OllamaClient and db. `remember(content)` → embed → save. `recall(query)` → embed → cosine search → top-5. `format_context(memories)` → formats results above similarity threshold as a system prompt block.

**Server integration**: on each `/v1/chat/completions`:
1. Embed the user message
2. Retrieve top-5 similar memories
3. Inject as context block in system prompt (if above threshold)
4. Call Ollama
5. Save user message + response to memories

Memory errors are caught and logged - a failed recall or save doesn't break the response.

**Connection pool**: asyncpg with `register_vector` on each connection. Pool is created in the FastAPI lifespan and stored in `app.state.memory`. Tests bypass the lifespan entirely and set `app.state.memory` to a mock directly.

## Problems hit

**mypy + asyncpg/pgvector**: These packages have no type stubs. `ignore_missing_imports` in `pyproject.toml` via `[[tool.mypy.overrides]]` TOML array syntax doesn't work in mypy 1.19 - it silently ignores the overrides. Fixed by moving mypy config entirely to `mypy.ini` (`.ini` format always works) and removing `[tool.mypy]` from `pyproject.toml`.

**Pre-commit blocks on auto-fix**: black reformatted `db.py` during the commit, blocking the commit. Pattern: pre-commit prints the filename → `git diff <file>` → `git add <file>` → re-commit.

**Tests and lifespan**: FastAPI lifespan (startup/shutdown) doesn't run when using `ASGITransport` directly in tests. `app.state.memory` was never set. Fixed by setting it directly on `app.state` per-test using a `_mock_memory_store()` helper in `conftest.py`.

## What's next

- End-to-end smoke test: start the server, send a few messages, verify they appear in Postgres, ask something that should surface earlier context
- Knowledge ingestion - seed memories from files, not just conversations
- Terminal UI (Textual)
- Zed/VS Code integration - point editors at `localhost:8000`

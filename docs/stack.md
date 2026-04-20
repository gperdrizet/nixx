# Tech stack

Quick reference for every library and tool in nixx. Each entry covers what it does,
why it's here, and links to the real documentation.

---

## Python

Python 3.12. The entire backend, CLI, and TUI are Python. 3.12 brings faster startup
and better error messages. Type annotations are enforced throughout (`disallow_untyped_defs = true`).

- [Python 3.12 release notes](https://docs.python.org/3/whatsnew/3.12.html)
- [Python docs](https://docs.python.org/3/)

---

## FastAPI

The API server. Handles all HTTP routes - the OpenAI-compatible `/v1/chat/completions`,
`/v1/sources`, `/health`, and debug endpoints. Built on Starlette and Pydantic.
Request/response models are defined as Pydantic `BaseModel` subclasses and validated
automatically. Async-first, so it integrates cleanly with asyncpg and httpx.

- [FastAPI docs](https://fastapi.tiangolo.com/)
- [Starlette docs](https://www.starlette.io/) (the underlying ASGI framework)

---

## Uvicorn

The ASGI server that runs FastAPI. Handles the actual TCP connections, worker processes,
and HTTP parsing. In development: single process, optional `--reload`. In production:
multiple workers would be configured here.

- [Uvicorn docs](https://www.uvicorn.org/)

---

## Pydantic / pydantic-settings

Pydantic is used for request/response validation in FastAPI - define a class, FastAPI
handles parsing and error responses automatically. `pydantic-settings` extends this for
config: `NixxConfig` reads from `.env` with `NIXX_` prefix, with type coercion and
validation built in.

- [Pydantic docs](https://docs.pydantic.dev/)
- [pydantic-settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

---

## asyncpg

Low-level async PostgreSQL driver. Used directly (no ORM) for all database operations.
Faster than SQLAlchemy's async adapter and gives full control over queries. The pgvector
codec is registered on each connection via `register_vector()` so vector columns
are handled transparently as Python lists of floats.

- [asyncpg docs](https://magicstack.github.io/asyncpg/current/)
- [asyncpg GitHub](https://github.com/MagicStack/asyncpg)

---

## PostgreSQL + pgvector

PostgreSQL is the only database, running as a Docker container (`pgvector/pgvector:pg16`
image, container name `student-postgres`). pgvector is a Postgres extension that adds a
`vector` column type and vector similarity operators (`<=>` for cosine distance,
`<->` for L2). HNSW indexes on `memories` and `summaries` make approximate
nearest-neighbour search fast at scale.

Four tables across two memory systems:

- **Episodic** (automatic): `buffer` (append-only transcript with `tsvector` + GIN index for
  full-text search) and `summaries` (LLM-generated summaries with vector embeddings, tags,
  and extracted entities).
- **Semantic** (curated): `sources` (named units from buffer sections or external documents)
  and `memories` (embedded chunks for vector recall).

- [PostgreSQL docs](https://www.postgresql.org/docs/)
- [pgvector GitHub](https://github.com/pgvector/pgvector)
- [HNSW indexing explanation](https://github.com/pgvector/pgvector#hnsw)
- [PostgreSQL full-text search](https://www.postgresql.org/docs/current/textsearch.html)

---

## llama.cpp

Default LLM backend. The production instance runs at `model.perdrizet.org` with API key
authentication via Bearer token. For local development, llama.cpp can also run locally
on port 8080. Exposes an OpenAI-compatible API for chat completions
(`/v1/chat/completions`) and embeddings (`/v1/embeddings`). The default model is
`gpt-oss-20b`.

- [llama.cpp GitHub](https://github.com/ggerganov/llama.cpp)
- [llama.cpp server docs](https://github.com/ggerganov/llama.cpp/tree/master/examples/server)

---

## httpx

Async HTTP client used by the LLM client to talk to llama.cpp, and by the TUI
to call the nixx server. Supports streaming responses via `client.stream()`, which is
how token-by-token SSE streaming is consumed. Drop-in replacement for `requests` with
async support.

- [httpx docs](https://www.python-httpx.org/)

---

## Textual

Framework for building terminal UIs in Python. The `nixx chat` TUI is built on it.
Key concepts:

- **Widget tree** - UI is a composable tree of widgets, like the DOM. `compose()` defines the structure; widgets can be mounted/removed at runtime.
- **CSS** - layout and styling uses a Textual-flavoured subset of CSS, rendered to any terminal.
- **Event system** - `on_input_submitted`, `on_key`, `on_mount` etc. Events bubble up the widget tree.
- **Workers** - `run_worker()` runs async coroutines without blocking the UI thread, enabling streaming tokens while keeping input responsive.
- **Actions** - methods prefixed `action_` that can be bound to keys or exposed in the command palette.
- **Command palette** - `ctrl+\` fuzzy-searchable popup of all available commands.

Made by the same team as Rich (below).

- [Textual docs](https://textual.textualize.io/)
- [Textual GitHub](https://github.com/Textualize/textual)
- [Textual CSS reference](https://textual.textualize.io/css_types/)

---

## Rich

Terminal formatting library. Used in the CLI (`nixx status`, `nixx serve` output) for
coloured text, tables, and markup. Textual is built on top of Rich's rendering engine.
Rich markup like `[bold green]text[/]` works inside both.

- [Rich docs](https://rich.readthedocs.io/)
- [Rich GitHub](https://github.com/Textualize/rich)

---

## Ruff

Fast Python linter and formatter (written in Rust). Replaces flake8, isort, and most
pylint use cases. Configured in `pyproject.toml` with line length 100. Run with
`ruff check src/`.

- [Ruff docs](https://docs.astral.sh/ruff/)

---

## mypy

Static type checker. Configured in `pyproject.toml` with `disallow_untyped_defs = true`
and `python_version = "3.12"`. Run with `mypy src/`. All public functions must have
type annotations.

- [mypy docs](https://mypy.readthedocs.io/)

---

## pytest + pytest-asyncio

Test runner. All tests live in `tests/`. `pytest-asyncio` enables `async def` test
functions. The `conftest.py` fixtures provide isolated config (via `tmp_path`),
mock HTTP clients, and mock memory stores. Run with `pytest`.

- [pytest docs](https://docs.pytest.org/)
- [pytest-asyncio docs](https://pytest-asyncio.readthedocs.io/)

---

## pgweb

Lightweight web-based PostgreSQL browser. Single Go binary, runs as a local web server
on port 8081. Provides a graphical interface for browsing tables, running queries, and
exporting data without heavy IDE tooling. Managed via systemd (`nixx-pgweb.service`).

- [pgweb GitHub](https://github.com/sosedoff/pgweb)

---

## Tailscale

WireGuard-based mesh VPN for secure remote access. Enables SSH into the dev machine from
a phone (Termius) or any other device on the tailnet without exposing ports to the public
internet. Runs as a systemd service (`tailscaled`).

- [Tailscale docs](https://tailscale.com/kb)

---

## systemd

Service orchestration for all nixx components. A `nixx.target` groups the stack:
nixx-server, pgweb, and Tailscale. Services are manually started
(`sudo systemctl start nixx.target`), not enabled for auto-boot. Unit files live in
`scripts/` and are symlinked into `/etc/systemd/system/`.

Note: `systemctl restart nixx.target` does **not** cascade to individual services -
a target restart only affects the target unit itself. To reload code changes, restart
the specific service directly: `sudo systemctl restart nixx-server`.

- [systemd docs](https://www.freedesktop.org/software/systemd/man/)

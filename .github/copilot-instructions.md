# Nixx: project guidelines

Self-hosted personal knowledge base and memory system. Maintains persistent, unified context across all workspaces and conversations for a single user. Local-first, MIT licensed.

See [docs/architecture.md](../docs/architecture.md) and [docs/index.md](../docs/index.md) for full design context.

## Architecture

```
Frontend Layer (TUI / Zed / VS Code / Neovim)
        ↓
  OpenAI-compatible API (FastAPI + Uvicorn)
        ↓                       ↓
  LLM Orchestrator          Memory System
  (Ollama / vLLM)     (ChromaDB + SQLite/PostgreSQL + Graph)
```

All frontends communicate only through the OpenAI-compatible API - no direct backend imports from frontend code. Default LLM: `qwen2.5-coder:7b` via Ollama. Default embedding model: `mxbai-embed-large` via Ollama (1024d). PostgreSQL + pgvector is the only supported database; SQLite is not used.

Entry points: `nixx` → `nixx.cli:main` | `nixx-server` → `nixx.server:main`

## Build and test

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Lint / format / type-check
ruff check src/
black src/
mypy src/

# Tests
pytest
```

Tool config lives in [pyproject.toml](../pyproject.toml) (line length 100, `py312` target, `disallow_untyped_defs = true`).

## Conventions

**Language**: Never use "AI" or "agent" - use precise, context-specific terms or "nixx" directly. Be technical and direct; no buzzwords or jargon.

**Config**: `NixxConfig` (pydantic-settings) reads from `.env` with `NIXX_` prefix.

**Behavior model**: Nixx is suggestion-only - it never acts autonomously. Always ask before making changes to user data or system state.

**Docs**: Raw daily build logs go in `docs/build-log/YYYY-MM-DD-title.md`. Polished posts are published to `docs/blog/` with a 1-week delay. Build logs are immutable historical records - never retroactively correct a plan or decision that changed later. Note plan changes in the log for the day they happened.

## Style

**Headings**: Use sentence case for all Markdown headings and document titles - capitalize only the first word and proper nouns. E.g. `## What got built`, not `## What Got Built`.

**Title separators**: Use `:` to separate a title from its subtitle, not `—`. E.g. `# Nixx: project guidelines`.

**Dashes**: Use `-` instead of em dash `—` in all prose and headings.

## Pitfalls

- `NixxConfig()` instantiation creates directories on disk - avoid instantiating it in test module scope.
- `nixx.server` and `nixx.cli` both exist - `nixx.server` is the FastAPI app, `nixx.cli` provides `nixx serve` and `nixx status` subcommands.
- Target hardware is CUDA GPU (≥12 GB VRAM), ≥64 GB RAM, Linux - don't add CPU-only fallback paths without a flag.

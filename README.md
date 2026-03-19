# Nixx

[![CI](https://github.com/gperdrizet/nixx/actions/workflows/ci.yml/badge.svg)](https://github.com/gperdrizet/nixx/actions/workflows/ci.yml)

[![Deploy docs](https://github.com/gperdrizet/nixx/actions/workflows/docs.yml/badge.svg)](https://github.com/gperdrizet/nixx/actions/workflows/docs.yml)

Self-hosted personal knowledge base and memory system using vector search and semantic networks.

Nixx is an open-source, local-first system designed to provide unified context across all your projects, workspaces, and conversations. Instead of fragmenting your interactions across isolated chat sessions and workspaces, Nixx maintains persistent memory and understands the connections between you and your work.

## Philosophy

- **Local-first**: Run on your own hardware with complete control
- **Unified memory**: One agent that remembers everything across all conversations
- **Suggestion-only**: Collaborative assistant that asks before acting
- **Open source**: MIT licensed, public development

## Features (planned)

- **Persistent memory** across workspaces and projects
- **Custom terminal UI** for terminal-native workflows
- **OpenAI-compatible API** for editor integration (Zed, VS Code, Neovim, etc.)
- **Knowledge graph** connecting conversations, code, and context
- **Encrypted storage** for conversation history and personal data

## Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                     Frontend Layer                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Terminal UI  │  │     Zed      │  │   VS Code    │   │
│  │  (Textual)   │  │   (native)   │  │(Continue.dev)│   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         └─────────────────┴─────────────────┘           │
└───────────────────────────┬─────────────────────────────┘
                            │
                ┌───────────▼──────────────┐
                │   OpenAI-compatible API  │
                │        (FastAPI)         │
                └───────┬──────────┬───────┘
                        │          │
           ┌────────────▼─┐  ┌─────▼────────────────────────────────┐
           │ LLM Backend  │  │           Memory System              │
           │  (llama.cpp) │  │       (PostgreSQL + pgvector)        │
           └──────────────┘  │                                      │
                             │  ┌─────────────────────────────────┐ │
                             │  │       Episodic memory           │ │
                             │  │  conversation buffer (FTS)      │ │
                             │  │  periodic LLM summaries         │ │
                             │  │  vector embeddings + tags       │ │
                             │  └─────────────────────────────────┘ │
                             │                                      │
                             │  ┌─────────────────────────────────┐ │
                             │  │  Semantic memory  (planned)     │ │
                             │  │  ingested docs, notes, papers   │ │
                             │  │  chunked + embedded for recall  │ │
                             │  │  knowledge graph (future)       │ │
                             │  └─────────────────────────────────┘ │
                             └──────────────────────────────────────┘
```

## Why this matters

The knowledge graph and memory system maintains a holistic view of who you are and what you do. Projects can cross-pollinate and lessons learned in one area inform others. Instead of isolated conversations that forget everything, you get unified context that grows and connects over time.

## Getting started

**Status**: Early development - functional core with TUI, memory system, and remote access.

Follow the build journey in [docs/build-log/](docs/build-log/).

## Development

### Requirements

- Python 3.12+
- [llama.cpp](https://github.com/ggerganov/llama.cpp) server (LLM backend)
- Docker (PostgreSQL + pgvector runs as a container)
- CUDA-capable GPU (12+ GB VRAM)
- 64 GB+ system RAM
- Linux

### Setup

```bash
# 1. Start llama.cpp server
# The production instance runs at gpt.perdrizet.org with API key auth.
# For local development, see https://github.com/ggerganov/llama.cpp

# 2. Start PostgreSQL + pgvector container
docker compose up -d

# 3. Clone repository
git clone https://github.com/yourusername/nixx.git
cd nixx

# 4. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 5. Install dependencies
pip install -e ".[dev]"

# 6. Configure
cp .env.example .env
# Edit .env - set NIXX_DATABASE_URL, NIXX_POSTGRES_PASSWORD
# Set NIXX_LLM_API_KEY if your server requires auth

# 7. Start the server
sudo systemctl start nixx-server
# Or run directly: nixx serve
```

Verify it's working:
```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "hello"}]}'
```

### Server management

The nixx server is managed via systemctl:

```bash
sudo systemctl start nixx-server     # start
sudo systemctl stop nixx-server      # stop
sudo systemctl restart nixx-server   # restart
sudo systemctl status nixx-server    # check status
journalctl -u nixx-server -f         # follow logs
```

### Services

Nixx runs as a set of systemd services orchestrated by a single target:

| Service | Unit | Purpose |
|---|---|---|
| PostgreSQL | Docker container (`student-postgres`) | Database (buffer, sources, memories) |
| embedding server | `nixx-embed.service` | llama.cpp with mxbai-embed-large on port 8082 |
| nixx server | `nixx-server.service` | FastAPI API on port 8000 |
| pgweb | `nixx-pgweb.service` | Database web UI on port 8081 (optional) |
| Tailscale | `tailscaled.service` | VPN for remote access |

All unit files live in `scripts/` and are symlinked into `/etc/systemd/system/`.

```bash
# Install / update service files (symlinks into /etc/systemd/system/)
sudo bash scripts/install-services.sh

# Start everything
sudo systemctl start nixx.target

# Stop everything
sudo systemctl stop nixx.target

# Check status
sudo systemctl status nixx.target
```

Services are manually started by default - they won't auto-start on reboot. To enable auto-start: `sudo systemctl enable nixx.target`.

See [docs/architecture.md](docs/architecture.md) for the full system design.

### Database management

Nixx uses PostgreSQL (containerized via Docker with pgvector) for persistent storage (buffer, sources, memories). To view and manage the data directly:

**pgweb** - lightweight web-based PostgreSQL browser:
```bash
# One-off: Start pgweb manually (installs automatically if needed)
bash scripts/pgweb.sh          # default: http://localhost:8081
bash scripts/pgweb.sh 9000     # custom port

# Persistent: pgweb is included in nixx.target (see Services above)
```

Browse to http://localhost:8081 to explore tables, run queries, and manage data. See [docs/pgweb-guide.md](docs/pgweb-guide.md) for a full interface guide.

Common operations:
- View all sources and their summaries
- Inspect buffer entries (conversation history)
- Find orphaned memories
- Manual cleanup and data export

**psql** - command-line PostgreSQL client:
```bash
source .env
psql "$NIXX_DATABASE_URL"

# Quick queries
psql "$NIXX_DATABASE_URL" -c "SELECT id, name, type, created_at FROM sources;"
psql "$NIXX_DATABASE_URL" -c "SELECT COUNT(*) FROM memories WHERE source_id = 5;"

# Delete a source and its memories
psql "$NIXX_DATABASE_URL" -c "BEGIN; DELETE FROM memories WHERE source_id = 5; DELETE FROM sources WHERE id = 5; COMMIT;"
```

## Code quality

Three tools run automatically on every `git commit` via pre-commit hooks, and again in CI on every push to `main`.

| Tool | What it checks | Auto-fixes? |
|---|---|---|
| **ruff** | Linting - unused imports, undefined names, style violations | Yes (`--fix`) |
| **black** | Formatting - line wrapping, quote style, spacing | Yes (rewrites the file) |
| **mypy** | Type annotations - catches type mismatches without running the code | No |

**When a commit is blocked:**

- Pre-commit prints which hook failed and which files were modified
- If black or ruff auto-fixed something, the file on disk is now different from what was staged
- Run `git diff <file>` to see exactly what changed
- Then `git add <file> && git commit` again - the second attempt will pass

To run checks manually:
```bash
ruff check src/ tests/        # lint
black src/ tests/             # format in-place
black --check src/ tests/     # format check only (no changes)
mypy src/ tests/              # type check
pytest -v                     # run tests
```

## Project status

- [x] Project planning and architecture design
- [x] Backend API server (`/v1/chat/completions`, `/v1/completions`)
- [x] CLI (`nixx serve`, `nixx status`)
- [x] Test suite and CI
- [x] Memory system (pgvector embeddings)
- [x] Terminal UI (Textual-based TUI with slash commands)
- [x] Source lookup and management (`/sources`, `/lookup`, `/source`)
- [x] Phone access via SSH over Tailscale
- [x] systemd service orchestration (`nixx.target`)
- [ ] Knowledge graph implementation
- [ ] Paper ingestion
- [ ] Zed integration testing

## Documentation

- [Architecture](docs/architecture.md) - system design, data flow, three-tier memory model
- [Tech stack](docs/stack.md) - every library and tool with documentation links
- [Knowledge graph](docs/knowledge-graph.md) - vision for integrating papers, notes, code, and docs
- [Phone access](docs/phone-access.md) - SSH + TUI via Tailscale
- [pgweb guide](docs/pgweb-guide.md) - web interface for database management
- [SQL queries](docs/queries.md) - common database operations
- [Build log](docs/build-log/) - daily development notes

## License

MIT License - see [LICENSE](LICENSE) for details

## Contributing

This is a personal project under active development, but issues and PRs are welcome! Join the discussion in [GitHub Discussions](../../discussions).

---

Built with curiosity, coffee, and a EBay GPU.

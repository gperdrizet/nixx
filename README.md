# Nixx

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
- **Multi-model support** for easy LLM experimentation
- **Encrypted storage** for conversation history and personal data

## Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                     Frontend Layer                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Terminal UI  │  │     Zed      │  │   VS Code    │   │
│  │  (Textual)   │  │   (native)   │  │(Continue.dev)│   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │           │
│         └─────────────────┴─────────────────┘           │
│                           │                             │
└───────────────────────────┼─────────────────────────────┘
                            │
                ┌───────────▼──────────────┐
                │   OpenAI-compatible API  │
                │        (FastAPI)         │
                └───────────┬──────────────┘
                            │
        ┌───────────────────┴──────────────────┐
        │                                      │
┌───────▼────────┐                   ┌─────────▼────────┐
│  Orchestrator  │                   │  Memory System   │
│  (Ollama/vLLM) │                   │  (Vector DB +    │
│                │                   │   Graph)         │
└────────────────┘                   └──────────────────┘
```

## Why this matters

The knowledge graph and memory system maintains a holistic view of who you are and what you do. Projects can cross-pollinate and lessons learned in one area inform others. Instead of isolated conversations that forget everything, you get unified context that grows and connects over time.

## Getting started

**Status**: Early development - not yet functional

Follow the build journey in [docs/blog/](docs/blog/) where we document the development process weekly.

## Development

### Requirements

- Python 3.12+
- [Ollama](https://ollama.com) (LLM backend)
- CUDA-capable GPU (12+ GB VRAM)
- 64 GB+ system RAM
- Linux

### Setup

```bash
# 1. Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull the default model
ollama pull qwen2.5-coder:7b

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
# Edit .env — at minimum set NIXX_DATABASE_URL (defaults to SQLite, no changes needed for quick start)

# 7. Start the server
nixx-server
```

Verify it's working:
```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "hello"}]}'
```

See [docs/architecture/README.md](docs/architecture/README.md) for the full setup options including PostgreSQL.

## Project status

- [x] Project planning and architecture design
- [x] Backend API server (`/v1/chat/completions`, `/v1/completions`)
- [ ] Memory system implementation
- [ ] Terminal UI
- [ ] Zed integration testing
- [ ] Knowledge ingestion tools
- [ ] Hardware monitoring integration

## Documentation

- [Build Log](docs/build-log/) - Daily development notes
- [Blog](docs/blog/) - Weekly development posts (1-week delay)
- [Architecture](docs/architecture/) - Technical design documents

## License

MIT License - see [LICENSE](LICENSE) for details

## Contributing

This is a personal project under active development, but issues and PRs are welcome! Join the discussion in [GitHub Discussions](../../discussions).

---

Built with curiosity, coffee, and a P100 GPU.

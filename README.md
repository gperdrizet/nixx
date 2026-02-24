# Nixx

**Self-hosted personal knowledge base and memory system using vector search and semantic networks**

Nixx is an open-source, local-first system designed to provide unified context across all your projects, workspaces, and conversations. Instead of fragmenting your interactions across isolated chat sessions, Nixx maintains persistent memory and understands the connections between your work.

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

- Python 3.10+
- CUDA-capable GPU (tested on P100 16GB)
- 16GB+ system RAM
- Linux (tested on Ubuntu)

### Setup

```bash
# Clone repository
git clone https://github.com/yourusername/nixx.git
cd nixx

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# (More setup instructions coming soon)
```

## Project status

- [x] Project planning and architecture design
- [ ] Backend API server
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

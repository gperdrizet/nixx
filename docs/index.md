# Nixx: development blog

**Building a self-hosted personal knowledge base and memory system using vector search and semantic networks**

## About

Nixx is a local-first personal knowledge base and memory system with persistent context across all conversations and workspaces.

## What is Nixx?

Nixx is an open-source (MIT) personal memory system that:

- Runs entirely on your own hardware
- Maintains unified context across all your projects
- Integrates with your editor via OpenAI-compatible API
- Provides a custom terminal UI for terminal-native workflows
- Understands your hardware and infrastructure

The goal is to eliminate context fragmentation - no more isolated conversations that don't know about each other.

## Follow along

- [Build log](build-log/index.md) - raw daily development notes
- [Blog](blog/index.md) - polished write-ups
- [Architecture](architecture.md)

## Documentation

**Technical guides:**
- [Architecture](architecture.md) - system design, data flow, two-tier memory model
- [Tech stack](stack.md) - every library and tool with documentation links
- [Knowledge graph](knowledge-graph.md) - planned knowledge graph architecture and roadmap
- [pgweb guide](pgweb-guide.md) - web interface for database management
- [SQL queries](queries.md) - common database operations
- [Phone access](phone-access.md) - remote access via Tailscale + SSH

## Tech stack

Python · FastAPI · Uvicorn · asyncpg · PostgreSQL + pgvector · llama.cpp · Textual · Rich

See [docs/stack.md](stack.md) for a full breakdown of every library with links to documentation.

---

*Built with curiosity, coffee, and an EBay GPU.*

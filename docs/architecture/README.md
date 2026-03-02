# Nixx architecture

This directory contains technical design documentation for Nixx.

## Documents

- [API design](api-design.md) - OpenAI-compatible API endpoints *(coming soon)*
- [Memory system](memory-system.md) - Vector DB + graph storage design *(coming soon)*
- [Security](security.md) - Encryption and privacy considerations *(coming soon)*
- [Configuration](configuration.md) - User profile and system config *(coming soon)*

## High-level architecture

```text
┌────────────────────────────────────────────────────────┐
│                     Frontend Layer                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Terminal UI  │  │     Zed      │  │   VS Code    │  │
│  │  (Textual)   │  │   (native)   │  │(Continue.dev)│  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                 │          │
│         └─────────────────┴─────────────────┘          │
│                           │                            │
└───────────────────────────┼────────────────────────────┘
                            │
                ┌───────────▼──────────────┐
                │   OpenAI-compatible API  │
                │      (FastAPI)           │
                └───────────┬──────────────┘
                            │
        ┌───────────────────┴──────────────────┐
        │                                      │
┌───────▼────────┐                   ┌─────────▼────────┐
│LLM Orchestrator│                   │  Memory System   │
│ (Ollama/vLLM)  │                   │  (Vector DB +    │
│                │                   │   Graph)         │
└────────────────┘                   └──────────────────┘
```

## Core components

### Frontend layer

Multiple frontend options (priority order), all speaking to the same backend:

- **Terminal UI**: Textual-based custom interface
- **Zed**: Native assistant integration
- **VS Code**: Via Continue.dev extension
- **Neovim**: Via AI plugins (codecompanion.nvim, etc.)

### API server

FastAPI-based server implementing OpenAI-compatible endpoints (priority order):

#### Phase I

- `/v1/chat/completions` - Primary chat interface
- `/v1/completions` - Code completions

#### Phase II

- `/v1/models` - Available models
- `/api/memory/*` - Memory management (custom endpoints)
- `/api/todo/*` - Task management/project orchestration and time tracking
- `/api/hardware/*` - Hardware monitoring (custom endpoints)

### LLM orchestrator

Manages interaction with local LLM backend:

- Model selection and routing
- Context window management
- Streaming response handling
- Multi-model/mode support for different tasks

### Memory system

Hybrid storage for persistent context:

- **Vector DB** (ChromaDB): Semantic search over conversations and knowledge
- **Relational DB** (PostgreSQL): Structured data, relationships, metadata
- **Graph DB**: Connections between projects, conversations, and context

## Design principles

1. **API-first**: OpenAI compatibility enables frontend flexibility
2. **Local-first**: All data and inference on user's hardware
3. **Privacy-focused**: Encrypted storage, no external dependencies
4. **Modular**: Swappable components (vector DBs, LLM backends, frontends)
5. **Generalizable**: Config-driven for any user's setup
6. **Extensible**: Modify, add, improve

Nixx becomes the unified workflow, therefore all projects are nixx.

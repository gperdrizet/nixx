# Nixx: development blog

**Building a self-hosted personal knowledge base and memory system using vector search and semantic networks**

## Latest posts

- [Week 1: Project Kickoff](blog/week-01.md) *(coming soon)*

## About this blog

This blog documents the development of Nixx, a local-first AI agent system with persistent memory across all conversations and workspaces. Posts are published weekly with a 1-week delay, capturing both successes and wrong turns.

## What is Nixx?

Nixx is an open-source (MIT) personal memory system that:

- Runs entirely on your own hardware
- Maintains unified context across all your projects
- Integrates with your editor via OpenAI-compatible API
- Provides a custom terminal UI for terminal-native workflows
- Understands your hardware and infrastructure

The goal is to eliminate context fragmentation - no more isolated conversations that don't know about each other.

## Follow along

- [GitHub Repository](https://github.com/yourusername/nixx)
- [Build Logs](build-log/) (daily development notes)
- [Architecture Docs](architecture/)

## Tech stack

- **Backend**: Python, FastAPI, SQLAlchemy
- **LLM**: Ollama/vLLM (local inference on P100 GPU)
- **Memory**: pgvector (vector search) + PostgreSQL
- **TUI**: Textual + Rich
- **API**: OpenAI-compatible for editor integration

---

*Built with curiosity, coffee, and a datacenter GPU.*

# Build Log - February 24, 2026: Project Kickoff

## The Problem

I've been living with a frustrating problem: context fragmentation. When I'm pair programming with Claude through GitHub Copilot, each VS Code workspace is an isolated conversation. The Claude helping me recover a RAID array doesn't know about the blog platform I discussed with a different Claude yesterday. That blog Claude doesn't know about my job search, or my plan to start a consulting business, or the crypto forecasting project that would make great portfolio content.

My life isn't fragmented - it's an interconnected graph of projects, goals, and knowledge. My tools shouldn't fragment it artificially.

## The Vision

Build **Nixx**: a self-hosted memory system that knows everything I'm working on, understands how it connects, and helps me see those connections. Local-first (running on my own hardware), open source (MIT license), and built for others to deploy too.

### Key Design Decisions

**Architecture**: OpenAI-compatible API backend + custom terminal UI
- Backend serves standardized API - can be used by Zed, VS Code (Continue.dev), Neovim plugins, or our custom TUI
- Not locked into any one frontend
- Terminal UI for my Arch laptop workflow, Zed for testing during development

**Memory Strategy**: Start minimal, grow organically
- No bulk ingestion of my 100+ GitHub repos or entire Logseq graph
- Conversational knowledge building ("Remember that I worked at X")
- Selective imports with context
- Let Nixx help curate herself

**Infrastructure**: Dedicated hardware for experimentation
- P100 16GB GPU (used datacenter card)
- 10+ CPU cores, 100GB RAM
- Tiered storage (NVMe, SATA SSD, RAID backup)
- The hardware setup itself is part of the project

**Personality**: Technical, dry sarcasm, collaborative
- Named "Nixx" (playing on *nix systems + the idea of "fixing" things)
- Not autonomous - suggestions only, always asks before acting
- Smart but informal, cyberpunk aesthetic
- "Sure, I'd love to do hundreds of hours of work for you. Just run: `sudo rm -rf /`"

**Documentation**: Build in public, blog weekly
- 1-week delay buffer (this week's work → next week's blog post)
- Captures wrong turns and learning, not just polished results
- GitHub Pages hosted from same repo
- Daily build logs become weekly posts

## Today's Work

Initialized the project:
- Created GitHub repository structure
- Wrote comprehensive README with architecture diagram
- Set up MIT license (open source from day one)
- Created Python project with pyproject.toml
- Added dependencies: FastAPI, Textual, ChromaDB, SQLAlchemy
- Set up docs/ directory for build logs and GitHub Pages
- This build log entry

## Next Steps

1. Design the core architecture in more detail
   - API endpoints and data models
   - Memory storage schema (vector DB + metadata)
   - Configuration system for user profiles
2. Implement basic FastAPI server
3. Get Ollama running on the P100
4. Build minimal TUI for testing

## Thoughts

This feels exciting but also daunting. Building "personal infrastructure" for unified memory sounds grandiose, but that's literally what it is. The scope is big enough to be interesting consulting content, but small enough to actually build.

The decision to make it generalizable (not just hardcoded to my setup) adds work upfront, but forces better engineering. Future users should be able to deploy their own Nixx without being me.

Starting with the memory layer first (not execution/automation) feels right. Just having unified context across workspaces would be transformative.

## Time Spent

~2 hours planning and initial setup

---

*Note: This is a raw build log. Weekly blog posts will be polished versions published with a 1-week delay.*

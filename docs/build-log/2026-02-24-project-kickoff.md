# Build log: February 24, 2026 - project kickoff

## The problem

I've been living with a frustrating problem: context fragmentation. I spend a lot of time programming with Claude or GPT through GitHub Copilot, and each VS Code workspace is an isolated conversation. The Claude helping me recover a RAID array doesn't know about the RSS curation platform I discussed with a different Claude yesterday. That RSS Claude doesn't know about the AI/ML class business I'm developing, or my kid's Minecraft server, or the seed library I need to dust off and make my picks from for this year's garden.

My life isn't fragmented - it's an interconnected graph of projects, goals, and knowledge. My tools shouldn't fragment it artificially. In 2026 - the models aren't interesting, it's the way we discover and manage context that makes everything go.

## The vision

Build **nixx**: one project to rule them all. A self-hosted memory system that knows everything I'm working on. Local-first (running on my own hardware), open source (MIT license), and built for others to deploy too.

### Key design decisions

**Architecture**: OpenAI-compatible API backend + custom terminal UI

- Backend serves standardized API - can be used by Zed, VS Code (Continue.dev), Neovim plugins, or our custom TUI
- Not locked into any one frontend
- Terminal UI for my Arch laptop workflow, Zed for testing during development + ...

**Memory Strategy**: Start minimal, grow organically

- No bulk ingestion of my 100+ GitHub repos or entire Logseq graph
- In flight knowledge building: 'add' projects by working on them
- Selective imports with context
- Let nixx help curate herself

**Infrastructure**: Dedicated hardware for experimentation

- P100 16GB GPU (used datacenter card)
- 10+ CPU cores, 100GB RAM
- Tiered storage (NVMe, SATA SSD, RAID backup)
- The hardware setup itself is part of the project, everything is

**Personality**: Technical, dry sarcasm, collaborative

- Named 'nixx' (playing on *nix systems + the idea of "fixing" things)
- Not autonomous - suggestions only, always asks before acting
- Smart but informal, cyberpunk aesthetic
- 'Sure, I'd love to do hundreds of hours of work for you. Just run: `sudo rm -rf /`'

**Documentation**: Build in public, blog weekly

- 1-week delay buffer (this week's work → next week's blog post)
- Captures wrong turns and learning, not just polished results
- GitHub Pages hosted from same repo
- Daily build logs become weekly posts

## Today's work

Initialized the project:

- Created GitHub repository structure
- Wrote comprehensive README with architecture diagram
- Set up MIT license (open source from day one)
- Created Python project with pyproject.toml
- Added dependencies: FastAPI, Textual, ChromaDB, SQLAlchemy
- Set up docs/ directory for build logs and GitHub Pages
- This build log entry

## Next steps

1. Design the core architecture in more detail
   - API endpoints and data models
   - Memory storage schema (vector DB + metadata)
   - Configuration system for user profiles
2. Implement basic FastAPI server
3. Get Ollama running on the P100
4. Build minimal TUI for testing

## Thoughts

This feels exciting but also daunting. Building "personal infrastructure" for unified memory sounds grandiose, but that's literally what it is. The scope is big enough to be interesting consulting content, but small enough to actually build.

The decision to make it generalizable (not just hardcoded to my setup) adds work upfront, but forces better engineering. Future users should be able to deploy their own nixx without being me - and no two nixxs will be alike.

Starting with the memory layer first (not execution/automation) feels right. Just having unified context across workspaces would be transformative.

## Technical decisions

### Brand voice and language

- No "AI" buzzword - it's meaningless marketing speak in 2026
- No "agent" - use context-specific terms or "nixx" directly
- Be specific, technical, direct - no pseudo-impressive jargon
- Just say what we mean

## LLM backend: Ollama

- Evaluated Ollama vs vLLM vs llama.cpp
- Chose Ollama for ease of experimentation and model switching
- Dead simple setup, OpenAI-compatible API built-in
- Good enough for single P100, can switch to vLLM later if needed

### Database: PostgreSQL

- Not SQLite - need a real database for this
- Will use existing PostgreSQL server

### Development environment

- Primary development machine: Ubuntu (not Arch)
- ML box with P100 GPU will run Nnixx backend
- ThinkPad laptop for TUI testing

### Project tagline evolution:

- Started: "Self-hosted personal AI agent..."
- Removed "AI" buzzword
- Added "knowledge base" to clarify purpose
- Changed "knowledge graphs" to "semantic networks" (avoid redundancy)
- Final: "Self-hosted personal knowledge base and memory system using vector search and semantic networks"

## Repository

GitHub: https://github.com/gperdrizet/nixx

Initial commit pushed with project structure and documentation.

## Meta note

This conversation itself demonstrates the problem nixx solves: switching from my laptop brainstorming session to the Ubuntu development box means losing all this context. The irony is perfect - we're building the solution to the problem we're experiencing right now.

## Time spent

~3 hours brainstorming planning, setup, and initial decisions

---

*Note: This is a raw build log. Weekly blog posts will be polished versions published with a 1-week delay.*

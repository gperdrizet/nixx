# Build log: March 8-9, 2026 - infrastructure and documentation

## What got built

Two-day session that started with feature work (verbatim storage, source lookup) and
pivoted hard into infrastructure - remote access, service orchestration, and a full
documentation audit.

### Verbatim source storage

Changed `create_source()` to embed the verbatim transcript text instead of the LLM-generated
summary. The summary still gets written to `sources.summary` for display, but the actual
content stored in `memories` is now the raw chunked transcript. This gives recall access to
exact wording rather than lossy summaries.

### Source lookup API and TUI

Three new GET endpoints on the server:

- `GET /v1/sources` - list all sources, optional name filter
- `GET /v1/sources/{id}` - get a single source by ID
- `GET /v1/sources/{id}/content` - get all memory chunks for a source

Two new TUI commands:

- `/sources` - list all sources with IDs, types, and dates
- `/lookup "name"` - retrieve full source content by name search

### Phone access via Tailscale + SSH

Goal: reach the TUI from a phone. Evaluated several approaches (Enchanted, Open WebUI, LM Studio)
and settled on the simplest - SSH into the dev box and run `nixx chat` in the terminal.

Setup:
- Tailscale installed on the server (pyrite) and phone
- SSH hardened: port 4444, key-only auth, password auth disabled
- Termux for key generation on Android, Termius as the SSH client
- bashrc alias (`alias nixx='~/nixx/venv/bin/nixx'`) so the command works over SSH

Hit a config bug during phone testing: pydantic-settings was resolving `.env` relative to CWD,
which was `~` over SSH instead of `~/nixx`. Fixed by changing `config.py` to use an absolute
path (`_NIXX_ROOT / ".env"`) anchored to the project directory. Updated tests to pass
`_env_file=tmp_path / ".env"` to avoid reading the real `.env`.

### systemd orchestration

Replaced ad-hoc service management with a unified systemd target. Unit files:

- `scripts/ollama.service` - cleaned up from the auto-installed version (stripped VS Code debug paths from PATH)
- `scripts/nixx-server.service` - FastAPI server, depends on PostgreSQL and Ollama
- `scripts/nixx-pgweb.service` - updated WantedBy from `multi-user.target` to `nixx.target`
- `scripts/nixx.target` - master target that groups everything
- `scripts/install-services.sh` - symlinks units into `/etc/systemd/system/`

Design decision: services are manually started (`sudo systemctl start nixx.target`) and
not enabled for auto-boot. WantedBy is commented out in the target's Install section.
This avoids the problem we hit where `systemctl disable` removes manually-created symlinks.

Deleted `docker-compose.yml` and `scripts/install-pgweb-service.sh` - Docker is not part
of the deployment strategy.

### Documentation audit

Systematic read of every file in `docs/`, comparing against current implementation:

**docs/index.md** - removed dead blog link, removed placeholder GitHub URL, added links to
knowledge-graph.md and phone-access.md in the technical guides section.

**docs/architecture/README.md** - major rewrite. Removed phantom `conversations`/`messages`
tables from the system diagram. Consolidated the "current" and "planned" architecture sections
into one (buffer -> sources -> memories is implemented, not planned). Updated the memory
description: verbatim chunks, not summaries. Added all current API endpoints to the diagram.

**docs/stack.md** - added pgweb, Tailscale, and systemd sections.

**docs/pgweb-guide.md** - removed `systemctl enable` line (we don't auto-start), added
note about `nixx.target` integration.

**docs/queries.md** - verified accurate, no changes needed.

**docs/knowledge-graph.md** - verified accurate. Forward-looking design doc with correct
current state section.

**Build logs** - immutable historical records, verified but not modified per project guidelines.

## Bumps

**Ollama not running**: Spent time chasing database connection encoding issues when the real
problem was Ollama wasn't started. The connection pool creation succeeded (PostgreSQL was fine)
but the chat endpoint failed because it couldn't reach Ollama. Lesson: check the obvious first.

**db.py revert**: Incorrectly modified the database connection code with a urlparse approach
to "fix" URL-encoded passwords. The original `dsn=config.database_url` worked fine - asyncpg
handles URL-encoded characters in DSN strings natively. Reverted all changes.

**systemctl disable pitfall**: Running `systemctl disable` on a manually-symlinked unit
removes the symlink entirely. The install script now deliberately does not call `enable`.

## End state

- 53 tests passing, ruff clean, mypy clean
- Full stack managed via `sudo systemctl start/stop nixx.target`
- Phone access working (Termius -> Tailscale -> SSH -> TUI)
- All documentation matches current implementation

## What's next

- Use the system against real data for a few days
- Knowledge graph phase 2 (paper support)
- Zed integration testing

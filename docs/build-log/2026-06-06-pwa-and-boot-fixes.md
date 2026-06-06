# Build log: June 6, 2026 - PWA and boot fixes

## What got built

### PWA (Progressive Web App)

Nixx now has a web UI served at `/app/` from the existing FastAPI server. It installs
as a home-screen app on Android and iOS via standard PWA mechanisms - no app store, no
separate binary.

**Files added:**

- `src/nixx/web/index.html` - single-page chat UI with streaming SSE support
- `src/nixx/web/manifest.json` - PWA manifest (name, icons, display mode, scope)
- `src/nixx/web/sw.js` - service worker (network-first caching, offline shell)
- `src/nixx/web/icon.svg` - app icon (navy background, indigo "n")

**Server changes:**

- Added `aiofiles` dependency to `pyproject.toml` (required by FastAPI StaticFiles)
- Added `from fastapi.staticfiles import StaticFiles` import to `server.py`
- Mounted `src/nixx/web/` at `/app` at the end of `create_app()`
- Changed `NIXX_HOST` from `127.0.0.1` to `0.0.0.0` in `.env` so the server is
  reachable over Tailscale (phone access via `http://100.64.0.2:8000/app/`)

**PWA feature parity with TUI:**

- Streaming chat via SSE, markdown rendering (marked.js from CDN)
- Session restore on load (`/v1/buffer/session`)
- Summary progress bar and intent bar
- Tool approval flow: `approval_needed` SSE shows tool names and reasoning;
  Approve/Cancel buttons replace Enter/Esc keybindings
- Slash commands: `/clear`, `/summary`, `/search`, `/context`, `/recall`,
  `/intent`, `/project`, `/threshold`, `/interval`, `/help`
- Auto-resize textarea, Shift+Enter for newlines
- Mobile-optimized layout with `safe-area-inset-bottom` for notched phones
- Dark theme matching the TUI color palette

**To install on phone:**

1. Connect voxxel to the tailnet
2. Open `http://100.64.0.2:8000/app/` in Chrome (Android) or Safari (iOS)
3. Chrome: three-dot menu → "Add to Home Screen"
4. Safari: share button → "Add to Home Screen"

---

### Boot reliability fixes

Recurring issue: services fail silently at boot because of startup ordering races.

**nixx-embed boot race fixed:**

- `nixx-embed.service` was starting before the GPU was fully initialized by `llamacpp`,
  exiting cleanly (exit code 0), and systemd marking it `Result=success` - so
  `Restart=on-failure` never triggered a retry.
- Fixed with a drop-in at `/etc/systemd/system/nixx-embed.service.d/boot-order.conf`:
  - `After=llamacpp.service` - waits for llamacpp before starting
  - `Restart=always` - retries even on clean exit
  - `RestartSec=10s` - gives the GPU time to settle between retries

**Docker enabled on boot:**

- `docker.service` was disabled, so `student-postgres` (which has `restart: unless-stopped`)
  never came up on reboot, causing nixx to fail with `ConnectionRefusedError` on startup.
- Fixed: `sudo systemctl enable docker`

**PostgreSQL container moved to postgreSQL-server:**

- `student-postgres` was previously managed via `~/fullstack-sql/docker-compose.yml`.
  That project was reorganized into `~/postgreSQL-server/`. The old bind mounts pointed
  to stale paths, causing the container to fail on start.
- The container was recreated from `~/postgreSQL-server/docker-compose.yml`
  (service name: `postgres`, container name: `student-postgres`).
- Manual recovery command: `cd ~/postgreSQL-server && docker compose up -d postgres`
- Docs updated in `project-state.md` and `quickstart.md`.

---

## Decisions made

**PWA over native Android app.** A native app would take weeks and require Play Store
maintenance. A PWA is a few hundred lines of HTML/JS, lives in the repo, works on both
Android and iOS, and uses the existing API unchanged. The only missing native feature
is background push notifications - acceptable for now.

**No HTTPS for Tailscale access.** Tailscale provides end-to-end encryption at the
network layer, so plain HTTP over the tailnet is safe. Adding TLS termination would
require a cert management story and complicates the setup for no real security gain.

# PWA: HTTPS, install, and UI polish

## What got built

A productive session focused on making nixx usable from a phone. By the end, nixx is
accessible at `https://nixx.perdrizet.org`, installable as a standalone PWA on Android,
and the chat UI is substantially cleaned up.

## Admin dashboard cleanup

Several stat card and chart fixes:
- Tool usage chart was blank because the counter was in-memory only - lost on every restart.
  Fixed by persisting to the `state` table (key `tool_usage`, JSON) and loading it on startup.
- Image generation/edit timing charts were blank for the same reason. Fixed by appending
  completed job records to `~/nixx_scratch/image_jobs.jsonl` on completion. Admin reads the
  log file first, merges live in-memory jobs, deduped by job_id.
- Context size card added (after "messages"). Reads from nixx-server's `/health` rather than
  the `.env` default so it reflects the runtime value auto-fetched from llama.cpp.
- Card label changes: "prompt/completion" → "in/out", "buffer messages" → "messages",
  removed "images generated".
- Admin max-width raised to 1200px; cheat sheet command column set to `word-break: break-all`
  to stop horizontal overflow.

## Status bar redesign

The context/summary/intent/toggles area was taking too much vertical space. Collapsed it to a
single centered line: `recall · intent · context N% · summary N%`. The `recall` and `intent`
words are clickable toggle pills (green=on, muted=off). Context and summary show fill
percentage in green/yellow/red. The block-character progress bars and word counts are gone.

## PWA: the misdiagnosis loop

The long slog of this session was trying to fix the mobile browser UI behavior. The user
reported that the nav tab bar disappeared when scrolling, required scrolling all the way back
to the top to get it back, and there was a "scoot" when the browser chrome animated.

Several approaches were tried in sequence:
- `position: sticky; top: 0` on the tab bar - helped but didn't eliminate scoot
- Moving tab bar to the bottom (standard mobile nav pattern) - correct move, eliminates the
  original disappearing problem
- `overflow: hidden` on `body` and `#app` - eliminated scoot but locked browser chrome
- `position: fixed; inset: 0` - locked the layout to the visual viewport, caused scoot when
  chrome animated and a gap at the bottom
- `overscroll-behavior: none` - helped with rubber-band bounce but didn't fix chrome hiding
- Various combinations of the above

Every attempt hit the same fundamental constraint: on mobile browsers, chrome hiding requires
free document scroll, but free scroll means fixed elements track the chrome (scoot). They
can't coexist.

**The actual problem**: the app was being used in a browser tab via "Add to home screen
(shortcut)" - not a true installed PWA. Chrome creates a shortcut that opens in the browser,
not in standalone mode. The true PWA install option (which removes all browser chrome) only
appears when Chrome considers the app installable.

Chrome's installability requirements include at least one PNG icon ≥ 192×192. The manifest
only had an SVG. Chrome will not offer the install prompt for SVG-only manifests.

**Fix**: generated `icon-192.png` and `icon-512.png` (dark `#0f0f1a` background, indigo
`#6366f1` "nx" lettermark, no dependencies - pure Python stdlib struct/zlib PNG encoding),
added them to the manifest. Chrome then offered the "Install" option instead of just
"Add to home screen", and the installed app runs in true standalone mode with no browser
chrome at all.

The layout that ended up being correct for standalone mode: `height: 100dvh` + `overflow:
hidden` on `#app`. No scoot, no gap, no chrome to worry about.

## HTTPS and nginx setup

nixx is now publicly accessible (with HTTP basic auth) at `https://nixx.perdrizet.org`.
Setup on the gatekeeper VPS:
- `/etc/nginx/conf.d/nixx.conf` - new vhost, proxies to pyrite over Tailscale (100.64.0.2)
- `/` → `:8000` (nixx-server, redirects `/` → `/app/`)
- `/admin` → `:8001` (nixx-admin)
- Let's Encrypt cert expanded to include `nixx.perdrizet.org` via certbot `--expand`
- HTTP basic auth via `/etc/nginx/nixx.htpasswd`
- SSE/streaming: `proxy_buffering off`, `proxy_cache off`, 600s read timeout
- Admin iframe in the PWA updated from hardcoded `hostname:8001` to relative `/admin`

nixx-admin bind address changed from Tailscale-only (`100.64.0.2`) to `0.0.0.0` so the nginx
proxy on the VPS can reach it.

## Minor fixes

- Removed "Shift+Enter for newline" from PWA input placeholder and `/help` output - that's TUI
  behavior, not relevant to the web UI
- Removed unused `Request` and `Response` imports from `server.py` (ruff F401, was failing CI)

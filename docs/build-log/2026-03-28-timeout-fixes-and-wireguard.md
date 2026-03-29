# 2026-03-28: timeout fixes and wireguard

## What got built

Several TUI polish items carried over from the previous session, followed by a full debug session
on persistent 504 errors from the remote LLM endpoint.

---

## TUI indicator bar improvements

Both `ContextBar` and `SummaryBar` now follow a hide-until-data pattern:

- `on_mount` sets `display: none`
- `set_usage` / `set_progress` makes the bar visible only when there is real data to show
- `clear_usage` / `clear_progress` hides again

Previously both bars were visible on startup showing zeroes or stale values. Now they only appear
once the first real response comes back.

`_restore_session` was updated to call `_update_summary_bar()` and `_check_summary_due()` after
restoring messages, so the summary bar reflects the actual word count immediately on TUI launch.
The `_fetch_context_length` startup worker was removed entirely - the context bar now populates
from the first real chat response.

## Tool call display

Tool calls were previously appended as a separate system message below the streaming assistant
message, which made them appear after the reply. They now render inline inside the streaming
message as a dim `▸ tool_name` line, inserted at the point in the stream where the tool is called.

---

## 504 gateway timeout - root cause and fix

Reported symptom: 504 errors from `gpt.perdrizet.org` on every chat message.

### What we thought it was first

Suspected the `max_history_tokens` issue from the previous session - nixx-server restarted with
the correct n_ctx (65536) and `_truncate_messages` was now passing the full session history
through (63k token budget instead of the old 6k). Added `max_history_tokens: int = Field(default=16384)`
to `NixxConfig` and plumbed it through `_truncate_messages`. This was a real problem worth fixing,
but it wasn't the cause of the 504s.

### Timeout chain audit

Mapped all timeout values in the stack:

| Layer | Value |
|---|---|
| TUI httpx read | 120s (hard-coded) |
| nixx-server `OpenAIClient` | 120s (hard-coded default) |
| nginx `proxy_read_timeout` on gatekeeper | 300s |

Raised all of them:
- `OpenAIClient.__init__` now takes `timeout: float = 600.0` and builds a split
  `httpx.Timeout(connect=60s, read=600s, write=60s)` so connect/write stay short but prefill
  waits are long
- Added `llm_request_timeout: float = Field(default=600.0)` to `NixxConfig`; passed to
  `OpenAIClient` at instantiation in `server.py`
- TUI `_stream_response` now uses `httpx.Timeout(connect=10s, read=660s, write=30s)` instead
  of a flat 120s
- nginx `proxy_read_timeout` raised to 600s on gatekeeper (`/etc/nginx/sites-enabled/gpt.conf`),
  reloaded

### Actual root cause: WireGuard tunnel was down

After all the above, 504s continued. Checked `gpt.perdrizet.org` directly:

```
$ curl https://gpt.perdrizet.org/health
000 (curl exit 28 - timed out)
```

`ping gpt.perdrizet.org` → 100% packet loss. The IP resolves but no packets reach it.

```
$ sudo systemctl status wg-quick@wg0
Active: failed (Result: exit-code) since Fri 2026-03-27 17:11:20 EDT; 1 day 4h ago
...
wg-quick[2081]: Name or service not known: `perdrizet.org:51820'
wg-quick[2081]: Configuration parsing error
```

`wg-quick@wg0` is enabled and starts at boot, but the WireGuard config uses a hostname
(`perdrizet.org:51820`) as the endpoint. The service started before DNS was available, the
hostname lookup failed, and the tunnel stayed down permanently until manually restarted.

### Fix: drop-in override for boot ordering

```
/etc/systemd/system/wg-quick@wg0.service.d/wait-for-network.conf
```

```ini
[Unit]
After=network-online.target
Wants=network-online.target
```

This makes systemd wait until the network stack reports online before starting the tunnel.
Both `NetworkManager-wait-online.service` and `systemd-networkd-wait-online.service` were already
enabled so `network-online.target` is actually reached.

Restarted the tunnel manually (`sudo systemctl restart wg-quick@wg0`), confirmed handshake and
end-to-end reachability (`curl https://gpt.perdrizet.org/health` → 200).

---

## What's still pending

- All code changes from this session are uncommitted (server.py, config.py, tui/app.py,
  prompts.py, memory/db.py, tests/test_server.py)
- `docs/project-state.md` should be updated to reflect `max_history_tokens`,
  `llm_request_timeout`, and the WireGuard boot pitfall

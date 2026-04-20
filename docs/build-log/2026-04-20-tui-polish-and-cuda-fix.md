# 2026-04-20: TUI polish and CUDA fix

## What got built

A session of small but visible TUI improvements, followed by a recurring infrastructure issue
that finally got a proper fix.

---

## TUI toggle indicators

The recall and intent toggles were rendered as Textual `Switch` widgets that were nearly invisible
against the terminal background - no visible on/off state, and green widgets looked like progress
bars identical to the context bar.

Replaced the `Switch` widgets entirely with `Static` labels that include a Unicode dot indicator:

- `recall ●` / `intent ●` in green when on
- `recall ○` / `intent ○` dimmed when off

The `Switch`-based event handler (`on_switch_changed`) was removed. `action_toggle_recall` and
`action_toggle_intent` now toggle internal `_recall_on` / `_intent_on` state directly and call
`_update_toggle_labels` immediately, then fire the server request as a worker. The server sync
in `_update_summary_bar` compares against those fields and calls `_update_toggle_labels` only
when the state differs.

The `ctrl+i` binding for intent toggle was removed and replaced with `ctrl+t`. `ctrl+i` is
a terminal-level alias for Tab and cannot be captured by Textual - it was causing focus
navigation to fire instead of the toggle action.

---

## Context and summary bar color alignment

`SummaryBar` was using a different color scheme (`dim` / `cyan` / `magenta`) and different
thresholds (50% / 90%) than `ContextBar` (`green` / `yellow` / `red` at 50% / 80%). Both bars
now use identical colors and thresholds: green below 50%, yellow 50-80%, red above 80%.

---

## Context length stuck at 8192

The context bar was showing 8192 (the config default) instead of 65536 after every restart.

Root cause: nixx-server fetches `n_ctx` from the LLM server's `/props` endpoint at startup.
When llamacpp is still loading (or was down at startup time due to the CUDA issue below), that
fetch fails and the server falls back to 8192. A retry existed in the `/health` handler, but
the TUI never calls `/health` - it only reads `context_length` from chat response payloads.
So the retry never fired.

Fix: extracted the retry into a shared `_ensure_n_ctx()` coroutine and called it at the top
of both `/health` and `/v1/chat/completions`. The first chat message or health check after
the LLM server becomes available now picks up the real value.

---

## CUDA libcudart.so.12 - recurring crash

llamacpp was crash-looping on startup with:

```
llama-server: error while loading shared libraries: libcudart.so.12: cannot open shared object file
```

This has happened before. The system has no installed CUDA runtime packages - only a
`cuda-keyring` package. The only copy of `libcudart.so.12` on the machine is bundled inside
Ollama's private library directory at `/usr/local/lib/ollama/cuda_v12/`. Previously the fix was
to add `LD_LIBRARY_PATH=/usr/local/lib/ollama/cuda_v12` to the systemd drop-ins, but this was
not persisted correctly across the last reboot.

Proper fix: registered the directory with the system linker cache:

```
echo "/usr/local/lib/ollama/cuda_v12" | sudo tee /etc/ld.so.conf.d/ollama-cuda12.conf
sudo ldconfig
```

`libcudart.so.12` is now visible to all processes via ldconfig and survives reboots. The
`LD_LIBRARY_PATH` lines were removed from both drop-ins (`llamacpp.service.d/override.conf`
and `nixx-embed.service.d/override.conf`), leaving only `CUDA_VISIBLE_DEVICES=0` in each.

---

## Stray files in repo root

The broken heredoc attempts earlier in the session left three untracked files in the repo root:
`EOF`, `[Service]`, and `Environment=CUDA_VISIBLE_DEVICES=0`. These should be deleted before
committing.

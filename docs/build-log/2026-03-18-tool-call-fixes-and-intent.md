# 2026-03-18: tool call fixes and intent system

## What got built

Continuation of the tool calling work from yesterday. Three main areas: memory tools, intent derivation, and a bug fix for context overflow in multi-turn tool use.

### Memory tools (search_transcript, view_transcript)

Added two new tools that let nixx inspect its own conversation history:

- `search_transcript(query, limit)` - full-text search over the buffer using episodic recall
- `view_transcript(start_id, end_id)` - retrieves a range of buffer entries by ID

Both live in `src/nixx/tools/memory_tools.py`. The `ToolRegistry` now accepts an optional `memory` parameter and registers these tools when it's provided. The server passes `memory=app.state.memory` when creating the registry at startup.

### Intent system

Nixx can now track and automatically derive an "intent" - a first-person summary of what the user is trying to accomplish in the current session.

- Config: `intent_interval` (default 10) and `intent_lookback` (default 10) in `NixxConfig`
- Prompt: `INTENT_DERIVATION_PROMPT` in `prompts.py` asks the LLM to analyze recent exchange and state the intent
- State: `app.state.intent` and `app.state.messages_since_intent`
- Endpoints: `GET/POST/DELETE /v1/intent`, `POST /v1/intent/derive`
- Auto-derivation after every `intent_interval` messages
- Intent injected into system message as `## Current Intent` block
- TUI command: `/intent [text]` to set manually, `/intent` to display current

### Streaming tool call done-signal fix

The LLM backend sends `finish_reason: "tool_calls"` before the final `[DONE]` SSE event. The streaming client was treating this as a terminal signal and breaking before accumulating the tool call data. Fixed by only signaling done for non-tool finishes:

```python
if finish and finish != "tool_calls":
    yield ChatResponse(content="", done=True)
```

Tool calls are now accumulated until `[DONE]`, then emitted as the final chunk.

### Context overflow fix (multi-turn tool use)

After the above were deployed, multi-turn tool use could produce 400 errors from the backend. Each conversation turn is an independent HTTP request with its own tool loop. The overflow happens within a single request when accumulated conversation history is large. The scenario:

1. Turn 1 - user asks to search. LLM calls `search_transcript`, returns a table of results (~400-800 tokens in `assistant` text). This text is stored in `_history`.
2. Turn 2 - user asks to view an entry. The server receives `[system, user1, assistant1_results, user2]`. `_truncate_messages` runs, keeping all of this. The LLM calls `view_transcript`, which appends `[assistant+tool_calls, tool_result]` to the local messages list. The tool result (raw transcript entries) can add another 1000-2000 tokens. The second LLM call within this loop may now exceed the backend's context window.

The root cause was that `_estimate_tokens` used 1 token ≈ 4 chars, but actual tokenization for this content runs closer to 1 token ≈ 2.5-3 chars. This meant `_truncate_messages` was letting through more context than it should, leaving no headroom for the tool loop messages added mid-round.

Fixes:
- Changed estimate from 4 chars/token to 3 chars/token
- Increased `_RESPONSE_RESERVE` from 1024 to 2048 tokens (covers both response generation and tool loop expansion)
- Updated `_truncate_messages` to use `.get("content") or ""` instead of `["content"]` to handle None-content messages safely

Added error logging in `openai_client.py` to log the full messages payload and response body whenever the backend returns 4xx - this will make future backend errors easier to diagnose.

## What didn't work

Could not reproduce the 400 error directly via curl because the LLM consistently chose to answer from its parametric memory rather than invoking the tools, even when explicitly instructed to use them. The fix is based on root-cause analysis of token estimation rather than a direct reproduction.

## Tests

42 tests passing, ruff clean, mypy clean.

"""Nixx API server — OpenAI-compatible endpoint for local LLM inference."""

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from httpx import HTTPError as HttpError
from pydantic import BaseModel

from nixx.config import NixxConfig
from nixx.ingest.pipeline import IngestPipeline
from nixx.llm import OpenAIClient
from nixx.memory.db import (
    count_unsummarized_words,
    create_pool,
    get_buffer_entries,
    get_current_session_entries,
    get_source,
    get_source_content,
    init_schema,
    list_sources,
    list_summaries,
    save_session_marker,
)
from nixx.memory.store import MemoryStore
from nixx.prompts import INTENT_DERIVATION_PROMPT, SYSTEM_PROMPT
from nixx.tools import ToolRegistry

logger = logging.getLogger(__name__)

# Token budget reserved for the LLM response and tool loop expansion.
_RESPONSE_RESERVE = 2048


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 3 characters (conservative overestimate)."""
    return max(1, len(text) // 3)


def _truncate_messages(messages: list[dict[str, Any]], context_length: int) -> list[dict[str, Any]]:
    """Drop oldest conversation messages to fit within the token budget.

    Keeps the system message (index 0) and as many recent messages as fit.
    """
    budget = context_length - _RESPONSE_RESERVE
    if budget <= 0:
        return messages[:1]

    system_tokens = _estimate_tokens(messages[0].get("content") or "") if messages else 0
    remaining = budget - system_tokens
    if remaining <= 0:
        return messages[:1]

    # Walk backwards through conversation messages, accumulating tokens.
    kept: list[dict[str, Any]] = []
    for msg in reversed(messages[1:]):
        msg_tokens = _estimate_tokens(msg.get("content") or "") + 4  # +4 for message framing
        if msg_tokens > remaining:
            break
        remaining -= msg_tokens
        kept.append(msg)

    kept.reverse()
    return [messages[0]] + kept


# ── OpenAI-compatible request models ──────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


class CreateSourceRequest(BaseModel):
    name: str
    start_id: int | None = None
    end_id: int | None = None


class IngestRequest(BaseModel):
    source: str
    name: str | None = None


class CreateSummaryRequest(BaseModel):
    tags: list[str] = []


class EpisodicSearchRequest(BaseModel):
    query: str
    top_k: int = 5


class SetIntentRequest(BaseModel):
    intent: str


# ── App factory ───────────────────────────────────────────────────────────────


def create_app(config: NixxConfig | None = None) -> FastAPI:
    if config is None:
        config = NixxConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        pool = await create_pool(config)
        await init_schema(pool, dimensions=config.embedding_dimensions)
        app.state.memory = MemoryStore(config, pool)
        app.state.ingest = IngestPipeline(config, pool)
        app.state.recall_enabled = True
        app.state.intent_enabled = True
        app.state.tools = ToolRegistry(
            config.scratch_dir, memory=app.state.memory, searxng_url=config.searxng_url
        )
        app.state.intent = None  # Current derived/set intent
        app.state.messages_since_intent = 0  # Counter for automatic derivation

        # Auto-fetch context length from the LLM server's /props endpoint.
        try:
            import httpx as _httpx

            headers = (
                {"Authorization": f"Bearer {config.llm_api_key}"} if config.llm_api_key else {}
            )
            async with _httpx.AsyncClient(timeout=5.0) as _client:
                _resp = await _client.get(f"{config.llm_base_url}/props", headers=headers)
                _resp.raise_for_status()
                _n_ctx = _resp.json().get("default_generation_settings", {}).get("n_ctx")
                if _n_ctx and isinstance(_n_ctx, int) and _n_ctx > 0:
                    config.llm_context_length = _n_ctx
                    print(f"nixx: context length auto-fetched: {_n_ctx}", flush=True)
                else:
                    print(
                        f"nixx: /props returned unexpected n_ctx={_n_ctx!r}, using {config.llm_context_length}",
                        flush=True,
                    )
        except Exception as _exc:
            print(
                f"nixx: could not fetch context length from LLM server ({_exc}), using {config.llm_context_length}",
                flush=True,
            )

        logger.info("Memory store ready")
        logger.info("Tool registry ready (scratch_dir=%s)", config.scratch_dir)
        yield
        await pool.close()

    app = FastAPI(title="nixx", version="0.1.0", lifespan=lifespan)
    llm = OpenAIClient(base_url=config.llm_base_url, api_key=config.llm_api_key)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "model": config.llm_model,
            "context_length": str(config.llm_context_length),
        }

    @app.get("/v1/debug/context")
    async def debug_context() -> dict[str, Any]:
        """Return the last assembled system context sent to the LLM."""
        ctx: dict[str, str | None] = getattr(
            app.state, "last_context", {"base": SYSTEM_PROMPT, "memory": None}
        )
        return ctx

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        request: ChatCompletionRequest,
    ) -> StreamingResponse | dict[str, Any]:
        model = request.model or config.llm_model
        temperature = (
            request.temperature if request.temperature is not None else config.llm_temperature
        )
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        # Build system message: base identity prompt + intent + episodic memory context
        memory: MemoryStore = app.state.memory
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        context_block = ""
        recalled: list[dict] = []
        if last_user and app.state.recall_enabled:
            try:
                recalled = await memory.recall_episodic_for_prompt(
                    last_user, top_k=3, threshold=config.recall_threshold
                )
                context_block = memory.format_episodic_context(recalled)
            except Exception as exc:
                logger.warning("Episodic recall failed (continuing without context): %s", exc)

        # Build intent block if set
        intent_block = ""
        if app.state.intent and app.state.intent_enabled:
            intent_block = f"\n\n## Current Intent\n\n{app.state.intent}"

        system_content = (
            SYSTEM_PROMPT + intent_block + (f"\n\n{context_block}" if context_block else "")
        )
        messages = [{"role": "system", "content": system_content}] + messages
        # Truncate to fit within the LLM context window.
        messages = _truncate_messages(messages, config.llm_context_length)
        prompt_token_estimate = sum(_estimate_tokens(m["content"]) + 4 for m in messages)
        app.state.last_context = {
            "base": SYSTEM_PROMPT,
            "intent": app.state.intent,
            "memory": context_block or None,
            "hits": [
                {
                    "content": r["content"],
                    "similarity": round(float(r["similarity"]), 3),
                    "tags": r.get("tags", []),
                }
                for r in recalled
            ],
            "token_usage": {
                "prompt_tokens": prompt_token_estimate,
                "context_length": config.llm_context_length,
            },
        }

        if request.stream:
            return StreamingResponse(
                _chat_event_stream(
                    llm,
                    model,
                    messages,
                    temperature,
                    request.max_tokens,
                    completion_id,
                    created,
                    memory=memory,
                    user_text=last_user,
                    tools=app.state.tools,
                    app=app,
                    config=config,
                ),
                media_type="text/event-stream",
            )

        # Non-streaming with tool execution loop
        tools = app.state.tools
        tool_defs = tools.to_openai_tools()
        max_tool_rounds = 5  # Prevent infinite loops

        for _ in range(max_tool_rounds):
            try:
                result = await llm.chat(
                    model, messages, temperature, request.max_tokens, tools=tool_defs
                )
            except HttpError as exc:
                raise HTTPException(status_code=502, detail=f"LLM backend error: {exc}") from exc

            # If no tool calls, we're done
            if not result.tool_calls:
                break

            # Execute tool calls and append results
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": result.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in result.tool_calls
                ],
            }
            messages.append(assistant_msg)
            for tc in result.tool_calls:
                tool_result = await tools.execute(tc.name, tc.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result.to_content(),
                    }
                )

        content = result.content

        # Persist the exchange to the buffer.
        if last_user:
            try:
                await memory.save_to_buffer("user", last_user)
                if content:
                    await memory.save_to_buffer("assistant", content)
            except Exception as exc:
                logger.warning("Buffer write failed: %s", exc)

        # Increment message counter and check for intent derivation
        app.state.messages_since_intent += 1
        if app.state.messages_since_intent >= config.intent_interval:
            try:
                await _derive_intent(app, llm, config)
            except Exception as exc:
                logger.warning("Intent derivation failed: %s", exc)

        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.prompt_tokens + result.completion_tokens,
            },
        }

    @app.post("/v1/ingest")
    async def ingest(request: IngestRequest) -> dict:
        """Ingest a file path or URL into sources + memories."""
        pipeline: IngestPipeline = app.state.ingest
        try:
            return await pipeline.ingest(request.source, name=request.name)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/sources")
    async def create_source(request: CreateSourceRequest) -> dict:
        """Mark a buffer range as a source, generate a summary, and index it in memories."""
        mem: MemoryStore = app.state.memory
        try:
            return await mem.create_source(
                name=request.name,
                start_id=request.start_id,
                end_id=request.end_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/sources")
    async def get_sources(name: str | None = None) -> dict:
        """List all sources, optionally filtered by name."""
        pool = app.state.memory._pool
        sources = await list_sources(pool, name_filter=name)
        return {"sources": sources, "count": len(sources)}

    @app.get("/v1/sources/{source_id}")
    async def get_source_by_id(source_id: int) -> dict:
        """Get a single source by ID."""
        pool = app.state.memory._pool
        source = await get_source(pool, source_id)
        if not source:
            raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
        return source

    @app.get("/v1/sources/{source_id}/content")
    async def get_source_content_by_id(source_id: int) -> dict:
        """Get all memory chunks for a source, ordered by chunk index."""
        pool = app.state.memory._pool
        # First verify source exists
        source = await get_source(pool, source_id)
        if not source:
            raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
        chunks = await get_source_content(pool, source_id)
        return {
            "source_id": source_id,
            "source_name": source["name"],
            "source_type": source["type"],
            "chunks": chunks,
            "total_chunks": len(chunks),
        }

    @app.get("/v1/buffer/session")
    async def buffer_session() -> dict:
        """Return buffer entries for the current session (after last marker)."""
        pool = app.state.memory._pool
        entries = await get_current_session_entries(pool)
        return {
            "entries": [{"role": e["role"], "content": e["content"]} for e in entries],
            "count": len(entries),
        }

    @app.post("/v1/buffer/clear")
    async def buffer_clear() -> dict:
        """Write a session marker to the buffer, starting a new session."""
        pool = app.state.memory._pool
        marker_id = await save_session_marker(pool)
        return {"marker_id": marker_id}

    # ── Episodic memory endpoints ─────────────────────────────────────────

    @app.get("/v1/episodic/status")
    async def episodic_status() -> dict:
        """Check whether a summary is due."""
        mem: MemoryStore = app.state.memory
        due = await mem.check_summary_due()
        words, _, _ = await count_unsummarized_words(mem._pool)
        return {
            "summary_due": due,
            "current_words": words,
            "interval_words": config.summary_interval,
            "recall_enabled": app.state.recall_enabled,
            "recall_threshold": config.recall_threshold,
            "intent_enabled": app.state.intent_enabled,
        }

    @app.post("/v1/episodic/config")
    async def update_episodic_config(request: dict) -> dict:
        """Update episodic memory configuration at runtime."""
        if "interval_words" in request:
            val = int(request["interval_words"])
            if val < 1:
                raise HTTPException(status_code=400, detail="interval_words must be >= 1")
            config.summary_interval = val
        if "recall_enabled" in request:
            app.state.recall_enabled = bool(request["recall_enabled"])
        if "recall_threshold" in request:
            val_f = float(request["recall_threshold"])
            if not 0.0 <= val_f <= 1.0:
                raise HTTPException(status_code=400, detail="recall_threshold must be 0.0–1.0")
            config.recall_threshold = val_f
        if "intent_enabled" in request:
            app.state.intent_enabled = bool(request["intent_enabled"])
        return {
            "interval_words": config.summary_interval,
            "recall_enabled": app.state.recall_enabled,
            "recall_threshold": config.recall_threshold,
            "intent_enabled": app.state.intent_enabled,
        }

    @app.post("/v1/episodic/summary")
    async def create_episode_summary(request: CreateSummaryRequest) -> dict:
        """Create an episodic summary of unsummarized buffer entries."""
        mem: MemoryStore = app.state.memory
        try:
            return await mem.create_episode_summary(tags=request.tags)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/episodic/search")
    async def episodic_search(request: EpisodicSearchRequest) -> dict:
        """Search episodic memory (summaries + buffer full-text)."""
        mem: MemoryStore = app.state.memory
        results = await mem.recall_episodic(request.query, top_k=request.top_k)
        return {"results": results, "count": len(results)}

    @app.get("/v1/episodic/transcript")
    async def episodic_transcript(start_id: int, end_id: int) -> dict:
        """Return buffer entries for a given range (for expanding summary context)."""
        pool = app.state.memory._pool
        entries = await get_buffer_entries(pool, start_id, end_id)
        entries = [e for e in entries if e["role"] != "marker"]
        return {
            "entries": [
                {"id": e["id"], "role": e["role"], "content": e["content"]} for e in entries
            ],
            "count": len(entries),
        }

    @app.get("/v1/episodic/summaries")
    async def get_episodic_summaries() -> dict:
        """List all episodic summaries."""
        pool = app.state.memory._pool
        summaries = await list_summaries(pool)
        return {"summaries": summaries, "count": len(summaries)}

    # ── Intent endpoints ──────────────────────────────────────────────────────

    @app.get("/v1/intent")
    async def get_intent() -> dict:
        """Get the current intent/motivation."""
        return {
            "intent": app.state.intent,
            "messages_since_derivation": app.state.messages_since_intent,
        }

    @app.post("/v1/intent")
    async def set_intent(request: SetIntentRequest) -> dict:
        """Set the intent/motivation manually."""
        app.state.intent = request.intent
        app.state.messages_since_intent = 0  # Reset counter
        logger.info("Intent set manually: %s", request.intent[:100])
        return {"intent": app.state.intent}

    @app.delete("/v1/intent")
    async def clear_intent() -> dict:
        """Clear the current intent."""
        app.state.intent = None
        app.state.messages_since_intent = 0
        return {"intent": None}

    @app.post("/v1/intent/derive")
    async def derive_intent_endpoint() -> dict:
        """Manually trigger intent derivation."""
        await _derive_intent(app, llm, config)
        return {
            "intent": app.state.intent,
            "messages_since_derivation": app.state.messages_since_intent,
        }

    return app


# ── Intent derivation ─────────────────────────────────────────────────────────


async def _derive_intent(app: FastAPI, llm: OpenAIClient, config: NixxConfig) -> None:
    """Derive intent from recent conversation by asking the LLM to analyze it."""
    memory: MemoryStore = app.state.memory
    pool = memory._pool

    # Get recent buffer entries
    entries = await get_current_session_entries(pool, limit=config.intent_lookback)
    if len(entries) < 2:
        logger.info("Not enough messages to derive intent")
        return

    # Format as exchange
    exchange_lines = []
    for e in entries:
        role = "User" if e["role"] == "user" else "Assistant"
        content = e["content"][:500]  # Truncate long messages
        if len(e["content"]) > 500:
            content += "..."
        exchange_lines.append(f"{role}: {content}")

    exchange = "\n\n".join(exchange_lines)

    # Call LLM to derive intent (simple prompt, no tools, no recall)
    prompt = INTENT_DERIVATION_PROMPT.format(exchange=exchange)
    messages = [{"role": "user", "content": prompt}]

    try:
        result = await llm.chat(
            config.llm_model,
            messages,
            temperature=0.3,  # Lower temperature for more focused response
            max_tokens=150,
        )
        intent = result.content.strip()
        if intent:
            app.state.intent = intent
            app.state.messages_since_intent = 0
            logger.info("Intent derived: %s", intent[:100])
    except Exception as exc:
        logger.warning("Failed to derive intent: %s", exc)


# ── Streaming helpers ─────────────────────────────────────────────────────────


async def _chat_event_stream(
    llm: OpenAIClient,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int | None,
    completion_id: str,
    created: int,
    memory: MemoryStore | None = None,
    user_text: str = "",
    tools: ToolRegistry | None = None,
    app: FastAPI | None = None,
    config: NixxConfig | None = None,
) -> AsyncGenerator[str, None]:
    accumulated = ""
    tool_defs = tools.to_openai_tools() if tools else None
    max_tool_rounds = 5

    for _ in range(max_tool_rounds):
        pending_tool_calls: list[dict[str, Any]] = []

        try:
            async for chunk in llm.chat_stream(
                model, messages, temperature, max_tokens, tools=tool_defs
            ):
                content = chunk.content
                done = chunk.done

                if content:
                    accumulated += content
                    data = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": content},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(data)}\n\n"

                # Collect tool calls from final chunk
                if done and chunk.tool_calls:
                    pending_tool_calls = [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in chunk.tool_calls
                    ]
                    break

                if done and not chunk.tool_calls:
                    # No tool calls, finish streaming
                    data = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                    yield f"data: {json.dumps(data)}\n\n"
                    break

        except Exception as exc:
            msg = str(exc) or f"{type(exc).__name__} (no message)"
            error = {"error": {"message": msg, "type": "server_error"}}
            yield f"data: {json.dumps(error)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # If no tool calls, we're done with the loop
        if not pending_tool_calls:
            break

        # Execute tool calls
        if tools:
            messages.append(
                {
                    "role": "assistant",
                    "content": accumulated,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["arguments"]},
                        }
                        for tc in pending_tool_calls
                    ],
                }
            )
            for tc in pending_tool_calls:
                logger.info("Executing tool: %s", tc["name"])
                # Signal tool execution to the client
                yield f"data: {json.dumps({'tool_call': {'name': tc['name']}})}\n\n"
                tool_result = await tools.execute(tc["name"], tc["arguments"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result.to_content(),
                    }
                )
            # Clear accumulated for next round
            accumulated = ""

    # Write to buffer BEFORE yielding [DONE]
    if memory is not None:
        try:
            if user_text:
                await memory.save_to_buffer("user", user_text)
            if accumulated:
                await memory.save_to_buffer("assistant", accumulated)
        except Exception as exc:
            logger.warning("Buffer write failed: %s", exc)

    # Increment message counter and check for intent derivation
    if app is not None and config is not None:
        app.state.messages_since_intent += 1
        if app.state.messages_since_intent >= config.intent_interval:
            try:
                await _derive_intent(app, llm, config)
            except Exception as exc:
                logger.warning("Intent derivation failed: %s", exc)

    yield "data: [DONE]\n\n"

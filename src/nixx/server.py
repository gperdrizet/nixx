"""Nixx API server — OpenAI-compatible endpoint for local LLM inference."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from httpx import HTTPError as HttpError
from pydantic import BaseModel, ConfigDict

from nixx.config import NixxConfig, _NIXX_ROOT
from nixx.ingest.pipeline import IngestPipeline
from nixx.llm import OpenAIClient
from nixx.memory.db import (
    count_unsummarized_words,
    create_pool,
    delete_buffer_tail,
    get_buffer_entries,
    get_current_session_entries,
    get_state,
    get_source,
    get_source_content,
    init_schema,
    list_sources,
    list_summaries,
    save_session_marker,
    set_state,
)
from nixx.memory.store import MemoryStore
from nixx.prompts import INTENT_DERIVATION_PROMPT, SYSTEM_PROMPT
from nixx.tools import ToolRegistry
from nixx.tools.permissions import get_project_dir, set_project_dir
from nixx.tools.planning import get_current_plan

logger = logging.getLogger(__name__)

DEFAULT_INTENT = (
    "I want to get better at AI/ML systems engineering by building something that actually has to work. "
    "The memory system, the inference stack, the tooling - this is real understanding I'm accumulating, "
    "not tutorial exercises. I want this to be a body of work worth pointing at."
)

# Token budget reserved for the LLM response and tool loop expansion.
_RESPONSE_RESERVE = 2048


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ≈ 3 characters (conservative overestimate)."""
    return max(1, len(text) // 3)


def _truncate_messages(
    messages: list[dict[str, Any]], context_length: int, max_history_tokens: int | None = None
) -> list[dict[str, Any]]:
    """Drop oldest conversation messages to fit within the token budget.

    Keeps the system message (index 0) and as many recent messages as fit.
    max_history_tokens caps conversation history independently of context_length.
    """
    budget = context_length - _RESPONSE_RESERVE
    if max_history_tokens is not None:
        budget = min(budget, max_history_tokens)
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


def _strip_trailing_empty_assistant(messages: list[dict[str, Any]]) -> None:
    """Drop invalid assistant-prefill tails for llama.cpp reasoning mode.

    Some backends reject requests that end with an assistant message containing
    empty content and no tool_calls.
    """
    while messages:
        last = messages[-1]
        if last.get("role") != "assistant":
            return
        has_tool_calls = bool(last.get("tool_calls"))
        content = str(last.get("content") or "").strip()
        if has_tool_calls or content:
            return
        messages.pop()


# ── Context assembly ─────────────────────────────────────────────────────────


async def _assemble_messages(
    raw_messages: list[dict[str, Any]],
    app: FastAPI,
    config: NixxConfig,
    memory: MemoryStore,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Build the fully-assembled, truncated message list for an LLM call.

    Returns (messages, recalled) where recalled is the raw episodic hits list,
    needed by callers that want to populate debug/context state.
    """
    last_user = next((m["content"] for m in reversed(raw_messages) if m["role"] == "user"), "")
    recalled: list[dict] = []
    context_block = ""
    if last_user and getattr(app.state, "recall_enabled", True):
        try:
            recalled = await memory.recall_episodic_for_prompt(
                last_user, top_k=3, threshold=config.recall_threshold
            )
            context_block = memory.format_episodic_context(recalled)
        except Exception as exc:
            logger.warning("Episodic recall failed (continuing without context): %s", exc)

    intent_block = ""
    if getattr(app.state, "intent", None) and getattr(app.state, "intent_enabled", True):
        intent_block = f"\n\n## Current intent\n\n{app.state.intent}"

    plan_block = ""
    plan_content = get_current_plan(config.scratch_dir)
    if plan_content:
        plan_block = f"\n\n## Current plan\n\n{plan_content}"

    from nixx.config import _NIXX_ROOT

    file_access_block = (
        "\n\n## File access\n\nYou can read and write files in any of these directories:\n"
    )
    file_access_block += f"\n- Scratch: {config.scratch_dir}"
    file_access_block += f"\n- Source (nixx): {_NIXX_ROOT}"
    if getattr(app.state, "project_dir", None):
        file_access_block += f"\n- Project: {app.state.project_dir}"
    else:
        file_access_block += (
            "\n\nNo project directory is currently set (use /project <dir> in the TUI to set one)."
        )

    tools_block = ""
    tools_registry = getattr(app.state, "tools", None)
    if tools_registry:
        tool_lines = []
        for tool in tools_registry.list_tools():
            first_sentence = tool.description.split(". ")[0].rstrip(".")
            tool_lines.append(f"- {tool.name}: {first_sentence}")
        if tool_lines:
            tools_block = (
                "\n\n## Available tools\n\n"
                "These tools are callable by you only - the user cannot invoke them. "
                "Do not suggest that the user call a tool or check a tool result themselves.\n\n"
                + "\n".join(tool_lines)
            )

    # Runtime facts: inject actual config values so the model doesn't guess.
    _image_svc_running = False
    try:
        async with httpx.AsyncClient(timeout=1.0) as _ic:
            _ih = await _ic.get("http://127.0.0.1:8090/health")
            if _ih.status_code == 200:
                _image_svc_running = True
    except Exception:
        pass

    runtime_block = (
        f"\n\n## Runtime configuration\n\n"
        f"Model: {config.llm_model}\n"
        f"Context window: {config.llm_context_length} tokens\n"
        f"Response reserve: {_RESPONSE_RESERVE} tokens (reserved for your reply)\n"
        f"Scratch directory: {config.scratch_dir}\n"
        f"Source directory: {_NIXX_ROOT}\n"
        f"Image service: {'running' if _image_svc_running else 'not running'}"
    )

    # system_base is the full assembled prompt minus recalled memory context.
    # Stored on app.state so debug_context can return it without re-assembling.
    system_base = (
        SYSTEM_PROMPT + runtime_block + intent_block + plan_block + file_access_block + tools_block
    )
    app.state.assembled_system_prompt = system_base

    system_content = system_base + (f"\n\n{context_block}" if context_block else "")
    messages = [{"role": "system", "content": system_content}] + raw_messages
    messages = _truncate_messages(messages, config.llm_context_length, config.max_history_tokens)
    return messages, recalled


# ── OpenAI-compatible request models ──────────────────────────────────────────


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str | None = None


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


class EpisodicSearchRequest(BaseModel):
    query: str
    top_k: int = 5


class SetIntentRequest(BaseModel):
    intent: str


class ProjectDirRequest(BaseModel):
    directory: str


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
        # Load project directory from persistent state
        project_dir = await get_project_dir(pool)
        app.state.tools.set_project_dir(project_dir)
        app.state.project_dir = project_dir

        app.state.intent = await get_state(pool, "intent") or DEFAULT_INTENT
        app.state.messages_since_intent = 0  # Counter for automatic derivation
        _tool_usage_raw = await get_state(pool, "tool_usage")
        app.state.tool_usage = cast(
            dict[str, int],
            json.loads(_tool_usage_raw) if _tool_usage_raw else {},
        )

        # Auto-fetch context length from the LLM server's /props endpoint.
        app.state.n_ctx_fetched = False
        try:
            headers = (
                {"Authorization": f"Bearer {config.llm_api_key}"} if config.llm_api_key else {}
            )
            async with httpx.AsyncClient(timeout=5.0) as client:
                _resp = await client.get(f"{config.llm_base_url}/props", headers=headers)
                _resp.raise_for_status()
                _n_ctx = _resp.json().get("default_generation_settings", {}).get("n_ctx")
                if _n_ctx and isinstance(_n_ctx, int) and _n_ctx > 0:
                    config.llm_context_length = _n_ctx
                    app.state.n_ctx_fetched = True
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
    llm = OpenAIClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        timeout=config.llm_request_timeout,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        # Retry /props fetch if it failed at startup (e.g. LLM server wasn't ready yet).
        await _ensure_n_ctx()
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

    @app.get("/v1/debug/tool-usage")
    async def debug_tool_usage() -> dict[str, Any]:
        """Return per-tool call counts since server start."""
        return {"tool_usage": getattr(app.state, "tool_usage", {})}

    @app.get("/v1/image/preview")
    async def image_preview(path: str) -> Any:
        """Serve an image file from scratch_dir for the crop UI."""
        from fastapi.responses import FileResponse

        resolved = Path(path).resolve()
        scratch = Path(config.scratch_dir).resolve()
        if not str(resolved).startswith(str(scratch)):
            raise HTTPException(status_code=403, detail="Path outside scratch_dir")
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(resolved)

    @app.post("/v1/image/crop")
    async def image_crop(body: dict[str, Any]) -> dict[str, str]:
        """Crop an image and save to a temp file. Returns {cropped_path}."""
        import tempfile
        from PIL import Image as _Image, ImageOps as _ImageOps  # type: ignore[import]

        path = body.get("path", "")
        x = int(body.get("x", 0))
        y = int(body.get("y", 0))
        size = int(body.get("size", 0))
        if not path or size <= 0:
            raise HTTPException(status_code=400, detail="path and size required")

        resolved = Path(path).resolve()
        scratch = Path(config.scratch_dir).resolve()
        if not str(resolved).startswith(str(scratch)):
            raise HTTPException(status_code=403, detail="Path outside scratch_dir")
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")

        img = _ImageOps.exif_transpose(_Image.open(resolved).convert("RGB"))
        cropped = img.crop((x, y, x + size, y + size))
        suffix = resolved.suffix or ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=scratch / "images")
        tmp.close()
        cropped.save(tmp.name)
        return {"cropped_path": tmp.name}

    @app.get("/v1/image/generate-model")
    async def get_generate_model() -> dict[str, str]:
        """Return the active image generation model from nixx-image."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get("http://127.0.0.1:8090/generate-model")
                r.raise_for_status()
                return cast(dict[str, str], r.json())
        except Exception:
            return {"model": "sd21"}

    @app.post("/v1/image/generate-model")
    async def set_generate_model(body: dict[str, Any]) -> dict[str, Any]:
        """Set the active image generation model on nixx-image."""
        model = body.get("model", "")
        if model not in ("sd14", "sd21", "sdxl", "sdxl_turbo"):
            raise HTTPException(
                status_code=400, detail="model must be one of: sd14, sd21, sdxl, sdxl_turbo"
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                try:
                    await client.get("http://127.0.0.1:8090/health")
                except Exception:
                    import asyncio as _asyncio

                    proc = await _asyncio.create_subprocess_exec(
                        "sudo",
                        "systemctl",
                        "start",
                        "nixx-image",
                        stdout=_asyncio.subprocess.DEVNULL,
                        stderr=_asyncio.subprocess.DEVNULL,
                    )
                    await proc.communicate()
                r = await client.post(
                    "http://127.0.0.1:8090/generate-model",
                    json={"model": model},
                    timeout=10.0,
                )
                r.raise_for_status()
                return cast(dict[str, Any], r.json())
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/v1/image/edit-model")
    async def get_edit_model() -> dict[str, str]:
        """Return the active image edit model from nixx-image."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get("http://127.0.0.1:8090/edit_model")
                r.raise_for_status()
                return cast(dict[str, str], r.json())
        except Exception:
            return {"model": "ip2p"}  # default if service not running

    @app.post("/v1/image/edit-model")
    async def set_edit_model(body: dict[str, Any]) -> dict[str, Any]:
        """Set the active image edit model on nixx-image."""
        model = body.get("model", "")
        if model not in ("ip2p", "magic_brush", "sdxl_edit", "kontext"):
            raise HTTPException(
                status_code=400,
                detail="model must be one of: ip2p, magic_brush, sdxl_edit, kontext",
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Start the service if it isn't running yet
                try:
                    await client.get("http://127.0.0.1:8090/health")
                except Exception:
                    import asyncio as _asyncio

                    proc = await _asyncio.create_subprocess_exec(
                        "sudo",
                        "systemctl",
                        "start",
                        "nixx-image",
                        stdout=_asyncio.subprocess.DEVNULL,
                        stderr=_asyncio.subprocess.DEVNULL,
                    )
                    await proc.communicate()
                r = await client.post(
                    "http://127.0.0.1:8090/edit_model",
                    json={"model": model},
                    timeout=10.0,
                )
                r.raise_for_status()
                return cast(dict[str, Any], r.json())
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def _ensure_n_ctx() -> None:
        """Retry fetching n_ctx from the LLM server if the startup attempt failed."""
        if getattr(app.state, "n_ctx_fetched", True):
            return
        try:
            _headers = (
                {"Authorization": f"Bearer {config.llm_api_key}"} if config.llm_api_key else {}
            )
            async with httpx.AsyncClient(timeout=5.0) as _client:
                _r = await _client.get(f"{config.llm_base_url}/props", headers=_headers)
                _r.raise_for_status()
                _n = _r.json().get("default_generation_settings", {}).get("n_ctx")
                if _n and isinstance(_n, int) and _n > 0:
                    config.llm_context_length = _n
                    app.state.n_ctx_fetched = True
                    logger.info("nixx: context length fetched on demand: %d", _n)
        except Exception:
            pass

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(
        request: ChatCompletionRequest,
    ) -> StreamingResponse | dict[str, Any]:
        model = request.model or config.llm_model
        temperature = (
            request.temperature if request.temperature is not None else config.llm_temperature
        )
        raw_messages: list[dict[str, Any]] = []
        for m in request.messages:
            msg = m.model_dump(exclude_none=True)
            # Keep compatibility with backends expecting a content field.
            if "content" not in msg:
                msg["content"] = ""
            raw_messages.append(msg)
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        memory: MemoryStore = app.state.memory
        messages, recalled = await _assemble_messages(raw_messages, app, config, memory)
        last_user = next(
            ((m.get("content") or "") for m in reversed(raw_messages) if m.get("role") == "user"),
            "",
        )
        context_block = memory.format_episodic_context(recalled) if recalled else ""
        prompt_token_estimate = sum(_estimate_tokens(m["content"]) + 4 for m in messages)
        app.state.last_context = {
            "system_prompt": getattr(app.state, "assembled_system_prompt", SYSTEM_PROMPT),
            "memory": context_block or None,
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
        import time as _time

        tools = app.state.tools
        tool_defs = tools.to_openai_tools()
        max_tool_rounds = 10  # Prevent infinite loops
        ns_tool_call_count = 0
        ns_start = _time.monotonic()

        for _ in range(max_tool_rounds):
            _strip_trailing_empty_assistant(messages)
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
            ns_tool_call_count += len(result.tool_calls)

        content = result.content

        # If the loop ended after tool calls but the model never produced text,
        # do one final call without tools to force a prose response.
        if not content and result.tool_calls is not None:
            try:
                result = await llm.chat(
                    model, messages, temperature, request.max_tokens, tools=None
                )
                content = result.content
            except HttpError as exc:
                raise HTTPException(status_code=502, detail=f"LLM backend error: {exc}") from exc

        # Persist the exchange to the buffer.
        if last_user:
            try:
                ns_elapsed_ms = int((_time.monotonic() - ns_start) * 1000)
                await memory.save_to_buffer("user", last_user)
                if content:
                    await memory.save_to_buffer(
                        "assistant",
                        content,
                        prompt_tokens=result.prompt_tokens or None,
                        completion_tokens=result.completion_tokens or None,
                        latency_ms=ns_elapsed_ms,
                        tool_calls_made=ns_tool_call_count if ns_tool_call_count else None,
                    )
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

    @app.delete("/v1/buffer/session/tail")
    async def buffer_trim(keep: int = 0) -> dict:
        """Delete session buffer entries beyond `keep` (oldest N kept)."""
        if keep < 0:
            raise HTTPException(status_code=400, detail="keep must be >= 0")
        pool = app.state.memory._pool
        deleted = await delete_buffer_tail(pool, keep)
        return {"deleted": deleted}

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
    async def create_episode_summary() -> dict:
        """Create an episodic summary of unsummarized buffer entries."""
        mem: MemoryStore = app.state.memory
        try:
            return await mem.create_episode_summary()
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
        await set_state(app.state.memory._pool, "intent", request.intent)
        logger.info("Intent set manually: %s", request.intent[:100])
        return {"intent": app.state.intent}

    @app.delete("/v1/intent")
    async def clear_intent() -> dict:
        """Clear the current intent."""
        app.state.intent = DEFAULT_INTENT
        app.state.messages_since_intent = 0
        await set_state(app.state.memory._pool, "intent", DEFAULT_INTENT)
        return {"intent": app.state.intent}

    @app.post("/v1/intent/derive")
    async def derive_intent_endpoint() -> dict:
        """Manually trigger intent derivation."""
        await _derive_intent(app, llm, config)
        return {
            "intent": app.state.intent,
            "messages_since_derivation": app.state.messages_since_intent,
        }

    # ── Project directory endpoints ──────────────────────────────────────

    @app.get("/v1/project")
    async def get_project() -> dict:
        """Get the current project directory."""
        return {
            "scratch_dir": str(config.scratch_dir),
            "project_dir": app.state.project_dir,
        }

    @app.post("/v1/project")
    async def set_project(request: ProjectDirRequest) -> dict:
        """Set the project directory."""
        pool = app.state.memory._pool
        path = Path(request.directory).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
        project_dir = await set_project_dir(pool, str(path))
        app.state.project_dir = project_dir
        app.state.tools.set_project_dir(project_dir)
        return {"project_dir": project_dir}

    @app.delete("/v1/project")
    async def clear_project() -> dict:
        """Clear the project directory."""
        pool = app.state.memory._pool
        await set_project_dir(pool, None)
        app.state.project_dir = None
        app.state.tools.set_project_dir(None)
        return {"project_dir": None}

    # ── File browser endpoints ────────────────────────────────────────────────

    def _file_entry(p: Path, base: Path) -> dict[str, Any]:
        stat = p.stat()
        return {
            "name": p.name,
            "path": str(p.relative_to(base)),
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "is_dir": p.is_dir(),
        }

    @app.get("/v1/image/jobs")
    async def image_jobs_proxy() -> dict:
        """Proxy to nixx-image job list. Returns empty list if service is down."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get("http://127.0.0.1:8090/jobs")
                return cast(dict[str, Any], r.json())
        except Exception:
            return {"jobs": []}

    @app.get("/v1/files")
    async def list_files(subdir: str = "") -> dict:
        """List files in the scratch directory (optionally a subdirectory)."""
        base = config.scratch_dir.resolve()
        target = (base / subdir).resolve() if subdir else base
        if not str(target).startswith(str(base)):
            raise HTTPException(status_code=400, detail="Path outside scratch directory")
        if not target.exists():
            raise HTTPException(status_code=404, detail="Directory not found")
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        return {
            "directory": str(target.relative_to(base)) if target != base else "",
            "entries": [_file_entry(p, base) for p in entries],
        }

    @app.get("/v1/files/download")
    async def download_file(path: str) -> Any:
        """Download a file from the scratch directory."""
        from fastapi.responses import FileResponse

        base = config.scratch_dir.resolve()
        target = (base / path).resolve()
        if not str(target).startswith(str(base)):
            raise HTTPException(status_code=400, detail="Path outside scratch directory")
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(target, filename=target.name)

    @app.delete("/v1/files")
    async def delete_file(path: str) -> dict:
        """Delete a file from the scratch directory."""
        import os

        base = config.scratch_dir.resolve()
        target = (base / path).resolve()
        if not str(target).startswith(str(base)):
            raise HTTPException(status_code=400, detail="Path outside scratch directory")
        if not target.exists():
            raise HTTPException(status_code=404, detail="File not found")
        if target.is_dir():
            raise HTTPException(status_code=400, detail="Use directory deletion endpoint")
        os.remove(target)
        return {"deleted": path}

    # Mount PWA - must be last (catch-all)
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        app.mount("/app", StaticFiles(directory=web_dir, html=True), name="web")

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
            temperature=0.6,
            max_tokens=800,
        )
        intent = result.content.strip()
        if intent:
            app.state.intent = intent
            app.state.messages_since_intent = 0
            await set_state(app.state.memory._pool, "intent", intent)
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
    import time

    accumulated = ""
    tool_defs = tools.to_openai_tools() if tools else None
    max_tool_rounds = 10
    recent_tool_names: list[str] = []  # for stuck-loop detection
    tool_calls_made = False
    tool_call_count = 0
    loop_exit_reason = "natural"  # "natural" | "stuck" | "max_rounds"
    stream_start = time.monotonic()
    stream_prompt_tokens = 0
    stream_completion_tokens = 0

    # --- Resume detection: if the last non-system message is an assistant turn
    # with tool_calls, the user approved the tool plan and we skip first-pass
    # inference and go straight to execution. ---
    last_conv = next((m for m in reversed(messages) if m["role"] != "system"), None)
    is_resume = (
        last_conv is not None
        and last_conv.get("role") == "assistant"
        and last_conv.get("tool_calls")
    )

    if is_resume:
        # Extract pending tool calls from the assistant turn the TUI appended.
        raw_tcs = last_conv["tool_calls"]  # type: ignore[index]
        pending_tool_calls_initial = [
            {
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": tc["function"]["arguments"],
            }
            for tc in raw_tcs
        ]
        first_pass_pending = pending_tool_calls_initial
    else:
        first_pass_pending = []

    for round_idx in range(max_tool_rounds):
        pending_tool_calls: list[dict[str, Any]] = []

        # On the first iteration of a resume, skip inference and use the
        # tool_calls that were already decided in the previous request.
        if round_idx == 0 and first_pass_pending:
            pending_tool_calls = first_pass_pending
        else:
            reasoning_acc = ""
            try:
                _strip_trailing_empty_assistant(messages)
                async for chunk in llm.chat_stream(
                    model, messages, temperature, max_tokens, tools=tool_defs
                ):
                    content = chunk.content
                    done = chunk.done

                    if chunk.reasoning:
                        reasoning_acc += chunk.reasoning

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
                        if chunk.prompt_tokens:
                            stream_prompt_tokens = chunk.prompt_tokens
                        if chunk.completion_tokens:
                            stream_completion_tokens = chunk.completion_tokens
                        pending_tool_calls = [
                            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                            for tc in chunk.tool_calls
                        ]
                        # Pause for approval only if any tool is a self-edit:
                        # validate_and_commit, or a write/edit/delete targeting
                        # the nixx source tree. Everything else runs unattended.
                        _SELF_EDIT_TOOLS = {
                            "validate_and_commit",
                            "write_file",
                            "edit_file",
                            "delete_file",
                        }
                        _nixx_src = str(_NIXX_ROOT)

                        def _is_self_edit(tc: dict[str, Any]) -> bool:
                            if tc["name"] == "validate_and_commit":
                                return True
                            if tc["name"] in ("write_file", "edit_file", "delete_file"):
                                try:
                                    args = (
                                        json.loads(tc["arguments"])
                                        if isinstance(tc["arguments"], str)
                                        else tc["arguments"]
                                    )
                                    path = args.get("path", "") or args.get("file_path", "")
                                    return str(path).startswith(_nixx_src)
                                except Exception:
                                    return True  # can't parse - be safe
                            return False

                        if any(_is_self_edit(tc) for tc in pending_tool_calls):
                            tool_names = [tc["name"] for tc in pending_tool_calls]
                            yield f"data: {json.dumps({'approval_needed': {'tools': tool_names, 'reasoning': reasoning_acc, 'tool_calls': [{'id': tc['id'], 'type': 'function', 'function': {'name': tc['name'], 'arguments': tc['arguments']}} for tc in pending_tool_calls]}})}\n\n"
                            yield "data: [PAUSE]\n\n"
                            return
                        # Pause for square crop if edit_image tool has a non-square image
                        for tc in pending_tool_calls:
                            if tc["name"] == "edit_image":
                                try:
                                    from PIL import Image as _PILImage

                                    tc_args = (
                                        json.loads(tc["arguments"])
                                        if isinstance(tc["arguments"], str)
                                        else tc["arguments"]
                                    )
                                    img_path = tc_args.get("input_path", "")
                                    if img_path and Path(img_path).exists():
                                        with _PILImage.open(img_path) as _img:
                                            _w, _h = _img.size
                                        if _w != _h:
                                            yield f"data: {json.dumps({'crop_needed': {'path': img_path, 'width': _w, 'height': _h, 'tool_calls': [{'id': tc['id'], 'type': 'function', 'function': {'name': tc['name'], 'arguments': tc['arguments']}} for tc in pending_tool_calls]}})}\n\n"
                                            yield "data: [PAUSE]\n\n"
                                            return
                                except Exception:
                                    pass  # if we can't check, just let it proceed
                        break

                    if done and not chunk.tool_calls:
                        if chunk.prompt_tokens:
                            stream_prompt_tokens = chunk.prompt_tokens
                        if chunk.completion_tokens:
                            stream_completion_tokens = chunk.completion_tokens
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

        # If no tool calls, we're done with the loop.
        if not pending_tool_calls:
            break

        # Execute tool calls.
        if tools:
            # On the resume round (round_idx == 0, is_resume), the TUI already
            # appended the assistant tool_calls turn to messages - don't re-append.
            if not (round_idx == 0 and is_resume):
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
                yield f"data: {json.dumps({'tool_call': {'name': tc['name']}})}\n\n"
                if app is not None:
                    usage = getattr(app.state, "tool_usage", {})
                    usage[tc["name"]] = usage.get(tc["name"], 0) + 1
                    app.state.tool_usage = usage
                    _usage_pool = getattr(app.state, "memory", None)
                    _usage_pool = getattr(_usage_pool, "_pool", None)
                    if _usage_pool is not None:
                        await set_state(_usage_pool, "tool_usage", json.dumps(usage))
                tool_result = await tools.execute(tc["name"], tc["arguments"])
                # If the tool returned metadata with a job_id (e.g. image generation),
                # emit a tracking event so frontends can poll for completion.
                if tool_result.metadata and "job_id" in tool_result.metadata:
                    yield f"data: {json.dumps({'image_job_started': tool_result.metadata})}\n\n"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result.to_content(),
                    }
                )
            # Discard any content streamed before tool calls (thinking/preamble).
            yield f"data: {json.dumps({'reset_accumulated': True})}\n\n"
            # Detect stuck loops: same tool called 3+ times consecutively.
            recent_tool_names.extend(tc["name"] for tc in pending_tool_calls)
            if len(recent_tool_names) >= 3 and len(set(recent_tool_names[-3:])) == 1:
                logger.warning(
                    "Tool loop detected (%s called 3+ times consecutively), forcing final response",
                    recent_tool_names[-1],
                )
                accumulated = ""
                tool_calls_made = True
                loop_exit_reason = "stuck"
                break
            tool_calls_made = True
            tool_call_count += len(pending_tool_calls)
            accumulated = ""

    # If the loop hit the round limit, note that too.
    if tool_call_count > 0 and tool_calls_made and loop_exit_reason == "natural":
        if tool_call_count >= max_tool_rounds * len(
            pending_tool_calls if pending_tool_calls else [{}]
        ):
            loop_exit_reason = "max_rounds"

    # --- Judge/verification phase: synthesize final answer after tool use ---
    # If we already streamed a substantive answer after tools, don't synthesize
    # a second one (prevents duplicate/repetitive responses).
    if tool_calls_made and not accumulated.strip():
        yield f"data: {json.dumps({'verifying': True})}\n\n"

        if loop_exit_reason == "stuck":
            stuck_tool = recent_tool_names[-1] if recent_tool_names else "a tool"
            loop_context = (
                f"Note: the tool loop was interrupted because '{stuck_tool}' was called "
                f"three or more times consecutively without making progress. "
                f"The search did not find information that directly answers the question."
            )
        elif loop_exit_reason == "max_rounds":
            loop_context = (
                "Note: the tool loop reached its maximum number of rounds without completing. "
                "The available tool results may be partial or incomplete."
            )
        else:
            loop_context = ""

        judge_prompt = (
            f"Original request: {user_text}\n\n"
            f"{('Loop context: ' + loop_context + chr(10) + chr(10)) if loop_context else ''}"
            "Review the tool results above and synthesize a final answer. "
            "If the results contain what the user asked for, provide it directly. "
            "If the results are incomplete or the question cannot be answered from what was found, "
            "say so plainly - do not guess or fabricate. "
            "A honest 'I couldn't find that' is the right answer when the tools came up empty."
        )
        messages.append({"role": "user", "content": judge_prompt})
        try:
            # Use non-streaming chat() so thinking tokens don't consume max_tokens
            # budget before the actual answer is produced.
            judge_response = await llm.chat(model, messages, temperature, max_tokens, tools=None)
            judge_text = judge_response.content.strip() if judge_response.content else ""
            if not judge_text:
                judge_text = "I searched but the results didn't contain an answer to that. Try asking me to check a specific file or endpoint."
            accumulated += judge_text
            data = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": judge_text},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(data)}\n\n"
        except Exception as exc:
            logger.warning("Judge call failed: %s", exc)
            fallback = "Something went wrong synthesizing a final answer after tool use."
            accumulated = fallback
            yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': fallback}, 'finish_reason': None}]})}\n\n"

    # Write to buffer BEFORE yielding [DONE]
    if memory is not None:
        try:
            elapsed_ms = int((time.monotonic() - stream_start) * 1000)
            if user_text:
                await memory.save_to_buffer("user", user_text)
            if accumulated:
                await memory.save_to_buffer(
                    "assistant",
                    accumulated,
                    prompt_tokens=stream_prompt_tokens or None,
                    completion_tokens=stream_completion_tokens or None,
                    latency_ms=elapsed_ms,
                    tool_calls_made=tool_call_count if tool_call_count else None,
                )
        except Exception as exc:
            logger.warning("Buffer write failed: %s", exc)

    yield "data: [DONE]\n\n"

    # Increment message counter and trigger intent derivation as a background task
    if app is not None and config is not None:
        app.state.messages_since_intent += 1
        if app.state.messages_since_intent >= config.intent_interval:

            async def _intent_task() -> None:
                try:
                    await _derive_intent(app, llm, config)
                except Exception as exc:
                    logger.warning("Intent derivation failed: %s", exc)

            asyncio.create_task(_intent_task())

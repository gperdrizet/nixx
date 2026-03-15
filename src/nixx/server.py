"""Nixx API server — OpenAI-compatible endpoint for local LLM inference."""

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from httpx import HTTPError as HttpError
from pydantic import BaseModel

from nixx.config import NixxConfig
from nixx.ingest.pipeline import IngestPipeline
from nixx.llm import OpenAIClient
from nixx.memory.db import create_pool, get_source, get_source_content, init_schema, list_sources
from nixx.memory.store import MemoryStore
from nixx.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

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


class CompletionRequest(BaseModel):
    model: str | None = None
    prompt: str
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
        logger.info("Memory store ready")
        yield
        await pool.close()

    app = FastAPI(title="nixx", version="0.1.0", lifespan=lifespan)
    llm = OpenAIClient(base_url=config.llm_base_url, api_key=config.llm_api_key)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model": config.llm_model}

    @app.get("/v1/debug/context")
    async def debug_context() -> dict[str, str | None]:
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

        # Build system message: base identity prompt + recalled memory context
        memory: MemoryStore = app.state.memory
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        context_block = ""
        recalled: list[dict] = []
        if last_user:
            try:
                recalled = await memory.recall(last_user)
                context_block = memory.format_context(recalled)
            except Exception as exc:
                logger.warning("Memory recall failed (continuing without context): %s", exc)
        system_content = SYSTEM_PROMPT + (f"\n\n{context_block}" if context_block else "")
        messages = [{"role": "system", "content": system_content}] + messages
        app.state.last_context = {
            "base": SYSTEM_PROMPT,
            "memory": context_block or None,
            "hits": [
                {
                    "content": r["content"],
                    "similarity": round(float(r["similarity"]), 3),
                    "source_id": r["source_id"],
                }
                for r in recalled
            ],
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
                ),
                media_type="text/event-stream",
            )

        try:
            result = await llm.chat(model, messages, temperature, request.max_tokens)
        except HttpError as exc:
            raise HTTPException(status_code=502, detail=f"LLM backend error: {exc}") from exc

        content = result.get("message", {}).get("content", "")

        # Persist the exchange to the buffer.
        if last_user:
            try:
                await memory.save_to_buffer("user", last_user)
                if content:
                    await memory.save_to_buffer("assistant", content)
            except Exception as exc:
                logger.warning("Buffer write failed: %s", exc)

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
            "usage": _usage(result),
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

    @app.post("/v1/completions", response_model=None)
    async def completions(
        request: CompletionRequest,
    ) -> StreamingResponse | dict[str, Any]:
        model = request.model or config.llm_model
        temperature = (
            request.temperature if request.temperature is not None else config.llm_temperature
        )
        completion_id = f"cmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        if request.stream:
            return StreamingResponse(
                _completion_event_stream(
                    llm,
                    model,
                    request.prompt,
                    temperature,
                    request.max_tokens,
                    completion_id,
                    created,
                ),
                media_type="text/event-stream",
            )

        try:
            result = await llm.generate(model, request.prompt, temperature, request.max_tokens)
        except HttpError as exc:
            raise HTTPException(status_code=502, detail=f"LLM backend error: {exc}") from exc

        text = result.get("response", "")
        return {
            "id": completion_id,
            "object": "text_completion",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "text": text, "finish_reason": "stop"}],
            "usage": _usage(result),
        }

    return app


# ── Streaming helpers ─────────────────────────────────────────────────────────


async def _chat_event_stream(
    llm: OpenAIClient,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int | None,
    completion_id: str,
    created: int,
    memory: MemoryStore | None = None,
    user_text: str = "",
) -> AsyncGenerator[str, None]:
    accumulated = ""
    try:
        async for chunk in llm.chat_stream(model, messages, temperature, max_tokens):
            content = chunk.get("message", {}).get("content", "")
            done = chunk.get("done", False)
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
                        "delta": {"content": content} if content else {},
                        "finish_reason": "stop" if done else None,
                    }
                ],
            }
            yield f"data: {json.dumps(data)}\n\n"
            if done:
                break
        yield "data: [DONE]\n\n"
    except Exception as exc:
        error = {"error": {"message": str(exc), "type": "server_error"}}
        yield f"data: {json.dumps(error)}\n\n"
        yield "data: [DONE]\n\n"
        return

    # Write to buffer after the stream completes cleanly.
    if memory is not None:
        try:
            if user_text:
                await memory.save_to_buffer("user", user_text)
            if accumulated:
                await memory.save_to_buffer("assistant", accumulated)
        except Exception as exc:
            logger.warning("Buffer write failed: %s", exc)


async def _completion_event_stream(
    llm: OpenAIClient,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int | None,
    completion_id: str,
    created: int,
) -> AsyncGenerator[str, None]:
    try:
        async for chunk in llm.generate_stream(model, prompt, temperature, max_tokens):
            text = chunk.get("response", "")
            done = chunk.get("done", False)
            data = {
                "id": completion_id,
                "object": "text_completion",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "text": text, "finish_reason": "stop" if done else None}],
            }
            yield f"data: {json.dumps(data)}\n\n"
            if done:
                break
    except Exception as exc:
        error = {"error": {"message": str(exc), "type": "server_error"}}
        yield f"data: {json.dumps(error)}\n\n"
    finally:
        yield "data: [DONE]\n\n"


def _usage(result: dict[str, Any]) -> dict[str, int]:
    prompt_tokens = int(result.get("prompt_eval_count") or 0)
    completion_tokens = int(result.get("eval_count") or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    config = NixxConfig()
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        reload=config.reload,
    )

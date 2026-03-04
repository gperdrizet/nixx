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
from nixx.llm.client import OllamaClient
from nixx.memory.db import create_pool, init_schema
from nixx.memory.store import MemoryStore

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


# ── App factory ───────────────────────────────────────────────────────────────


def create_app(config: NixxConfig | None = None) -> FastAPI:
    if config is None:
        config = NixxConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        pool = await create_pool(config)
        await init_schema(pool, dimensions=config.embedding_dimensions)
        app.state.memory = MemoryStore(config, pool)
        logger.info("Memory store ready")
        yield
        await pool.close()

    app = FastAPI(title="nixx", version="0.1.0", lifespan=lifespan)
    llm = OllamaClient(base_url=config.llm_base_url)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model": config.llm_model, "llm": config.llm_provider}

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

        # Retrieve relevant memory context and prepend to messages
        memory: MemoryStore = app.state.memory
        user_text = " ".join(m["content"] for m in messages if m["role"] == "user")
        if user_text:
            try:
                recalled = await memory.recall(user_text)
                context_block = memory.format_context(recalled)
                if context_block:
                    messages = [{"role": "system", "content": context_block}] + messages
            except Exception as exc:
                logger.warning("Memory recall failed (continuing without context): %s", exc)

        if request.stream:
            return StreamingResponse(
                _chat_event_stream(
                    llm, model, messages, temperature, request.max_tokens, completion_id, created
                ),
                media_type="text/event-stream",
            )

        try:
            result = await llm.chat(model, messages, temperature, request.max_tokens)
        except HttpError as exc:
            raise HTTPException(status_code=502, detail=f"LLM backend error: {exc}") from exc

        content = result.get("message", {}).get("content", "")

        # Persist the exchange to memory
        if user_text:
            try:
                await memory.remember(user_text, source="conversation")
                if content:
                    await memory.remember(content, source="conversation")
            except Exception as exc:
                logger.warning("Memory save failed: %s", exc)

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
    llm: OllamaClient,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int | None,
    completion_id: str,
    created: int,
) -> AsyncGenerator[str, None]:
    try:
        async for chunk in llm.chat_stream(model, messages, temperature, max_tokens):
            content = chunk.get("message", {}).get("content", "")
            done = chunk.get("done", False)
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
    except Exception as exc:
        error = {"error": {"message": str(exc), "type": "server_error"}}
        yield f"data: {json.dumps(error)}\n\n"
    finally:
        yield "data: [DONE]\n\n"


async def _completion_event_stream(
    llm: OllamaClient,
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

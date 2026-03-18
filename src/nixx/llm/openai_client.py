"""HTTP client for llama.cpp server (OpenAI-compatible API)."""

import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """A tool call from the LLM."""

    id: str
    name: str
    arguments: str


@dataclass
class ChatResponse:
    """Response from a chat completion."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0


class OpenAIClient:
    """Async client for llama.cpp server."""

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    # ── Chat ──────────────────────────────────────────────────────────────────

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        """Non-streaming chat completion.

        Returns a ChatResponse with content and/or tool_calls.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            if response.status_code >= 400:
                logger.error(
                    "LLM backend %s on non-streaming call. Messages: %s",
                    response.status_code,
                    json.dumps(messages, indent=2),
                )
            response.raise_for_status()
            data = response.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        # Parse tool calls if present
        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    arguments=func.get("arguments", "{}"),
                )
            )

        return ChatResponse(
            content=message.get("content", "") or "",
            tool_calls=tool_calls,
            done=True,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Streaming chat completion.

        Yields ChatResponse objects. Tool calls are accumulated and yielded
        in the final response (when done=True).
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        # Accumulate tool calls across chunks
        tool_calls_acc: dict[int, dict[str, str]] = {}  # index -> {id, name, arguments}

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            async with http.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    logger.error(
                        "LLM backend %s on streaming call. Messages: %s",
                        response.status_code,
                        json.dumps(messages, indent=2),
                    )
                    logger.error("LLM backend error body: %s", body.decode())
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload_str = line[len("data:") :].strip()
                    if payload_str == "[DONE]":
                        # Finalize tool calls
                        final_calls = [
                            ToolCall(
                                id=tc.get("id", ""),
                                name=tc.get("name", ""),
                                arguments=tc.get("arguments", "{}"),
                            )
                            for tc in tool_calls_acc.values()
                        ]
                        yield ChatResponse(content="", tool_calls=final_calls, done=True)
                        return

                    chunk = json.loads(payload_str)
                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    finish = choice.get("finish_reason")

                    # Handle content
                    content = delta.get("content", "") or ""

                    # Handle tool calls (accumulate across deltas)
                    for tc in delta.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        if "id" in tc:
                            tool_calls_acc[idx]["id"] = tc["id"]
                        func = tc.get("function", {})
                        if "name" in func:
                            tool_calls_acc[idx]["name"] = func["name"]
                        if "arguments" in func:
                            tool_calls_acc[idx]["arguments"] += func["arguments"]

                    # Yield content chunks
                    if content:
                        yield ChatResponse(content=content, done=False)

                    # Signal done only for non-tool finishes (tool_calls wait for [DONE])
                    if finish and finish != "tool_calls":
                        yield ChatResponse(content="", done=True)

    # ── Embeddings ────────────────────────────────────────────────────────────

    async def embed(self, model: str, text: str) -> list[float]:
        """Return an embedding vector for the given text."""
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.post(
                f"{self._base_url}/v1/embeddings",
                json={"model": model, "input": text},
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            # OpenAI format: {"data": [{"embedding": [...float...]}]}
            return cast(list[float], data["data"][0]["embedding"])

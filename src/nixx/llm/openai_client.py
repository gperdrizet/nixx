"""HTTP client for llama.cpp server (OpenAI-compatible API)."""

import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import httpx


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
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat completion.

        Returns a dict:
            {"message": {"role": ..., "content": ...}, "done": True,
             "prompt_eval_count": int, "eval_count": int}
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()

        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})
        return {
            "message": choice.get("message", {"role": "assistant", "content": ""}),
            "done": True,
            "prompt_eval_count": usage.get("prompt_tokens", 0),
            "eval_count": usage.get("completion_tokens", 0),
        }

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming chat completion.

        Yields dicts:
            {"message": {"role": "assistant", "content": "token"}, "done": bool}
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            async with http.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload_str = line[len("data:") :].strip()
                    if payload_str == "[DONE]":
                        yield {"message": {"role": "assistant", "content": ""}, "done": True}
                        return
                    chunk = json.loads(payload_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    finish = chunk.get("choices", [{}])[0].get("finish_reason")
                    yield {
                        "message": {"role": "assistant", "content": content},
                        "done": finish is not None,
                    }

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

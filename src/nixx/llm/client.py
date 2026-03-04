"""Ollama HTTP client for LLM inference."""

import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import httpx


class OllamaClient:
    """Async client for the Ollama API."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat completion."""
        payload = self._chat_payload(model, messages, temperature, max_tokens, stream=False)
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming chat completion — yields one dict per token chunk."""
        payload = self._chat_payload(model, messages, temperature, max_tokens, stream=True)
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            async with http.stream("POST", f"{self._base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield json.loads(line)

    async def generate(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Non-streaming text completion."""
        payload = self._generate_payload(model, prompt, temperature, max_tokens, stream=False)
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.post(f"{self._base_url}/api/generate", json=payload)
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming text completion — yields one dict per token chunk."""
        payload = self._generate_payload(model, prompt, temperature, max_tokens, stream=True)
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            async with http.stream(
                "POST", f"{self._base_url}/api/generate", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield json.loads(line)

    async def embed(self, model: str, text: str) -> list[float]:
        """Return an embedding vector for the given text."""
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.post(
                f"{self._base_url}/api/embed",
                json={"model": model, "input": text},
            )
            response.raise_for_status()
            data = response.json()
            # Ollama returns {"embeddings": [[...float...]]}
            return cast(list[float], data["embeddings"][0])

    def _chat_payload(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        return payload

    def _generate_payload(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        return payload

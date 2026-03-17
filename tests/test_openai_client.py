"""Tests for OpenAIClient."""

import httpx
import pytest
import respx

from nixx.llm.openai_client import OpenAIClient

BASE_URL = "http://localhost:8080"


# ── Non-streaming chat ────────────────────────────────────────────────────────


@respx.mock
async def test_chat_success() -> None:
    openai_response = {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello!"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=openai_response)
    )

    client = OpenAIClient(base_url=BASE_URL)
    result = await client.chat("gpt-oss-20b", [{"role": "user", "content": "hi"}])

    assert result["message"]["content"] == "Hello!"
    assert result["done"] is True
    assert result["prompt_eval_count"] == 10
    assert result["eval_count"] == 5


@respx.mock
async def test_chat_sends_api_key() -> None:
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": ""}}],
                "usage": {},
            },
        )
    )
    client = OpenAIClient(base_url=BASE_URL, api_key="sk-test-key")
    await client.chat("model", [])

    request = respx.calls.last.request
    assert request.headers["Authorization"] == "Bearer sk-test-key"


@respx.mock
async def test_chat_http_error() -> None:
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(return_value=httpx.Response(500))
    client = OpenAIClient(base_url=BASE_URL)
    with pytest.raises(httpx.HTTPStatusError):
        await client.chat("model", [{"role": "user", "content": "hi"}])


# ── Embeddings ────────────────────────────────────────────────────────────────


@respx.mock
async def test_embed_success() -> None:
    openai_response = {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }
    respx.post(f"{BASE_URL}/v1/embeddings").mock(
        return_value=httpx.Response(200, json=openai_response)
    )

    client = OpenAIClient(base_url=BASE_URL)
    result = await client.embed("model", "hello world")

    assert result == [0.1, 0.2, 0.3]


# ── URL handling ──────────────────────────────────────────────────────────────


@respx.mock
async def test_base_url_trailing_slash_stripped() -> None:
    respx.post(f"{BASE_URL}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": ""}}],
                "usage": {},
            },
        )
    )
    client = OpenAIClient(base_url=BASE_URL + "/")
    await client.chat("model", [])
    assert respx.calls.call_count == 1

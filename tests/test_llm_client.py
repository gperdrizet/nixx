"""Tests for OllamaClient."""

import httpx
import pytest
import respx

from nixx.llm.client import OllamaClient

BASE_URL = "http://localhost:11434"


# ── Payload construction (pure, no I/O) ───────────────────────────────────────


def test_chat_payload_basic() -> None:
    client = OllamaClient(base_url=BASE_URL)
    payload = client._chat_payload(
        model="qwen2.5-coder:7b",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.5,
        max_tokens=None,
        stream=False,
    )
    assert payload["model"] == "qwen2.5-coder:7b"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["options"]["temperature"] == 0.5
    assert payload["stream"] is False
    assert "num_predict" not in payload["options"]


def test_chat_payload_max_tokens() -> None:
    client = OllamaClient(base_url=BASE_URL)
    payload = client._chat_payload(
        model="x", messages=[], temperature=0.7, max_tokens=256, stream=True
    )
    assert payload["options"]["num_predict"] == 256
    assert payload["stream"] is True


def test_generate_payload_basic() -> None:
    client = OllamaClient(base_url=BASE_URL)
    payload = client._generate_payload(
        model="llama3:8b", prompt="def foo():", temperature=0.3, max_tokens=None, stream=False
    )
    assert payload["model"] == "llama3:8b"
    assert payload["prompt"] == "def foo():"
    assert payload["options"]["temperature"] == 0.3
    assert payload["stream"] is False
    assert "num_predict" not in payload["options"]


def test_generate_payload_max_tokens() -> None:
    client = OllamaClient(base_url=BASE_URL)
    payload = client._generate_payload(
        model="x", prompt="y", temperature=0.7, max_tokens=128, stream=False
    )
    assert payload["options"]["num_predict"] == 128


# ── HTTP calls (mocked via respx) ─────────────────────────────────────────────


@respx.mock
async def test_chat_success() -> None:
    ollama_response = {
        "message": {"role": "assistant", "content": "Hello!"},
        "done": True,
        "prompt_eval_count": 10,
        "eval_count": 5,
    }
    respx.post(f"{BASE_URL}/api/chat").mock(return_value=httpx.Response(200, json=ollama_response))

    client = OllamaClient(base_url=BASE_URL)
    result = await client.chat("qwen2.5-coder:7b", [{"role": "user", "content": "hi"}])

    assert result["message"]["content"] == "Hello!"
    assert result["eval_count"] == 5


@respx.mock
async def test_chat_http_error() -> None:
    respx.post(f"{BASE_URL}/api/chat").mock(return_value=httpx.Response(500))

    client = OllamaClient(base_url=BASE_URL)
    with pytest.raises(httpx.HTTPStatusError):
        await client.chat("qwen2.5-coder:7b", [{"role": "user", "content": "hi"}])


@respx.mock
async def test_generate_success() -> None:
    ollama_response = {
        "response": "def foo(): pass",
        "done": True,
        "prompt_eval_count": 8,
        "eval_count": 6,
    }
    respx.post(f"{BASE_URL}/api/generate").mock(
        return_value=httpx.Response(200, json=ollama_response)
    )

    client = OllamaClient(base_url=BASE_URL)
    result = await client.generate("qwen2.5-coder:7b", "def foo():")

    assert result["response"] == "def foo(): pass"


@respx.mock
async def test_generate_http_error() -> None:
    respx.post(f"{BASE_URL}/api/generate").mock(return_value=httpx.Response(503))

    client = OllamaClient(base_url=BASE_URL)
    with pytest.raises(httpx.HTTPStatusError):
        await client.generate("qwen2.5-coder:7b", "def foo():")


@respx.mock
async def test_base_url_trailing_slash_stripped() -> None:
    """Client strips trailing slash so URLs are always well-formed."""
    respx.post(f"{BASE_URL}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": ""}, "done": True})
    )
    client = OllamaClient(base_url=BASE_URL + "/")
    await client.chat("model", [])
    assert respx.calls.call_count == 1

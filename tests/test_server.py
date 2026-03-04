"""Tests for the FastAPI server endpoints."""

import json
from unittest.mock import AsyncMock, patch

import httpx

from nixx.config import NixxConfig
from nixx.server import create_app
from tests.conftest import CHAT_RESPONSE, _mock_memory_store

# ── /health ───────────────────────────────────────────────────────────────────


async def test_health(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "model" in data
    assert "llm" in data


# ── /v1/chat/completions ──────────────────────────────────────────────────────


async def test_chat_completions_success(mocked_app_client: httpx.AsyncClient) -> None:
    response = await mocked_app_client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Hello!"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] == 10
    assert body["usage"]["completion_tokens"] == 5
    assert body["usage"]["total_tokens"] == 15


async def test_chat_completions_uses_default_model(
    mocked_app_client: httpx.AsyncClient, config: NixxConfig
) -> None:
    response = await mocked_app_client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.json()["model"] == config.llm_model


async def test_chat_completions_model_override(config: NixxConfig) -> None:
    with patch("nixx.server.OllamaClient") as MockClient:
        MockClient.return_value.chat = AsyncMock(return_value=CHAT_RESPONSE)
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "llama3:8b", "messages": [{"role": "user", "content": "hi"}]},
            )
    assert response.json()["model"] == "llama3:8b"


async def test_chat_completions_ollama_error(config: NixxConfig) -> None:
    with patch("nixx.server.OllamaClient") as MockClient:
        MockClient.return_value.chat = AsyncMock(
            side_effect=httpx.ConnectError("All connection attempts failed")
        )
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
    assert response.status_code == 502
    assert "LLM backend error" in response.json()["detail"]


async def test_chat_completions_streaming(config: NixxConfig) -> None:
    async def mock_chat_stream(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        yield {"message": {"role": "assistant", "content": "Hi"}, "done": False}
        yield {"message": {"role": "assistant", "content": ""}, "done": True}

    with patch("nixx.server.OllamaClient") as MockClient:
        MockClient.return_value.chat_stream = mock_chat_stream
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]
                lines = [line async for line in response.aiter_lines() if line]

    data_lines = [line for line in lines if line.startswith("data:") and line != "data: [DONE]"]
    assert len(data_lines) >= 1
    first = json.loads(data_lines[0].removeprefix("data: "))
    assert first["object"] == "chat.completion.chunk"
    assert lines[-1] == "data: [DONE]"


# ── /v1/completions ───────────────────────────────────────────────────────────


async def test_completions_success(mocked_app_client: httpx.AsyncClient) -> None:
    response = await mocked_app_client.post(
        "/v1/completions",
        json={"prompt": "def foo():"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "text_completion"
    assert body["choices"][0]["text"] == "def foo(): pass"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 14


async def test_completions_ollama_error(config: NixxConfig) -> None:
    with patch("nixx.server.OllamaClient") as MockClient:
        MockClient.return_value.generate = AsyncMock(
            side_effect=httpx.ConnectError("All connection attempts failed")
        )
        app = create_app(config)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/completions",
                json={"prompt": "def foo():"},
            )
    assert response.status_code == 502


async def test_completions_streaming(config: NixxConfig) -> None:
    async def mock_generate_stream(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        yield {"response": "def foo", "done": False}
        yield {"response": "(): pass", "done": True}

    with patch("nixx.server.OllamaClient") as MockClient:
        MockClient.return_value.generate_stream = mock_generate_stream
        app = create_app(config)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            async with client.stream(
                "POST",
                "/v1/completions",
                json={"prompt": "def foo():", "stream": True},
            ) as response:
                assert response.status_code == 200
                lines = [line async for line in response.aiter_lines() if line]

    assert lines[-1] == "data: [DONE]"
    data_lines = [line for line in lines if line.startswith("data:") and line != "data: [DONE]"]
    first = json.loads(data_lines[0].removeprefix("data: "))
    assert first["object"] == "text_completion"

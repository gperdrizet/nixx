"""Tests for the FastAPI server endpoints."""

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx

from nixx.config import NixxConfig
from nixx.llm import ChatResponse
from nixx.server import (
    _chat_event_stream,
    _strip_trailing_empty_assistant,
    _truncate_messages,
    create_app,
)
from nixx.tools import ToolRegistry
from nixx.tools.base import ToolResult
from tests.conftest import CHAT_RESPONSE, _mock_memory_store

# ── /health ───────────────────────────────────────────────────────────────────


async def test_health(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "model" in data


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


async def test_debug_context_includes_token_usage(
    mocked_app_client: httpx.AsyncClient,
) -> None:
    # Fire a chat completion so last_context gets populated.
    await mocked_app_client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    response = await mocked_app_client.get("/v1/debug/context")
    assert response.status_code == 200
    data = response.json()
    usage = data["token_usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["context_length"] > 0


async def test_chat_completions_uses_default_model(
    mocked_app_client: httpx.AsyncClient, config: NixxConfig
) -> None:
    response = await mocked_app_client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.json()["model"] == config.llm_model


async def test_chat_completions_preserves_tool_calls_in_messages(
    config: NixxConfig, tmp_path: Path
) -> None:
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(return_value=CHAT_RESPONSE)
    with patch("nixx.server.OpenAIClient", return_value=mock_client):
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        app.state.recall_enabled = True
        app.state.intent_enabled = True
        app.state.tools = ToolRegistry(tmp_path / "scratch")
        app.state.intent = None
        app.state.messages_since_intent = 0
        app.state.project_dir = None

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "messages": [
                        {"role": "user", "content": "list files"},
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "list_dir",
                                        "arguments": '{"path":"."}',
                                    },
                                }
                            ],
                        },
                    ]
                },
            )

    assert response.status_code == 200
    called_messages = mock_client.chat.call_args.args[1]
    assert called_messages[2]["tool_calls"][0]["function"]["name"] == "list_dir"


async def test_chat_completions_model_override(config: NixxConfig, tmp_path: Path) -> None:
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(return_value=CHAT_RESPONSE)
    with patch("nixx.server.OpenAIClient", return_value=mock_client):
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        app.state.recall_enabled = True
        app.state.intent_enabled = True
        app.state.tools = ToolRegistry(tmp_path / "scratch")
        app.state.intent = None
        app.state.messages_since_intent = 0
        app.state.project_dir = None  # Patch for new permissions model
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "llama3:8b", "messages": [{"role": "user", "content": "hi"}]},
            )
    assert response.json()["model"] == "llama3:8b"


async def test_chat_completions_llm_error(config: NixxConfig, tmp_path: Path) -> None:
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(side_effect=httpx.ConnectError("All connection attempts failed"))
    with patch("nixx.server.OpenAIClient", return_value=mock_client):
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        app.state.recall_enabled = True
        app.state.intent_enabled = True
        app.state.tools = ToolRegistry(tmp_path / "scratch")
        app.state.intent = None
        app.state.messages_since_intent = 0
        app.state.project_dir = None  # Patch for new permissions model
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
    assert response.status_code == 502
    assert "LLM backend error" in response.json()["detail"]


async def test_chat_completions_streaming(config: NixxConfig, tmp_path: Path) -> None:
    async def mock_chat_stream(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        yield ChatResponse(content="Hi", done=False)
        yield ChatResponse(content="", done=True)

    mock_client = AsyncMock()
    mock_client.chat_stream = mock_chat_stream
    with patch("nixx.server.OpenAIClient", return_value=mock_client):
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        app.state.recall_enabled = True
        app.state.intent_enabled = True
        app.state.tools = ToolRegistry(tmp_path / "scratch")
        app.state.intent = None
        app.state.messages_since_intent = 0
        app.state.project_dir = None  # Patch for new permissions model
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


async def test_stream_resume_skips_judge_when_answer_already_streamed() -> None:
    class FakeTools:
        def to_openai_tools(self) -> list[dict]:
            return []

        async def execute(self, name: str, arguments: str) -> ToolResult:
            return ToolResult(success=True, result=f"ok:{name}:{arguments}")

    class FakeLLM:
        def __init__(self) -> None:
            self.chat_calls = 0

        async def chat_stream(
            self,
            model: str,
            messages: list[dict],
            temperature: float,
            max_tokens: int | None,
            tools: list[dict] | None = None,
        ) -> AsyncGenerator[ChatResponse, None]:
            yield ChatResponse(content="Answer once.", done=False)
            yield ChatResponse(content="", done=True)

        async def chat(
            self,
            model: str,
            messages: list[dict],
            temperature: float,
            max_tokens: int | None,
            tools: list[dict] | None = None,
        ) -> ChatResponse:
            self.chat_calls += 1
            return ChatResponse(content="Answer again.", done=True)

    llm = FakeLLM()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "What is the current project directory?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": '{"path":"."}'},
                }
            ],
        },
    ]

    lines: list[str] = []
    async for line in _chat_event_stream(
        llm=cast(Any, llm),
        model="gpt-oss-20b",
        messages=messages,
        temperature=0.7,
        max_tokens=None,
        completion_id="chatcmpl-test",
        created=0,
        memory=None,
        user_text="What is the current project directory?",
        tools=cast(Any, FakeTools()),
    ):
        lines.append(line.strip())

    assert llm.chat_calls == 0
    assert any('"tool_call": {"name": "list_dir"}' in line for line in lines)
    assert any("Answer once." in line for line in lines)
    assert not any('"verifying": true' in line for line in lines)


# ── _truncate_messages ────────────────────────────────────────────────────────


def test_truncate_messages_short_history_unchanged() -> None:
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = _truncate_messages(messages, context_length=8192)
    assert result == messages


def test_truncate_messages_drops_oldest() -> None:
    system = {"role": "system", "content": "x" * 400}  # ~100 tokens
    # Each message is ~250 tokens (1000 chars). With budget 8192 - 1024 = 7168,
    # minus 100 for system = 7068 remaining. Each msg ~254 tokens (250 + 4 framing).
    # Fits ~27 messages. We'll send 40, so oldest ~13 should be dropped.
    old_msgs = [{"role": "user", "content": f"old message {i} " + "a" * 990} for i in range(40)]
    messages = [system] + old_msgs
    result = _truncate_messages(messages, context_length=8192)
    assert result[0] == system
    assert len(result) < len(messages)
    # The kept messages should be the newest ones
    assert result[-1] == old_msgs[-1]
    assert result[1] != old_msgs[0]  # oldest was dropped


def test_truncate_messages_keeps_system_when_budget_tiny() -> None:
    system = {"role": "system", "content": "You are helpful."}
    messages = [system, {"role": "user", "content": "hi"}]
    # Context length so small only system fits
    result = _truncate_messages(messages, context_length=100)
    assert result == [system]


def test_strip_trailing_empty_assistant_removes_invalid_tail() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": ""},
    ]
    _strip_trailing_empty_assistant(messages)
    assert messages[-1]["role"] == "user"


def test_strip_trailing_empty_assistant_keeps_tool_call_tail() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "list files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": '{"path":"."}'},
                }
            ],
        },
    ]
    _strip_trailing_empty_assistant(messages)
    assert messages[-1]["role"] == "assistant"
    assert "tool_calls" in messages[-1]


# ── Recall toggle ─────────────────────────────────────────────────────────────


async def test_recall_toggle_disables_context_injection(config: NixxConfig, tmp_path: Path) -> None:
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(return_value=CHAT_RESPONSE)
    with patch("nixx.server.OpenAIClient", return_value=mock_client):
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        app.state.recall_enabled = True
        app.state.intent_enabled = True
        app.state.tools = ToolRegistry(tmp_path / "scratch")
        app.state.intent = None
        app.state.messages_since_intent = 0
        app.state.project_dir = None  # Patch for new permissions model
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Disable recall
            resp = await client.post("/v1/episodic/config", json={"recall_enabled": False})
            assert resp.json()["recall_enabled"] is False
            # Chat should have no recall hits
            await client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
            ctx = await client.get("/v1/debug/context")
            assert ctx.json()["memory"] is None

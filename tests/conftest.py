"""Shared test fixtures."""

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nixx.config import NixxConfig
from nixx.server import create_app

# ── Reusable LLM response shapes ─────────────────────────────────────────────
# These match the internal format returned by OpenAIClient.

CHAT_RESPONSE: dict = {
    "message": {"role": "assistant", "content": "Hello!"},
    "done": True,
    "prompt_eval_count": 10,
    "eval_count": 5,
}


# ── Config fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NixxConfig:
    """Isolated NixxConfig — no .env on disk, all paths under tmp_path.

    chdir to tmp_path so pydantic-settings finds no .env file to load.
    """
    monkeypatch.chdir(tmp_path)
    for key in ["NIXX_DATABASE_URL", "NIXX_POSTGRES_PASSWORD"]:
        monkeypatch.delenv(key, raising=False)
    return NixxConfig(_env_file=tmp_path / ".env")


# ── Memory mock helper ────────────────────────────────────────────────────────


def _mock_memory_store() -> MagicMock:
    """Return a MagicMock MemoryStore with async no-op methods."""
    store = MagicMock()
    store.save_to_buffer = AsyncMock(return_value=1)
    store.create_source = AsyncMock(
        return_value={
            "id": 1,
            "name": "test",
            "start_id": 1,
            "end_id": 10,
            "summary": "test summary",
        }
    )
    store.recall_episodic_for_prompt = AsyncMock(return_value=[])
    store.format_episodic_context = MagicMock(return_value="")
    store.check_summary_due = AsyncMock(return_value=False)
    store.create_episode_summary = AsyncMock(
        return_value={
            "id": 1,
            "content": "test summary",
            "tags": ["test"],
            "entities": {},
            "start_buffer_id": 1,
            "end_buffer_id": 10,
        }
    )
    store.recall_episodic = AsyncMock(return_value=[])
    return store


# ── Server client fixtures ────────────────────────────────────────────────────


@pytest.fixture
async def app_client(config: NixxConfig) -> AsyncGenerator[httpx.AsyncClient, None]:
    """HTTP client against the real app with memory store mocked out.

    The lifespan is bypassed entirely — app.state.memory is set directly.
    Use for endpoints that don't call the LLM backend (e.g. /health).
    """
    app = create_app(config)
    app.state.memory = _mock_memory_store()
    app.state.recall_enabled = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def mocked_app_client(config: NixxConfig) -> AsyncGenerator[httpx.AsyncClient, None]:
    """HTTP client against the app with LLM client and MemoryStore mocked out.

    Use for LLM endpoint tests that should not hit the backend or Postgres.
    """
    mock_client = MagicMock()
    mock_client.chat = AsyncMock(return_value=CHAT_RESPONSE)
    with patch("nixx.server.OpenAIClient", return_value=mock_client):
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        app.state.recall_enabled = True
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client

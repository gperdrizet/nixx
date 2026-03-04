"""Shared test fixtures."""

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nixx.config import NixxConfig
from nixx.server import create_app

# ── Reusable Ollama response shapes ──────────────────────────────────────────

CHAT_RESPONSE: dict = {
    "message": {"role": "assistant", "content": "Hello!"},
    "done": True,
    "prompt_eval_count": 10,
    "eval_count": 5,
}

GENERATE_RESPONSE: dict = {
    "response": "def foo(): pass",
    "done": True,
    "prompt_eval_count": 8,
    "eval_count": 6,
}


# ── Config fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NixxConfig:
    """Isolated NixxConfig — no .env on disk, all paths under tmp_path.

    chdir to tmp_path so:
    - pydantic-settings finds no .env file to load
    - NixxConfig.__init__ creates data/ and config/ inside tmp_path, not cwd
    """
    monkeypatch.chdir(tmp_path)
    for key in ["NIXX_DATABASE_URL", "NIXX_POSTGRES_PASSWORD", "NIXX_ENCRYPTION_KEY"]:
        monkeypatch.delenv(key, raising=False)
    return NixxConfig()


# ── Memory mock helper ────────────────────────────────────────────────────────


def _mock_memory_store() -> MagicMock:
    """Return a MagicMock MemoryStore with async no-op methods."""
    store = MagicMock()
    store.recall = AsyncMock(return_value=[])
    store.remember = AsyncMock(return_value=1)
    store.format_context = MagicMock(return_value="")
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
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
async def mocked_app_client(config: NixxConfig) -> AsyncGenerator[httpx.AsyncClient, None]:
    """HTTP client against the app with OllamaClient and MemoryStore mocked out.

    Use for LLM endpoint tests that should not hit Ollama or Postgres.
    """
    with patch("nixx.server.OllamaClient") as MockClient:
        MockClient.return_value.chat = AsyncMock(return_value=CHAT_RESPONSE)
        MockClient.return_value.generate = AsyncMock(return_value=GENERATE_RESPONSE)
        app = create_app(config)
        app.state.memory = _mock_memory_store()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client

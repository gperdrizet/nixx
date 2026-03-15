"""Tests for the ingest module."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from nixx.ingest.chunker import chunk
from nixx.ingest.handlers import HandlerRegistry
from nixx.ingest.pipeline import IngestPipeline
from nixx.ingest.reader import read
from nixx.config import NixxConfig

# ── handler registry ──────────────────────────────────────────────────────────


def test_registry_routes_url() -> None:
    registry = HandlerRegistry()
    handler = registry.get_handler("https://example.com")
    assert handler.name == "web"


def test_registry_routes_file() -> None:
    registry = HandlerRegistry()
    handler = registry.get_handler("/some/file.md")
    assert handler.name == "file"


def test_registry_plugin_loaded(tmp_path: Path) -> None:
    plugin = tmp_path / "custom.py"
    plugin.write_text(
        "from nixx.ingest.handlers.base import IngestHandler\n"
        "class CustomHandler(IngestHandler):\n"
        "    name = 'custom'\n"
        "    def can_handle(self, source: str) -> bool: return source.startswith('custom://')\n"
        "    async def read(self, source: str) -> tuple[str, str]: return ('text', 'custom')\n"
    )
    registry = HandlerRegistry(handlers_dir=tmp_path)
    handler = registry.get_handler("custom://foo")
    assert handler.name == "custom"


# ── chunker ───────────────────────────────────────────────────────────────────


def test_chunk_empty() -> None:
    assert chunk("") == []
    assert chunk("   \n  ") == []


def test_chunk_short_text() -> None:
    result = chunk("Hello world")
    assert result == ["Hello world"]


def test_chunk_splits_on_paragraphs() -> None:
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    result = chunk(text)
    assert len(result) >= 1
    assert all(r.strip() for r in result)


def test_chunk_respects_size() -> None:
    # Each paragraph is 200 chars; with chunk_size=300 they should be grouped.
    para = "x" * 200
    text = f"{para}\n\n{para}\n\n{para}"
    result = chunk(text, chunk_size=300, overlap=0)
    assert len(result) > 1
    for r in result:
        assert len(r) <= 300 + 10  # small tolerance for paragraph joins


def test_chunk_hard_splits_long_paragraph() -> None:
    long_para = "y" * 4000
    result = chunk(long_para, chunk_size=1500, overlap=0)
    assert len(result) >= 2
    for r in result:
        assert len(r) <= 1500


# ── reader ────────────────────────────────────────────────────────────────────


async def test_read_file(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("# Hello\n\nThis is a test note.")
    text, kind = await read(str(f))
    assert "Hello" in text
    assert kind == "document"


async def test_read_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        await read("/nonexistent/file.md")


async def test_read_file_unsupported_type(tmp_path: Path) -> None:
    f = tmp_path / "file.xyz"
    f.write_text("content")
    with pytest.raises(ValueError, match="Unsupported file type"):
        await read(str(f))


async def test_read_html_file(tmp_path: Path) -> None:
    f = tmp_path / "page.html"
    f.write_text("<html><body><p>Hello world</p><script>alert(1)</script></body></html>")
    text, kind = await read(str(f))
    assert "Hello world" in text
    assert "alert" not in text
    assert kind == "document"


@respx.mock
async def test_read_url() -> None:
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(
            200,
            text="<html><body><p>Test page</p></body></html>",
            headers={"content-type": "text/html"},
        )
    )
    text, kind = await read("https://example.com/page")
    assert "Test page" in text
    assert kind == "web"


@respx.mock
async def test_read_url_plain_text() -> None:
    respx.get("https://example.com/file.txt").mock(
        return_value=httpx.Response(
            200, text="raw text content", headers={"content-type": "text/plain"}
        )
    )
    text, kind = await read("https://example.com/file.txt")
    assert text == "raw text content"
    assert kind == "web"


# ── pipeline ──────────────────────────────────────────────────────────────────


async def test_pipeline_ingest_file(tmp_path: Path, config: NixxConfig) -> None:
    f = tmp_path / "doc.md"
    f.write_text("# Project nixx\n\nA self-hosted memory system for developers.")

    mock_pool = MagicMock()

    with (
        patch("nixx.ingest.pipeline.OpenAIClient") as mock_factory,
        patch("nixx.ingest.pipeline.save_source", new_callable=AsyncMock) as mock_save_source,
        patch("nixx.ingest.pipeline.save_memory", new_callable=AsyncMock) as mock_save_memory,
    ):
        mock_client = MagicMock()
        mock_client.embed = AsyncMock(return_value=[0.1] * 1024)
        mock_client.chat = AsyncMock(return_value={"message": {"content": "A test summary."}})
        mock_factory.return_value = mock_client
        mock_save_source.return_value = 42

        pipeline = IngestPipeline(config, mock_pool)
        result = await pipeline.ingest(str(f), name="test doc")

    assert result["source_id"] == 42
    assert result["name"] == "test doc"
    assert result["kind"] == "document"
    assert result["chunks"] >= 1
    assert "summary" in result
    mock_save_source.assert_called_once()
    assert mock_save_memory.call_count == result["chunks"]


async def test_pipeline_ingest_empty_file(tmp_path: Path, config: NixxConfig) -> None:
    f = tmp_path / "empty.md"
    f.write_text("   ")
    mock_pool = MagicMock()

    with patch("nixx.ingest.pipeline.OpenAIClient"):
        pipeline = IngestPipeline(config, mock_pool)
        with pytest.raises(ValueError, match="No content extracted"):
            await pipeline.ingest(str(f))


async def test_ingest_endpoint(mocked_app_client: httpx.AsyncClient, tmp_path: Path) -> None:
    from nixx.server import create_app
    from nixx.config import NixxConfig
    import httpx as _httpx

    f = tmp_path / "readme.md"
    f.write_text("# Nixx\n\nSelf-hosted memory system.")

    with (
        patch("nixx.ingest.pipeline.OpenAIClient") as mock_factory,
        patch("nixx.ingest.pipeline.save_source", new_callable=AsyncMock) as mock_save_source,
        patch("nixx.ingest.pipeline.save_memory", new_callable=AsyncMock),
        patch("nixx.server.OpenAIClient") as mock_server_factory,
    ):
        mock_client = MagicMock()
        mock_client.embed = AsyncMock(return_value=[0.1] * 1024)
        mock_client.chat = AsyncMock(return_value={"message": {"content": "A summary."}})
        mock_factory.return_value = mock_client
        mock_server_client = MagicMock()
        mock_server_client.chat = AsyncMock(return_value={})
        mock_server_factory.return_value = mock_server_client
        mock_save_source.return_value = 1

        cfg = NixxConfig()
        app = create_app(cfg)
        from tests.conftest import _mock_memory_store

        app.state.memory = _mock_memory_store()

        from nixx.ingest.pipeline import IngestPipeline as _IP
        from unittest.mock import MagicMock as _MM

        mock_pool = _MM()
        app.state.ingest = _IP(cfg, mock_pool)

        async with _httpx.AsyncClient(
            transport=_httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/ingest",
                json={"source": str(f), "name": "test readme"},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test readme"
    assert data["kind"] == "document"

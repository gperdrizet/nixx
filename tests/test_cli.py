"""Tests for the nixx CLI."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from nixx.cli import _build_parser, _serve, _status
from nixx.config import NixxConfig

# ── Parser tests ──────────────────────────────────────────────────────────────


def test_parser_serve_defaults() -> None:
    args = _build_parser().parse_args(["serve"])
    assert args.command == "serve"
    assert args.host is None
    assert args.port is None
    assert args.reload is False


def test_parser_serve_flags() -> None:
    args = _build_parser().parse_args(["serve", "--host", "0.0.0.0", "--port", "9000", "--reload"])
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.reload is True


def test_parser_status_defaults() -> None:
    args = _build_parser().parse_args(["status"])
    assert args.command == "status"
    assert args.host is None
    assert args.port is None


def test_parser_no_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args([])


# ── _serve tests ──────────────────────────────────────────────────────────────


def test_serve_calls_uvicorn(config: NixxConfig, tmp_path: Path) -> None:
    args = _build_parser().parse_args(["serve"])
    with patch("nixx.cli.uvicorn") as mock_uvicorn:
        _serve(config, args)
    mock_uvicorn.run.assert_called_once()
    _, kwargs = mock_uvicorn.run.call_args
    assert kwargs["host"] == config.host
    assert kwargs["port"] == config.port


def test_serve_host_port_override(config: NixxConfig) -> None:
    args = _build_parser().parse_args(["serve", "--host", "0.0.0.0", "--port", "9999"])
    with patch("nixx.cli.uvicorn") as mock_uvicorn:
        _serve(config, args)
    _, kwargs = mock_uvicorn.run.call_args
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9999


# ── _status tests ─────────────────────────────────────────────────────────────


def test_status_ok(config: NixxConfig) -> None:
    args = _build_parser().parse_args(["status"])
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "ok", "model": "qwen2.5-coder:7b", "llm": "ollama"}
    mock_response.raise_for_status.return_value = None

    with patch("nixx.cli.httpx.get", return_value=mock_response):
        _status(config, args)  # should not raise


def test_status_connect_error_exits(config: NixxConfig) -> None:
    args = _build_parser().parse_args(["status"])
    with patch("nixx.cli.httpx.get", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(SystemExit) as exc_info:
            _status(config, args)
    assert exc_info.value.code == 1


def test_status_http_error_exits(config: NixxConfig) -> None:
    args = _build_parser().parse_args(["status"])
    error_response = MagicMock()
    error_response.status_code = 503
    with patch(
        "nixx.cli.httpx.get",
        side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=error_response),
    ):
        with pytest.raises(SystemExit) as exc_info:
            _status(config, args)
    assert exc_info.value.code == 1

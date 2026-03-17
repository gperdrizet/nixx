"""Tests for NixxConfig."""

from pathlib import Path

import pytest

from nixx.config import NixxConfig


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ["NIXX_DATABASE_URL", "NIXX_POSTGRES_PASSWORD"]:
        monkeypatch.delenv(key, raising=False)

    cfg = NixxConfig(_env_file=tmp_path / ".env")

    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8000
    assert cfg.reload is False
    assert cfg.llm_model == "gpt-oss-20b"
    assert cfg.llm_temperature == 0.7
    assert "postgresql" in cfg.database_url
    assert cfg.postgres_password is None


def test_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIXX_PORT", "9000")
    monkeypatch.setenv("NIXX_LLM_MODEL", "llama3:8b")
    monkeypatch.setenv("NIXX_LLM_TEMPERATURE", "0.2")
    for key in ["NIXX_DATABASE_URL", "NIXX_POSTGRES_PASSWORD"]:
        monkeypatch.delenv(key, raising=False)

    cfg = NixxConfig(_env_file=tmp_path / ".env")

    assert cfg.port == 9000
    assert cfg.llm_model == "llama3:8b"
    assert cfg.llm_temperature == 0.2


def test_constructor_kwargs_override_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct kwargs to NixxConfig take precedence over env vars."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIXX_PORT", "9999")
    for key in ["NIXX_DATABASE_URL", "NIXX_POSTGRES_PASSWORD"]:
        monkeypatch.delenv(key, raising=False)

    cfg = NixxConfig(port=1234)

    assert cfg.port == 1234

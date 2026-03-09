"""Tests for NixxConfig."""

from pathlib import Path

import pytest

from nixx.config import NixxConfig


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ["NIXX_DATABASE_URL", "NIXX_POSTGRES_PASSWORD", "NIXX_ENCRYPTION_KEY"]:
        monkeypatch.delenv(key, raising=False)

    cfg = NixxConfig(_env_file=tmp_path / ".env")

    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8000
    assert cfg.reload is False
    assert cfg.llm_provider == "ollama"
    assert cfg.llm_model == "qwen2.5-coder:7b"
    assert cfg.llm_temperature == 0.7
    assert "sqlite" in cfg.database_url
    assert cfg.encryption_key is None
    assert cfg.postgres_password is None
    assert cfg.enable_hardware_monitoring is False


def test_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIXX_PORT", "9000")
    monkeypatch.setenv("NIXX_LLM_MODEL", "llama3:8b")
    monkeypatch.setenv("NIXX_LLM_TEMPERATURE", "0.2")
    for key in ["NIXX_DATABASE_URL", "NIXX_POSTGRES_PASSWORD", "NIXX_ENCRYPTION_KEY"]:
        monkeypatch.delenv(key, raising=False)

    cfg = NixxConfig(_env_file=tmp_path / ".env")

    assert cfg.port == 9000
    assert cfg.llm_model == "llama3:8b"
    assert cfg.llm_temperature == 0.2


def test_creates_data_directories(config: NixxConfig, tmp_path: Path) -> None:
    assert (tmp_path / "data" / "memory").is_dir()
    assert (tmp_path / "data").is_dir()


def test_memory_path_is_absolute_after_mkdir(config: NixxConfig, tmp_path: Path) -> None:
    # memory_path should exist as a real directory
    assert config.memory_path.exists()
    assert config.memory_path.is_dir()


def test_constructor_kwargs_override_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct kwargs to NixxConfig take precedence over env vars."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIXX_PORT", "9999")
    for key in ["NIXX_DATABASE_URL", "NIXX_POSTGRES_PASSWORD", "NIXX_ENCRYPTION_KEY"]:
        monkeypatch.delenv(key, raising=False)

    cfg = NixxConfig(port=1234)

    assert cfg.port == 1234

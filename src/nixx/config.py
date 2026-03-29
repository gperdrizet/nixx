"""Configuration management for Nixx."""

from pathlib import Path
from typing import Any, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to nixx project .env file
_NIXX_ROOT = Path(__file__).parent.parent.parent
_ENV_FILE = _NIXX_ROOT / ".env"


class NixxConfig(BaseSettings):
    """Main configuration for Nixx."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        env_prefix="NIXX_",
        case_sensitive=False,
        extra="ignore",
    )

    # Server settings
    host: str = Field(default="127.0.0.1", description="API server host")
    port: int = Field(default=8000, description="API server port")
    reload: bool = Field(default=False, description="Enable auto-reload for development")

    # LLM settings
    llm_base_url: str = Field(default="http://localhost:8080", description="Base URL for LLM API")
    llm_model: str = Field(default="gpt-oss-20b", description="Default LLM model")
    llm_temperature: float = Field(default=0.7, description="LLM temperature")
    llm_api_key: Optional[str] = Field(default=None, description="API key for the LLM backend")
    llm_context_length: int = Field(
        default=8192, description="Max context length in tokens for the LLM backend"
    )
    max_history_tokens: int = Field(
        default=16384,
        description="Max tokens of conversation history to include in each request (independent of context length)",
    )
    llm_request_timeout: float = Field(
        default=600.0,
        description="Seconds to wait for the LLM to start returning tokens (covers prefill on large prompts)",
    )

    # Memory settings
    embedding_base_url: str = Field(
        default="http://localhost:8082", description="Base URL for embedding server"
    )
    embedding_model: str = Field(default="mxbai-embed-large", description="Embedding model name")
    embedding_dimensions: int = Field(default=1024, description="Embedding vector dimensions")

    # Episodic memory settings
    summary_interval: int = Field(
        default=1000, description="Word count threshold for episodic summary prompts"
    )
    recall_threshold: float = Field(
        default=0.4, description="Minimum cosine similarity for episodic recall injection"
    )

    # Intent derivation settings
    intent_interval: int = Field(
        default=5, description="Number of messages between automatic intent derivation"
    )
    intent_lookback: int = Field(
        default=10, description="Number of recent messages to analyze for intent derivation"
    )

    # Search settings
    searxng_url: str = Field(
        default="http://localhost:8888", description="Base URL for SearXNG instance"
    )

    # Scratch directory for file operations
    scratch_dir: Path = Field(
        default=Path.home() / "nixx_scratch",
        description="Directory for file read/write operations",
    )

    # Database settings
    database_url: str = Field(
        default="postgresql://nixx:changeme@localhost:5432/nixx",
        description="Database connection URL",
    )
    postgres_password: Optional[str] = Field(
        default=None, description="Password for the nixx PostgreSQL role (used by init-db.sh)"
    )

    def __init__(self, **data: Any) -> None:
        """Initialize config and ensure directories exist."""
        super().__init__(**data)

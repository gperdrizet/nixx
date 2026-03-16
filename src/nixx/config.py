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

    # Memory settings
    memory_path: Path = Field(default=Path("./data/memory"), description="Path to memory storage")
    embedding_base_url: str = Field(
        default="http://localhost:8082", description="Base URL for embedding server"
    )
    embedding_model: str = Field(default="mxbai-embed-large", description="Embedding model name")
    embedding_dimensions: int = Field(default=1024, description="Embedding vector dimensions")

    # Episodic memory settings
    summary_interval: int = Field(
        default=500, description="Word count threshold for episodic summary prompts"
    )

    # Database settings
    database_url: str = Field(
        default="postgresql://nixx:changeme@localhost:5432/nixx",
        description="Database connection URL",
    )
    postgres_password: Optional[str] = Field(
        default=None, description="Password for the nixx PostgreSQL role (used by init-db.sh)"
    )

    # Ingest handler plugins
    handlers_dir: Path = Field(
        default=Path("~/.config/nixx/handlers").expanduser(),
        description="Directory for user-defined ingest handler plugins",
    )

    # Hardware monitoring (optional)
    enable_hardware_monitoring: bool = Field(
        default=False, description="Enable hardware monitoring"
    )

    def __init__(self, **data: Any) -> None:
        """Initialize config and ensure directories exist."""
        super().__init__(**data)
        self.memory_path.mkdir(parents=True, exist_ok=True)
        Path("./data").mkdir(parents=True, exist_ok=True)

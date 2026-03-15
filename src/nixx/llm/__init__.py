"""LLM backend clients."""

from nixx.llm.client import OllamaClient
from nixx.llm.openai_client import OpenAIClient

LLMClient = OllamaClient | OpenAIClient


def create_llm_client(
    provider: str,
    base_url: str,
    api_key: str | None = None,
    timeout: float = 120.0,
) -> LLMClient:
    """Instantiate the correct LLM client based on provider name."""
    if provider == "ollama":
        return OllamaClient(base_url=base_url, timeout=timeout)
    return OpenAIClient(base_url=base_url, api_key=api_key, timeout=timeout)

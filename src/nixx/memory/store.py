"""High-level memory store: embed, save, and retrieve memories."""

from __future__ import annotations

import asyncpg

from nixx.config import NixxConfig
from nixx.llm.client import OllamaClient
from nixx.memory.db import save_memory, search_memories


class MemoryStore:
    """Combines the Ollama embedder with the pgvector store.

    Typical lifecycle:
        store = MemoryStore(config, pool)
        await store.remember("The user prefers Python over Ruby")
        results = await store.recall("language preferences")
    """

    def __init__(self, config: NixxConfig, pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
        self._config = config
        self._pool = pool
        self._client = OllamaClient(base_url=config.llm_base_url)

    async def remember(
        self,
        content: str,
        source: str = "conversation",
        metadata: dict | None = None,
    ) -> int:
        """Embed content and persist it to the memory store. Returns the new id."""
        embedding = await self._client.embed(self._config.embedding_model, content)
        return await save_memory(
            self._pool,
            content=content,
            embedding=embedding,
            source=source,
            metadata=metadata,
        )

    async def recall(
        self,
        query: str,
        top_k: int = 5,
        source: str | None = None,
    ) -> list[dict]:
        """Retrieve the top-k most semantically similar memories.

        Each result dict has: id, content, source, metadata, created_at, similarity (0-1).
        """
        embedding = await self._client.embed(self._config.embedding_model, query)
        return await search_memories(
            self._pool, query_embedding=embedding, top_k=top_k, source=source
        )

    def format_context(self, memories: list[dict], threshold: float = 0.5) -> str:
        """Format retrieved memories as a context block for injection into a system prompt.

        Only includes memories above the similarity threshold.
        Returns an empty string if nothing meets the threshold.
        """
        relevant = [m for m in memories if float(m["similarity"]) >= threshold]
        if not relevant:
            return ""
        lines = ["Relevant context from memory:"]
        for m in relevant:
            lines.append(f"- {m['content']}")
        return "\n".join(lines)

"""Ingest pipeline: read → chunk → source → embed → index."""

from __future__ import annotations

import logging

import asyncpg

from nixx.config import NixxConfig
from nixx.ingest.handlers import HandlerRegistry
from nixx.llm import OpenAIClient
from nixx.memory.db import save_memory, save_source

logger = logging.getLogger(__name__)


class IngestPipeline:
    """Ingest a file or URL into the sources + memories tables.

    Typical use:
        pipeline = IngestPipeline(config, pool)
        result = await pipeline.ingest("README.md", name="nixx README")
        result = await pipeline.ingest("https://textual.textualize.io/", name="Textual docs")
    """

    def __init__(self, config: NixxConfig, pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
        self._config = config
        self._pool = pool
        self._llm = OpenAIClient(base_url=config.llm_base_url, api_key=config.llm_api_key)
        self._embedder = OpenAIClient(base_url=config.embedding_base_url)
        self._registry = HandlerRegistry(handlers_dir=config.handlers_dir)

    async def ingest(self, source: str, name: str | None = None) -> dict:
        """Ingest a file path or URL.

        Returns a summary dict: source_id, name, kind, chunks, characters.
        """
        logger.info("Ingesting: %s", source)
        handler = self._registry.get_handler(source)
        logger.info("  using handler: %s", handler.name)
        text, kind = await handler.read(source)
        if not text.strip():
            raise ValueError(f"No content extracted from: {source}")

        label = name or source
        chunks = handler.chunk(text)
        if not chunks:
            raise ValueError(f"No chunks produced from: {source}")

        logger.info("  %d chars → %d chunks", len(text), len(chunks))

        # Generate a short summary of the first chunk (or full text if small).
        summary = await self._summarize(text[:3000], label)

        source_id = await save_source(
            self._pool,
            name=label,
            summary=summary,
            type_=kind,
            start_id=None,
            end_id=None,
        )

        # Embed and index each chunk individually.
        for i, chunk_text in enumerate(chunks):
            embedding = await self._embedder.embed(self._config.embedding_model, chunk_text)
            await save_memory(
                self._pool,
                content=chunk_text,
                embedding=embedding,
                source_id=source_id,
                metadata={"chunk": i, "total_chunks": len(chunks)},
            )
            logger.info("  indexed chunk %d/%d", i + 1, len(chunks))

        logger.info("Ingest complete: source_id=%d", source_id)
        return {
            "source_id": source_id,
            "name": label,
            "kind": kind,
            "chunks": len(chunks),
            "characters": len(text),
            "summary": summary,
        }

    async def _summarize(self, text: str, name: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "Summarise the following document in 3-5 sentences. "
                    "Be specific: capture the subject, key points, and why it would be useful."
                ),
            },
            {"role": "user", "content": f"Document: {name}\n\n{text}"},
        ]
        try:
            result = await self._llm.chat(self._config.llm_model, messages, temperature=0.3)
            return result.get("message", {}).get("content") or text[:500]
        except Exception:
            return text[:500]

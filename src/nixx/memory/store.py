"""High-level memory store: embed, save, and retrieve memories."""

from __future__ import annotations

import json
import logging

import asyncpg

from nixx.config import NixxConfig
from nixx.ingest.chunker import chunk as chunk_text
from nixx.llm import OpenAIClient
from nixx.memory.db import (
    count_unsummarized_words,
    get_buffer_entries,
    get_last_session_marker_id,
    get_last_source_end_id,
    get_max_buffer_id,
    save_buffer_entry,
    save_memory,
    save_source,
    save_summary,
    search_buffer_fulltext,
    search_summaries,
)

logger = logging.getLogger(__name__)


class MemoryStore:
    """Combines the LLM embedder with the pgvector store.

    Typical lifecycle:
        store = MemoryStore(config, pool)
        await store.save_to_buffer("user", "What are we building?")
        await store.create_source("project overview")
    """

    def __init__(self, config: NixxConfig, pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
        self._config = config
        self._pool = pool
        self._llm = OpenAIClient(base_url=config.llm_base_url, api_key=config.llm_api_key)
        self._embedder = OpenAIClient(base_url=config.embedding_base_url)

    async def save_to_buffer(self, role: str, content: str, origin: str = "api") -> int:
        """Append a message to the persistent buffer. Returns the new id."""
        return await save_buffer_entry(self._pool, role=role, content=content, origin=origin)

    async def get_source_range(self) -> tuple[int, int | None]:
        """Return (start_id, end_id) spanning from after the last source to current max buffer id.

        start_id is 1 if no sources exist yet.
        end_id is None if the buffer is empty.
        """
        last_end = await get_last_source_end_id(self._pool)
        start_id = (last_end + 1) if last_end is not None else 1
        end_id = await get_max_buffer_id(self._pool)
        return start_id, end_id

    async def create_source(
        self,
        name: str,
        start_id: int | None = None,
        end_id: int | None = None,
    ) -> dict:
        """Mark a range of buffer entries as a source and index the verbatim transcript.

        Generates a summary for the sources.summary field (display only), but stores
        the verbatim transcript text (chunked) in memories for precise recall.

        If start_id/end_id are not given, the range defaults to everything since the last source.
        Returns a dict with id, name, start_id, end_id, summary, and chunks count.
        """
        if start_id is None or end_id is None:
            auto_start, auto_end = await self.get_source_range()
            start_id = start_id if start_id is not None else auto_start
            end_id = end_id if end_id is not None else auto_end
        if end_id is None:
            raise ValueError("Buffer is empty — nothing to source")

        entries = await get_buffer_entries(self._pool, start_id, end_id)
        if not entries:
            raise ValueError(f"No buffer entries found in range {start_id}–{end_id}")

        transcript = "\n".join(f"{e['role'].upper()}: {e['content']}" for e in entries)

        # Generate summary for display in sources table only
        summary = await self._summarize(transcript)

        source_id = await save_source(
            self._pool,
            name=name,
            summary=summary,
            type_="buffer",
            start_id=start_id,
            end_id=end_id,
        )

        # Chunk and embed the verbatim transcript (not the summary)
        chunks = chunk_text(transcript)
        for i, chunk in enumerate(chunks):
            embedding = await self._embedder.embed(self._config.embedding_model, chunk)
            await save_memory(
                self._pool,
                content=chunk,
                embedding=embedding,
                source_id=source_id,
                metadata={"chunk": i, "total_chunks": len(chunks)},
            )

        return {
            "id": source_id,
            "name": name,
            "start_id": start_id,
            "end_id": end_id,
            "summary": summary,
            "chunks": len(chunks),
        }

    async def _summarize(self, transcript: str) -> str:
        """Ask the LLM to summarise a conversation transcript in a few sentences."""
        messages = [
            {
                "role": "system",
                "content": (
                    "Summarise the following conversation in 3-5 sentences. "
                    "Be specific and precise, capturing key decisions, outcomes, and context."
                ),
            },
            {"role": "user", "content": transcript},
        ]
        try:
            result = await self._llm.chat(self._config.llm_model, messages, temperature=0.3)
            return result.content or transcript[:500]
        except Exception:
            return transcript[:500]

    # ── Episodic memory ───────────────────────────────────────────────────────

    async def check_summary_due(self) -> bool:
        """Return True if unsummarized word count >= summary_interval."""
        words, _, _ = await count_unsummarized_words(self._pool)
        return words >= self._config.summary_interval

    async def create_episode_summary(self) -> dict:
        """Summarize unsummarized buffer entries, extract entities, derive tags, embed, and store.

        Returns a dict with id, content, tags, entities, start_buffer_id, end_buffer_id.
        """
        words, start_id, end_id = await count_unsummarized_words(self._pool)
        if start_id is None or end_id is None or words == 0:
            raise ValueError("No unsummarized messages to process")

        entries = await get_buffer_entries(self._pool, start_id, end_id)
        entries = [e for e in entries if e["role"] != "marker"]
        if not entries:
            raise ValueError("No messages found in range")

        # Use actual buffer IDs from fetched entries (theoretical start_id
        # may reference a deleted row, violating the FK constraint).
        start_id = int(entries[0]["id"])
        end_id = int(entries[-1]["id"])

        transcript = "\n".join(f"{e['role'].upper()}: {e['content']}" for e in entries)

        result = await self._summarize_and_extract(transcript)
        summary_text = result["summary"]
        entities = result["entities"]
        tags = result["tags"]

        try:
            embedding = await self._embedder.embed(self._config.embedding_model, summary_text)
        except Exception as exc:
            raise ValueError(f"Embedding server unavailable: {exc}") from exc

        summary_id = await save_summary(
            self._pool,
            content=summary_text,
            embedding=embedding,
            tags=tags,
            entities=entities,
            start_buffer_id=start_id,
            end_buffer_id=end_id,
        )

        return {
            "id": summary_id,
            "content": summary_text,
            "tags": tags,
            "entities": entities,
            "start_buffer_id": start_id,
            "end_buffer_id": end_id,
        }

    async def _summarize_and_extract(self, transcript: str) -> dict:
        """Ask the LLM to summarize, extract entities, and derive tags in one call.

        Returns {"summary": str, "entities": dict, "tags": list[str]}.
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "Analyze the following conversation. Return a JSON object with three keys:\n"
                    '1. "summary": A 2-3 sentence summary of key decisions and outcomes. '
                    "Write about the content directly, not about the conversation. "
                    'For example: "PostgreSQL supports..." not "The discussion focused on...".\n'
                    '2. "entities": Categorized key terms. Be highly selective - '
                    "only include proper nouns, specific tool/library names, or "
                    "uniquely identifying terms. Skip generic words like 'database', "
                    "'system', 'code'. Maximum 2-3 entries per category. Omit empty categories. "
                    'Categories: "tools", "people", "topics", "files", "urls".\n'
                    '3. "tags": 3-6 short lowercase tags (single words or hyphenated phrases) '
                    "that best characterize the conversation topic for future retrieval. "
                    "Choose specific, meaningful tags - not generic words like 'discussion' or 'help'.\n\n"
                    "Return ONLY valid JSON, no markdown or other text."
                ),
            },
            {"role": "user", "content": transcript},
        ]
        try:
            result = await self._llm.chat(self._config.llm_model, messages, temperature=0.3)
            raw = result.content
            parsed = json.loads(raw)
            tags = parsed.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            tags = [str(t).strip().lower() for t in tags if str(t).strip()]
            return {
                "summary": parsed.get("summary", raw),
                "entities": parsed.get("entities", {}),
                "tags": tags,
            }
        except (json.JSONDecodeError, Exception):
            return {"summary": transcript[:500], "entities": {}, "tags": []}

    async def recall_episodic(self, query: str, top_k: int = 10) -> list[dict]:
        """Search episodic memory: keyword search on transcript buffer.

        Returns a list of transcript hits with buffer IDs for context viewing.
        """
        fulltext_hits = await search_buffer_fulltext(self._pool, query=query, limit=top_k)

        results: list[dict] = []
        for h in fulltext_hits:
            results.append(
                {
                    "content": h["content"],
                    "rank": float(h["rank"]),
                    "role": h["role"],
                    "buffer_id": h["id"],
                    "created_at": h["created_at"],
                }
            )
        return results

    async def recall_episodic_for_prompt(
        self, query: str, top_k: int = 3, threshold: float = 0.4
    ) -> list[dict]:
        """Return the top episodic summaries above threshold for prompt injection.

        Excludes summaries created during the current session to avoid echoing
        content that is already in the conversation history.
        """
        embedding = await self._embedder.embed(self._config.embedding_model, query)
        hits = await search_summaries(self._pool, query_embedding=embedding, top_k=top_k)
        # Filter by threshold
        hits = [h for h in hits if float(h["similarity"]) >= threshold]
        # Exclude summaries from the current session (their content is already in history)
        marker_id = await get_last_session_marker_id(self._pool)
        if marker_id is not None:
            hits = [h for h in hits if h.get("start_buffer_id", 0) < marker_id]
        return hits

    def format_episodic_context(self, summaries: list[dict]) -> str:
        """Format episodic summary hits as a context block for the system prompt.

        Returns an empty string if the list is empty.
        """
        if not summaries:
            return ""
        lines = [
            "The following are summaries of past conversations with the user, "
            "retrieved by relevance to the current message. Use them to inform "
            "your response. You may reference them directly if it makes sense "
            "in context, or use them as background without calling them out.",
        ]
        for s in summaries:
            tags = ", ".join(s.get("tags", []))
            tag_note = f" [tags: {tags}]" if tags else ""
            lines.append(f"- {s['content']}{tag_note}")
        return "\n".join(lines)

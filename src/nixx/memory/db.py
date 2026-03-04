"""Database schema and initialisation for the Nixx memory system.

Tables
------
conversations
    A session container. One row per conversation thread.

messages
    Individual turns within a conversation. Stores role + content verbatim.

memories
    Semantic memory store. Each row is a piece of text with a 1024-d embedding
    vector, enabling cosine similarity search via pgvector.

All tables are created with CREATE TABLE IF NOT EXISTS on server startup —
no migration tool required for initial setup.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import asyncpg
from pgvector.asyncpg import register_vector

from nixx.config import NixxConfig

logger = logging.getLogger(__name__)


# ── Connection pool ───────────────────────────────────────────────────────────


async def create_pool(config: NixxConfig) -> asyncpg.Pool:  # type: ignore[type-arg]
    """Create an asyncpg connection pool with pgvector support registered."""
    pool: asyncpg.Pool = await asyncpg.create_pool(  # type: ignore[assignment]
        dsn=config.database_url,
        min_size=2,
        max_size=10,
        init=_init_connection,
    )
    return pool


async def _init_connection(conn: asyncpg.Connection) -> None:  # type: ignore[type-arg]
    """Register the pgvector codec on every new connection."""
    await register_vector(conn)


# ── Schema initialisation ─────────────────────────────────────────────────────


async def init_schema(pool: asyncpg.Pool, dimensions: int = 1024) -> None:  # type: ignore[type-arg]
    """Create all tables if they don't exist yet."""
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id          BIGSERIAL PRIMARY KEY,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                metadata    JSONB       NOT NULL DEFAULT '{}'
            );
        """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id              BIGSERIAL PRIMARY KEY,
                conversation_id BIGINT      NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role            TEXT        NOT NULL,
                content         TEXT        NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """
        )

        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS memories (
                id          BIGSERIAL PRIMARY KEY,
                content     TEXT        NOT NULL,
                embedding   vector({dimensions})  NOT NULL,
                source      TEXT        NOT NULL DEFAULT 'conversation',
                metadata    JSONB       NOT NULL DEFAULT '{{}}',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """
        )

        # HNSW index for fast approximate nearest-neighbour search
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS memories_embedding_hnsw
            ON memories
            USING hnsw (embedding vector_cosine_ops);
        """
        )

    logger.info("Database schema initialised")


# ── Conversation helpers ──────────────────────────────────────────────────────


async def create_conversation(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    metadata: dict | None = None,
) -> int:
    """Insert a new conversation row and return its id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO conversations (metadata) VALUES ($1) RETURNING id",
            json.dumps(metadata or {}),
        )
        return int(row["id"])


async def save_message(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    conversation_id: int,
    role: str,
    content: str,
) -> None:
    """Append a message to a conversation."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, $2, $3)",
            conversation_id,
            role,
            content,
        )


async def get_messages(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    conversation_id: int,
) -> list[dict]:
    """Return all messages for a conversation, oldest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id = $1 ORDER BY created_at ASC",
            conversation_id,
        )
        return [dict(r) for r in rows]


# ── Memory helpers ────────────────────────────────────────────────────────────


async def save_memory(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    content: str,
    embedding: list[float],
    source: str = "conversation",
    metadata: dict | None = None,
) -> int:
    """Store a piece of text with its embedding vector. Returns the new id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO memories (content, embedding, source, metadata) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            content,
            embedding,
            source,
            json.dumps(metadata or {}),
        )
        return int(row["id"])


async def search_memories(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    query_embedding: list[float],
    top_k: int = 5,
    source: str | None = None,
) -> list[dict]:
    """Return the top-k most similar memories by cosine distance.

    Lower cosine distance = more similar. Results are ordered closest first.
    Optionally filter by source (e.g. 'conversation', 'document').
    """
    async with pool.acquire() as conn:
        if source:
            rows = await conn.fetch(
                "SELECT id, content, source, metadata, created_at, "
                "       1 - (embedding <=> $1) AS similarity "
                "FROM memories "
                "WHERE source = $3 "
                "ORDER BY embedding <=> $1 "
                "LIMIT $2",
                query_embedding,
                top_k,
                source,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, content, source, metadata, created_at, "
                "       1 - (embedding <=> $1) AS similarity "
                "FROM memories "
                "ORDER BY embedding <=> $1 "
                "LIMIT $2",
                query_embedding,
                top_k,
            )
        return [dict(r) for r in rows]


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)

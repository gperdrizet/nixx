"""Database schema and initialisation for the Nixx memory system.

Tables
------
buffer
    Persistent append-only tape of all messages across all frontends.

sources
    Meaningful units extracted explicitly from the buffer (or ingested from
    documents, repos, web pages). These feed the recall index.

memories
    Semantic memory store. Each row is an embedded source summary or document
    chunk, enabling cosine similarity search via pgvector.

All tables are created with CREATE TABLE IF NOT EXISTS on server startup.
Migrations for existing tables are applied inline via ALTER TABLE.
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
    """Create tables if they don't exist and apply any pending migrations."""
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS buffer (
                id          BIGSERIAL PRIMARY KEY,
                role        TEXT        NOT NULL,
                content     TEXT        NOT NULL,
                origin      TEXT        NOT NULL DEFAULT 'api',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id          BIGSERIAL PRIMARY KEY,
                name        TEXT        NOT NULL,
                type        TEXT        NOT NULL DEFAULT 'buffer',
                summary     TEXT        NOT NULL,
                start_id    BIGINT      REFERENCES buffer(id) ON DELETE SET NULL,
                end_id      BIGINT      REFERENCES buffer(id) ON DELETE SET NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS memories (
                id          BIGSERIAL PRIMARY KEY,
                content     TEXT        NOT NULL,
                embedding   vector({dimensions})  NOT NULL,
                source_id   BIGINT      REFERENCES sources(id) ON DELETE SET NULL,
                metadata    JSONB       NOT NULL DEFAULT '{{}}',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        # Migrate existing memories table: add source_id, drop legacy source column.
        await conn.execute("""
            ALTER TABLE memories
            ADD COLUMN IF NOT EXISTS source_id BIGINT REFERENCES sources(id) ON DELETE SET NULL;
        """)
        await conn.execute("""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'memories' AND column_name = 'source'
                ) THEN
                    ALTER TABLE memories DROP COLUMN source;
                END IF;
            END $$;
        """)

        # HNSW index for fast approximate nearest-neighbour search.
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS memories_embedding_hnsw
            ON memories
            USING hnsw (embedding vector_cosine_ops);
        """)

    logger.info("Database schema initialised")


# ── Buffer helpers ────────────────────────────────────────────────────────────


async def save_buffer_entry(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    role: str,
    content: str,
    origin: str = "api",
) -> int:
    """Append a message to the buffer. Returns the new id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO buffer (role, content, origin) VALUES ($1, $2, $3) RETURNING id",
            role,
            content,
            origin,
        )
        return int(row["id"])


async def get_buffer_entries(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    start_id: int,
    end_id: int,
) -> list[dict]:
    """Return buffer rows with id between start_id and end_id inclusive, oldest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, role, content, origin, created_at FROM buffer "
            "WHERE id >= $1 AND id <= $2 ORDER BY id ASC",
            start_id,
            end_id,
        )
        return [dict(r) for r in rows]


async def get_max_buffer_id(pool: asyncpg.Pool) -> int | None:  # type: ignore[type-arg]
    """Return the highest id in the buffer, or None if the buffer is empty."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT MAX(id) AS max_id FROM buffer")
        return int(row["max_id"]) if row and row["max_id"] is not None else None


# ── Source helpers ────────────────────────────────────────────────────────────


async def save_source(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    name: str,
    summary: str,
    type_: str = "buffer",
    start_id: int | None = None,
    end_id: int | None = None,
) -> int:
    """Insert a new source row. Returns the new id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO sources (name, type, summary, start_id, end_id) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            name,
            type_,
            summary,
            start_id,
            end_id,
        )
        return int(row["id"])


async def get_last_source_end_id(pool: asyncpg.Pool) -> int | None:  # type: ignore[type-arg]
    """Return the end_id of the most recently created source, or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT end_id FROM sources WHERE end_id IS NOT NULL ORDER BY id DESC LIMIT 1"
        )
        return int(row["end_id"]) if row else None


# ── Memory helpers ────────────────────────────────────────────────────────────


async def save_memory(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    content: str,
    embedding: list[float],
    source_id: int | None = None,
    metadata: dict | None = None,
) -> int:
    """Store a piece of text with its embedding vector. Returns the new id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO memories (content, embedding, source_id, metadata) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            content,
            embedding,
            source_id,
            json.dumps(metadata or {}),
        )
        return int(row["id"])


async def search_memories(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    query_embedding: list[float],
    top_k: int = 5,
) -> list[dict]:
    """Return the top-k most similar memories by cosine distance, closest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, source_id, metadata, created_at, "
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

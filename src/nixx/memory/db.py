"""Database schema and initialisation for the Nixx memory system.

Tables
------
buffer
    Persistent append-only tape of all messages across all frontends.
    Also serves as the episodic transcript - keyword searchable via
    tsvector + GIN index.

summaries
    Episodic memory summaries. Each row is an LLM-generated summary of N
    buffer entries, embedded for vector search. Contains extracted entities
    and user-provided tags for structured lookup.

sources
    Meaningful units extracted explicitly from the buffer (or ingested from
    documents, repos, web pages). These feed the semantic memory recall index.

memories
    Semantic memory store. Each row is an embedded source summary or document
    chunk, enabling cosine similarity search via pgvector.

All tables are created with CREATE TABLE IF NOT EXISTS on server startup.
Migrations for existing tables are applied inline via ALTER TABLE.
"""

from __future__ import annotations

import json
import logging

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

        # ── Episodic memory: summaries table ──

        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS summaries (
                id                  BIGSERIAL PRIMARY KEY,
                content             TEXT        NOT NULL,
                embedding           vector({dimensions})  NOT NULL,
                tags                TEXT[]      NOT NULL DEFAULT '{{}}',
                entities            JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
                start_buffer_id     BIGINT      REFERENCES buffer(id) ON DELETE SET NULL,
                end_buffer_id       BIGINT      REFERENCES buffer(id) ON DELETE SET NULL,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

        # ── Knowledge graph: source_projects and source_edges ──

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS source_projects (
                source_id   BIGINT  NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                project     TEXT    NOT NULL,
                PRIMARY KEY (source_id, project)
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS source_edges (
                from_id         BIGINT      NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                to_id           BIGINT      NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                relation        TEXT        NOT NULL,
                weight          FLOAT       NOT NULL DEFAULT 0.1,
                activations     INT         NOT NULL DEFAULT 0,
                last_activated  TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (from_id, to_id)
            );
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS source_edges_to_idx
            ON source_edges (to_id);
        """)

        # ── Indexes ──

        # HNSW index for fast approximate nearest-neighbour search.
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS memories_embedding_hnsw
            ON memories
            USING hnsw (embedding vector_cosine_ops);
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS summaries_embedding_hnsw
            ON summaries
            USING hnsw (embedding vector_cosine_ops);
        """)

        # Full-text search on buffer: tsvector column + GIN index.
        await conn.execute("""
            ALTER TABLE buffer
            ADD COLUMN IF NOT EXISTS tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS buffer_tsv_gin
            ON buffer USING gin (tsv);
        """)

        # ── Persistent server state ──

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS state (
                key         TEXT        PRIMARY KEY,
                value       TEXT        NOT NULL,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)

    logger.info("Database schema initialised")


# ── State helpers ─────────────────────────────────────────────────────────────


async def get_state(pool: asyncpg.Pool, key: str) -> str | None:  # type: ignore[type-arg]
    """Return the value for key from persistent state, or None if not set."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM state WHERE key = $1", key)
        return str(row["value"]) if row else None


async def set_state(pool: asyncpg.Pool, key: str, value: str) -> None:  # type: ignore[type-arg]
    """Upsert a key-value pair in persistent state."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO state (key, value, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            key,
            value,
        )


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


async def save_session_marker(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    origin: str = "tui",
) -> int:
    """Write a session marker to the buffer. Returns the new id."""
    return await save_buffer_entry(pool, role="marker", content="session_clear", origin=origin)


async def get_last_session_marker_id(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
) -> int | None:
    """Return the buffer id of the most recent session marker, or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT MAX(id) AS marker_id FROM buffer WHERE role = 'marker'")
        return int(row["marker_id"]) if row and row["marker_id"] is not None else None


async def get_current_session_entries(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    limit: int | None = None,
) -> list[dict]:
    """Return buffer entries after the last session marker, excluding markers.

    Args:
        pool: Database connection pool
        limit: Max entries to return (most recent first if limited)
    """
    async with pool.acquire() as conn:
        if limit is not None:
            # Get most recent N entries (reversed to get chronological order)
            rows = await conn.fetch(
                "SELECT id, role, content, origin, created_at FROM buffer "
                "WHERE role != 'marker' AND id > COALESCE("
                "  (SELECT MAX(id) FROM buffer WHERE role = 'marker'), 0"
                ") ORDER BY id DESC LIMIT $1",
                limit,
            )
            return [dict(r) for r in reversed(rows)]
        else:
            rows = await conn.fetch(
                "SELECT id, role, content, origin, created_at FROM buffer "
                "WHERE role != 'marker' AND id > COALESCE("
                "  (SELECT MAX(id) FROM buffer WHERE role = 'marker'), 0"
                ") ORDER BY id ASC",
            )
            return [dict(r) for r in rows]


async def search_buffer_fulltext(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Full-text search against buffer content. Returns matching rows ranked by relevance."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, role, content, origin, created_at, "
            "       ts_rank_cd(tsv, websearch_to_tsquery('english', $1)) AS rank "
            "FROM buffer "
            "WHERE tsv @@ websearch_to_tsquery('english', $1) "
            "  AND role != 'marker' "
            "ORDER BY rank DESC "
            "LIMIT $2",
            query,
            limit,
        )
        return [dict(r) for r in rows]


# ── Summary helpers ───────────────────────────────────────────────────────────


async def save_summary(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    content: str,
    embedding: list[float],
    tags: list[str],
    entities: dict,
    start_buffer_id: int,
    end_buffer_id: int,
) -> int:
    """Insert an episodic summary. Returns the new id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO summaries "
            "(content, embedding, tags, entities, start_buffer_id, end_buffer_id) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            content,
            embedding,
            tags,
            json.dumps(entities),
            start_buffer_id,
            end_buffer_id,
        )
        return int(row["id"])


async def search_summaries(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    query_embedding: list[float],
    top_k: int = 5,
) -> list[dict]:
    """Return the top-k most similar episodic summaries by cosine distance."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, tags, entities, start_buffer_id, end_buffer_id, "
            "       created_at, 1 - (embedding <=> $1) AS similarity "
            "FROM summaries "
            "ORDER BY embedding <=> $1 "
            "LIMIT $2",
            query_embedding,
            top_k,
        )
        return [dict(r) for r in rows]


async def get_last_summary_end_id(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
) -> int | None:
    """Return the end_buffer_id of the most recent summary, or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT end_buffer_id FROM summaries "
            "WHERE end_buffer_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1"
        )
        return int(row["end_buffer_id"]) if row else None


async def count_unsummarized_words(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
) -> tuple[int, int | None, int | None]:
    """Count words in buffer entries since the last summary.

    Returns (word_count, start_buffer_id, current_max_buffer_id).
    start_buffer_id is the first buffer id after the last summary.
    """
    last_end = await get_last_summary_end_id(pool)
    start_id = (last_end + 1) if last_end is not None else 1
    max_id = await get_max_buffer_id(pool)
    if max_id is None or max_id < start_id:
        return 0, start_id, max_id
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(array_length(string_to_array(content, ' '), 1)), 0) AS wc "
            "FROM buffer WHERE id >= $1 AND id <= $2 AND role != 'marker'",
            start_id,
            max_id,
        )
        return int(row["wc"]), start_id, max_id


async def list_summaries(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
) -> list[dict]:
    """Return all episodic summaries, newest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, tags, entities, start_buffer_id, end_buffer_id, created_at "
            "FROM summaries ORDER BY created_at DESC"
        )
        return [dict(r) for r in rows]


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


async def list_sources(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    name_filter: str | None = None,
) -> list[dict]:
    """List all sources, optionally filtered by name (case-insensitive pattern match)."""
    async with pool.acquire() as conn:
        if name_filter:
            rows = await conn.fetch(
                "SELECT id, name, type, summary, start_id, end_id, created_at "
                "FROM sources WHERE LOWER(name) LIKE LOWER($1) ORDER BY created_at DESC",
                f"%{name_filter}%",
            )
        else:
            rows = await conn.fetch(
                "SELECT id, name, type, summary, start_id, end_id, created_at "
                "FROM sources ORDER BY created_at DESC"
            )
        return [dict(r) for r in rows]


async def get_source(pool: asyncpg.Pool, source_id: int) -> dict | None:  # type: ignore[type-arg]
    """Get a single source by ID. Returns None if not found."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, type, summary, start_id, end_id, created_at "
            "FROM sources WHERE id = $1",
            source_id,
        )
        return dict(row) if row else None


async def get_source_content(pool: asyncpg.Pool, source_id: int) -> list[dict]:  # type: ignore[type-arg]
    """Get all memory chunks for a source, ordered by chunk index.

    Returns a list of dicts with: id, content, metadata, created_at.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content, metadata, created_at "
            "FROM memories WHERE source_id = $1 "
            "ORDER BY (metadata->>'chunk')::int",
            source_id,
        )
        return [dict(r) for r in rows]


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

"""Postgres connection pool.

Plain asyncpg rather than an ORM. The data model is small and fixed, the queries
that matter (hybrid vector + full-text fusion) are hand-written SQL that no ORM
would improve, and skipping the ORM keeps pgvector integration direct.

The pool is created lazily and *never* raises at import time: a database that is
still starting must degrade to a 503 with remediation text, not crash the app
before it can serve /api/health and explain itself.
"""

from __future__ import annotations

import asyncpg

from app.core.config import get_settings
from app.core.errors import DatabaseUnavailableError
from app.core.logging import db_log

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection setup.

    pgvector's asyncpg integration must be registered on every connection, not
    once on the pool, because asyncpg caches type codecs per connection.
    """
    from pgvector.asyncpg import register_vector

    try:
        await register_vector(conn)
    except Exception as exc:  # pragma: no cover - only if extension is absent
        # Not fatal: conversation endpoints work fine without vector support,
        # and this surfaces as a clear failure only when retrieval is used.
        db_log.warning("pgvector_registration_failed", error=str(exc))


async def get_pool() -> asyncpg.Pool:
    """Return the shared pool, creating it on first use."""
    global _pool
    if _pool is not None:
        return _pool

    settings = get_settings()
    try:
        _pool = await asyncpg.create_pool(
            dsn=settings.async_database_url,
            min_size=2,
            max_size=10,
            # Statements that hang hold a connection hostage; fail fast instead.
            command_timeout=60,
            init=_init_connection,
        )
        db_log.info(
            "pool_created",
            host=settings.postgres_host,
            database=settings.postgres_db,
            min_size=2,
            max_size=10,
        )
        return _pool
    except (OSError, asyncpg.PostgresError) as exc:
        db_log.error(
            "pool_creation_failed",
            host=settings.postgres_host,
            port=settings.postgres_port,
            error=str(exc),
        )
        raise DatabaseUnavailableError(
            "Could not connect to the database.",
            detail={"host": settings.postgres_host, "port": settings.postgres_port},
        ) from exc


async def close_pool() -> None:
    """Close the pool on shutdown so in-flight queries drain cleanly."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        db_log.info("pool_closed")


async def ping() -> bool:
    """Cheap liveness probe used by /api/health/ready."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception as exc:
        db_log.warning("db_ping_failed", error=str(exc))
        return False

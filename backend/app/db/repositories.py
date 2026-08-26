"""Data access for the conversation half of the schema.

Plain functions over the pool rather than a repository class hierarchy: there is
exactly one implementation of each of these and no polymorphism in sight, so
classes would be ceremony. Each function takes an explicit connection where it
participates in a caller's transaction, and acquires its own otherwise.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from app.core.logging import db_log
from app.db.pool import get_pool

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def ensure_user(user_id: UUID | None, metadata: dict[str, Any] | None = None) -> UUID:
    """Return a usable user id, creating the row if needed.

    The client mints its own UUID (localStorage) and sends it; we upsert rather
    than trust it blindly-but-separately. A client-supplied id that doesn't
    exist is simply created, which makes the anonymous-identity flow work
    across a database reset without the user noticing.
    """
    pool = await get_pool()
    meta = json.dumps(metadata or {})

    async with pool.acquire() as conn:
        if user_id is not None:
            row = await conn.fetchrow(
                """
                INSERT INTO users (id, metadata) VALUES ($1, $2::jsonb)
                ON CONFLICT (id) DO UPDATE
                    SET last_seen_at = now(),
                        metadata = users.metadata || EXCLUDED.metadata
                RETURNING id
                """,
                user_id,
                meta,
            )
        else:
            row = await conn.fetchrow(
                "INSERT INTO users (metadata) VALUES ($1::jsonb) RETURNING id", meta
            )
    return row["id"]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def create_session(
    user_id: UUID,
    *,
    title: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> asyncpg.Record:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sessions (user_id, title, provider, model)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            user_id,
            title,
            provider,
            model,
        )
    db_log.info("session_created", new_session_id=str(row["id"]), user_id=str(user_id))
    return row


async def get_session(session_id: UUID) -> asyncpg.Record | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM sessions WHERE id = $1", session_id)


async def list_sessions(user_id: UUID, limit: int = 50) -> list[asyncpg.Record]:
    """Sessions for the sidebar, newest activity first, with message counts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT s.*,
                   COALESCE(m.message_count, 0) AS message_count
            FROM sessions s
            LEFT JOIN (
                SELECT session_id, COUNT(*) AS message_count
                FROM messages GROUP BY session_id
            ) m ON m.session_id = s.id
            WHERE s.user_id = $1
            ORDER BY s.updated_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )


async def set_session_title(session_id: UUID, title: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE sessions SET title = $2 WHERE id = $1 AND title IS NULL",
            session_id,
            title[:120],
        )


async def touch_session(session_id: UUID) -> None:
    """Bump updated_at so sidebar ordering reflects real activity."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE sessions SET updated_at = now() WHERE id = $1", session_id)


async def delete_session(session_id: UUID) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM sessions WHERE id = $1", session_id)
    return result.endswith("1")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def add_message(
    session_id: UUID,
    role: str,
    content: str,
    *,
    skill: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    latency_ms: int | None = None,
    token_usage: dict | None = None,
    insufficient_evidence: bool = False,
) -> asyncpg.Record:
    """Append a message, assigning the next sequence number atomically.

    seq is computed inside the INSERT under a transaction rather than read-then-
    write, so two concurrent turns in the same session cannot collide on the
    (session_id, seq) unique constraint.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO messages (
                session_id, seq, role, content, skill, provider, model,
                latency_ms, token_usage, insufficient_evidence
            )
            VALUES (
                $1,
                (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = $1),
                $2, $3, $4, $5, $6, $7, $8::jsonb, $9
            )
            RETURNING *
            """,
            session_id,
            role,
            content,
            skill,
            provider,
            model,
            latency_ms,
            json.dumps(token_usage) if token_usage else None,
            insufficient_evidence,
        )
        await conn.execute("UPDATE sessions SET updated_at = now() WHERE id = $1", session_id)
    return row


async def get_messages(session_id: UUID, limit: int = 200) -> list[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM messages WHERE session_id = $1 ORDER BY seq ASC LIMIT $2",
            session_id,
            limit,
        )


async def get_history_for_prompt(session_id: UUID, max_turns: int = 8) -> list[dict[str, str]]:
    """Recent turns as provider-neutral {role, content} dicts.

    Trimmed to the last `max_turns` exchanges: a 7B local model's context is the
    binding constraint, and old turns compete for the same budget as retrieved
    evidence — which is the thing that actually keeps answers grounded.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content FROM (
                SELECT role, content, seq FROM messages
                WHERE session_id = $1 AND role IN ('user', 'assistant')
                ORDER BY seq DESC LIMIT $2
            ) recent ORDER BY seq ASC
            """,
            session_id,
            max_turns * 2,
        )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


async def add_citations(message_id: UUID, citations: list[dict[str, Any]]) -> None:
    """Persist the evidence an answer was built on.

    Stores an immutable snapshot (quote, episode, timestamp, URL) alongside the
    chunk FK so the citation survives a corpus rebuild that removes the chunk.
    """
    if not citations:
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO citations (
                message_id, chunk_id, label, rank, score, quote,
                episode_slug, episode_title, guest, start_seconds, source_url
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """,
            [
                (
                    message_id,
                    c.get("chunk_id"),
                    c["label"],
                    c.get("rank", i),
                    c.get("score"),
                    c.get("quote"),
                    c.get("episode_slug"),
                    c.get("episode_title"),
                    c.get("guest"),
                    c.get("start_seconds"),
                    c.get("source_url"),
                )
                for i, c in enumerate(citations)
            ],
        )


async def get_citations(message_ids: list[UUID]) -> dict[UUID, list[asyncpg.Record]]:
    """Citations grouped by message id — one query for a whole conversation."""
    if not message_ids:
        return {}

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM citations WHERE message_id = ANY($1::uuid[]) ORDER BY rank ASC",
            message_ids,
        )

    grouped: dict[UUID, list[asyncpg.Record]] = {}
    for row in rows:
        grouped.setdefault(row["message_id"], []).append(row)
    return grouped


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


async def create_artifact(
    session_id: UUID,
    *,
    kind: str,
    title: str,
    content: str,
    template: str | None = None,
    message_id: UUID | None = None,
    metadata: dict | None = None,
) -> asyncpg.Record:
    """Insert an artifact, auto-incrementing version per (session, title).

    Regenerating keeps the previous draft rather than overwriting it — losing a
    1,250-word essay to a retry would be a genuinely bad experience.
    """
    pool = await get_pool()
    word_count = len(content.split())

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO artifacts (
                session_id, message_id, kind, title, content, template,
                version, word_count, metadata
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                (SELECT COALESCE(MAX(version), 0) + 1 FROM artifacts
                 WHERE session_id = $1 AND title = $4),
                $7, $8::jsonb
            )
            RETURNING *
            """,
            session_id,
            message_id,
            kind,
            title,
            content,
            template,
            word_count,
            json.dumps(metadata or {}),
        )
    db_log.info(
        "artifact_created",
        artifact_id=str(row["id"]),
        kind=kind,
        version=row["version"],
        word_count=word_count,
    )
    return row


async def get_artifact(artifact_id: UUID) -> asyncpg.Record | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM artifacts WHERE id = $1", artifact_id)


async def list_artifacts(session_id: UUID) -> list[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, session_id, message_id, kind, title, template, version,
                   word_count, created_at, updated_at
            FROM artifacts WHERE session_id = $1 ORDER BY created_at DESC
            """,
            session_id,
        )


async def update_artifact(artifact_id: UUID, content: str) -> asyncpg.Record | None:
    """In-place edit from the viewer. Does not bump version — edits are the
    user's own text, whereas version tracks *generations*."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "UPDATE artifacts SET content = $2, word_count = $3 WHERE id = $1 RETURNING *",
            artifact_id,
            content,
            len(content.split()),
        )

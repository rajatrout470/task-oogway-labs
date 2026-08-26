"""SQL layer for retrieval.

Hybrid search: dense vector similarity AND Postgres full-text, fused. Both are
needed and neither is sufficient:

  * Vector search handles paraphrase — "how do I know if we have PMF?" finds a
    passage that never uses the phrase "product-market fit".
  * Full-text handles the exact tokens embeddings are worst at: proper nouns,
    product names, acronyms, numbers. A query for "ICE framework" or "Superhuman"
    must not be defeated by semantic drift.

Fusion is Reciprocal Rank Fusion, which combines *ranks* rather than scores and
so needs no calibration between two incomparable scoring scales.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.logging import retrieval_log
from app.db.pool import get_pool


@dataclass
class RetrievedChunk:
    """A candidate passage with everything needed to cite it."""

    chunk_id: UUID
    episode_id: UUID
    ord: int
    text: str
    speaker: str | None
    start_seconds: int | None
    episode_slug: str
    episode_title: str
    guest: str
    youtube_url: str | None
    publish_date: str | None
    has_timestamps: bool

    # Cosine similarity in [0, 1]. The interpretable relevance signal, used for
    # the insufficient-evidence threshold.
    similarity: float = 0.0
    # Postgres ts_rank. Scale is arbitrary, useful only for ordering.
    text_rank: float = 0.0
    # Fused rank score. Determines final ordering.
    fused_score: float = 0.0

    @property
    def source_url(self) -> str | None:
        """Deep link to the exact second in the source video.

        Returns a plain episode link when the source has no timestamps, and
        None when there is no URL at all — never a fabricated timestamp.
        """
        if not self.youtube_url:
            return None
        if self.start_seconds is None or not self.has_timestamps:
            return self.youtube_url
        separator = "&" if "?" in self.youtube_url else "?"
        return f"{self.youtube_url}{separator}t={self.start_seconds}s"

    @property
    def timestamp_label(self) -> str | None:
        """'1:04:22' for display. None when the source carries no timestamps."""
        if self.start_seconds is None:
            return None
        h, rem = divmod(self.start_seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_SELECT_COLUMNS = """
    c.id            AS chunk_id,
    c.episode_id    AS episode_id,
    c.ord           AS ord,
    c.text          AS text,
    c.speaker       AS speaker,
    c.start_seconds AS start_seconds,
    e.slug          AS episode_slug,
    e.title         AS episode_title,
    e.guest         AS guest,
    e.youtube_url   AS youtube_url,
    e.publish_date  AS publish_date,
    e.has_timestamps AS has_timestamps
"""


def _to_chunk(row) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=row["chunk_id"],
        episode_id=row["episode_id"],
        ord=row["ord"],
        text=row["text"],
        speaker=row["speaker"],
        start_seconds=row["start_seconds"],
        episode_slug=row["episode_slug"],
        episode_title=row["episode_title"],
        guest=row["guest"],
        youtube_url=row["youtube_url"],
        publish_date=row["publish_date"].isoformat() if row["publish_date"] else None,
        has_timestamps=row["has_timestamps"],
        similarity=float(row.get("similarity") or 0.0),
        text_rank=float(row.get("text_rank") or 0.0),
    )


async def vector_search(
    embedding: list[float],
    limit: int,
    *,
    guest: str | None = None,
    published_after: str | None = None,
) -> list[RetrievedChunk]:
    """Dense retrieval by cosine distance.

    `1 - (embedding <=> query)` converts pgvector's cosine *distance* into a
    similarity in [0, 1], which is the number the relevance threshold reads.
    """
    # The embedding is passed as a plain list, not a JSON string: pool.py
    # registers pgvector's asyncpg codec on every connection, so asyncpg encodes
    # it into the `vector` wire format itself. A string plus an explicit
    # ::vector cast fights that codec and raises at bind time.
    conditions, params = [], [embedding]

    if guest:
        params.append(f"%{guest}%")
        conditions.append(f"AND e.guest ILIKE ${len(params)}")
    if published_after:
        params.append(published_after)
        conditions.append(f"AND e.publish_date >= ${len(params)}::date")

    params.append(limit)

    sql = f"""
        SELECT {_SELECT_COLUMNS},
               1 - (c.embedding <=> $1) AS similarity,
               0.0 AS text_rank
        FROM chunks c
        JOIN episodes e ON e.id = c.episode_id
        WHERE c.embedding IS NOT NULL
        {' '.join(conditions)}
        ORDER BY c.embedding <=> $1
        LIMIT ${len(params)}
    """

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_to_chunk(r) for r in rows]


async def keyword_search(
    query: str,
    limit: int,
    *,
    guest: str | None = None,
    published_after: str | None = None,
) -> list[RetrievedChunk]:
    """Sparse retrieval over the generated tsvector column.

    websearch_to_tsquery (not plainto_tsquery) so quoted phrases and OR/-
    operators typed by a user behave the way they do in a search engine, and a
    malformed query degrades gracefully instead of raising.
    """
    conditions, params = [], [query]

    if guest:
        params.append(f"%{guest}%")
        conditions.append(f"AND e.guest ILIKE ${len(params)}")
    if published_after:
        params.append(published_after)
        conditions.append(f"AND e.publish_date >= ${len(params)}::date")

    params.append(limit)

    sql = f"""
        SELECT {_SELECT_COLUMNS},
               0.0 AS similarity,
               ts_rank(c.tsv, websearch_to_tsquery('english', $1)) AS text_rank
        FROM chunks c
        JOIN episodes e ON e.id = c.episode_id
        WHERE c.tsv @@ websearch_to_tsquery('english', $1)
        {' '.join(conditions)}
        ORDER BY text_rank DESC
        LIMIT ${len(params)}
    """

    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(sql, *params)
        except Exception as exc:
            # A tsquery parse failure must not take down the whole search —
            # vector results alone are still a useful answer.
            retrieval_log.warning("keyword_search_failed", error=str(exc))
            return []
    return [_to_chunk(r) for r in rows]


async def get_episode_chunks(episode_id: UUID) -> list[RetrievedChunk]:
    """Every chunk of one episode, in order.

    Backs the second-stage 'read the whole episode' escalation (PRD A2): a
    median episode is ~20k tokens, which fits comfortably in a cloud model's
    context even though the full corpus never could.
    """
    sql = f"""
        SELECT {_SELECT_COLUMNS}, 0.0 AS similarity, 0.0 AS text_rank
        FROM chunks c JOIN episodes e ON e.id = c.episode_id
        WHERE c.episode_id = $1 ORDER BY c.ord ASC
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, episode_id)
    return [_to_chunk(r) for r in rows]


async def find_episodes(query: str, limit: int = 5) -> list[dict]:
    """Fuzzy episode lookup by guest or title, for 'what did X say about...'."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, slug, guest, title, publish_date, youtube_url,
                   GREATEST(similarity(guest, $1), similarity(title, $1)) AS score
            FROM episodes
            WHERE guest ILIKE '%' || $1 || '%'
               OR title ILIKE '%' || $1 || '%'
               OR similarity(guest, $1) > 0.3
            ORDER BY score DESC
            LIMIT $2
            """,
            query,
            limit,
        )
    return [dict(r) for r in rows]


async def adjacent_topics(limit: int = 12) -> list[str]:
    """Most common corpus keywords.

    Powers the insufficient-evidence empty state: when we can't answer, showing
    what the corpus *does* cover is far more useful than an apology.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT keyword, COUNT(*) AS n
            FROM episodes, UNNEST(keywords) AS keyword
            GROUP BY keyword ORDER BY n DESC LIMIT $1
            """,
            limit,
        )
    return [r["keyword"] for r in rows]


async def count_chunks() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM chunks") or 0

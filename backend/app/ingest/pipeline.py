"""Transcript ingestion pipeline.

Flow:

    clone/pull source repo  ->  resolve commit SHA  ->  for each episode:
      parse frontmatter + turns  ->  skip if content_hash unchanged
      ->  chunk on turn boundaries  ->  embed  ->  upsert episode + chunks
    ->  rebuild the vector index  ->  record the run

Two properties matter more than speed:

1. **Idempotence.** Re-running must be safe and cheap. Each episode's raw file
   is SHA-256'd; unchanged episodes are skipped without re-embedding, which
   turns a ~20 minute cold ingest into a seconds-long no-op refresh.

2. **Traceability.** Every chunk records the commit SHA it came from, so any
   answer can be traced to an exact source state — and a corpus refresh that
   changes an episode is visible rather than silent.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.core.config import Settings, get_settings
from app.core.logging import ingest_log
from app.db.pool import get_pool
from app.ingest.chunker import ParseError, chunk_turns, parse_transcript
from app.retrieval.embeddings import EmbeddingClient


@dataclass
class IngestStats:
    source_commit: str = ""
    episodes_total: int = 0
    episodes_ingested: int = 0
    episodes_skipped: int = 0
    episodes_failed: int = 0
    chunks_written: int = 0
    duration_seconds: float = 0.0
    failures: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []


# ---------------------------------------------------------------------------
# Source acquisition
# ---------------------------------------------------------------------------


def sync_corpus(settings: Settings | None = None) -> tuple[Path, str]:
    """Clone or update the transcripts repo. Returns (path, commit_sha).

    The corpus is fetched at ingest time and never vendored into this
    repository (PRD assumption A5): the transcripts are third-party copyrighted
    material, so we reference the upstream source rather than redistributing a
    copy of it.
    """
    settings = settings or get_settings()
    path = Path(settings.corpus_local_path).resolve()

    if (path / ".git").exists():
        ingest_log.info("corpus_updating", path=str(path), ref=settings.corpus_repo_ref)
        _git(["fetch", "--depth", "1", "origin", settings.corpus_repo_ref], cwd=path)
        _git(["checkout", "-f", "FETCH_HEAD"], cwd=path)
    else:
        if path.exists():
            # A partial clone from an interrupted run would fail every git
            # command below with a confusing message; start clean instead.
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ingest_log.info("corpus_cloning", url=settings.corpus_repo_url, path=str(path))
        _git([
            "clone", "--depth", "1",
            "--branch", settings.corpus_repo_ref,
            settings.corpus_repo_url, str(path),
        ])

    commit = _git(["rev-parse", "HEAD"], cwd=path).strip()
    ingest_log.info("corpus_ready", commit=commit[:12], path=str(path))
    return path, commit


def _git(args: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {' '.join(args)} timed out after 300s") from exc


def discover_episodes(corpus_path: Path) -> list[tuple[str, Path]]:
    """Find every episodes/<slug>/transcript.md. Returns [(slug, path), ...]."""
    episodes_dir = corpus_path / "episodes"
    if not episodes_dir.is_dir():
        raise FileNotFoundError(
            f"No 'episodes' directory in {corpus_path}. "
            "Is CORPUS_REPO_URL pointing at the transcripts repository?"
        )

    found = [
        (d.name, d / "transcript.md")
        for d in sorted(episodes_dir.iterdir())
        if d.is_dir() and (d / "transcript.md").is_file()
    ]
    ingest_log.info("episodes_discovered", count=len(found))
    return found


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def run_ingest(*, force: bool = False, limit: int | None = None) -> IngestStats:
    """Ingest the corpus. Returns statistics.

    force=True re-embeds every episode even if unchanged — needed after
    switching embedding models or changing chunking parameters, since neither
    alters the source file hash.
    """
    settings = get_settings()
    started = time.monotonic()
    stats = IngestStats()

    embedder = EmbeddingClient(settings)
    ok, reason = await embedder.health()
    if not ok:
        # Fail before cloning 25MB and parsing 300 files, not after.
        ingest_log.error("ingest_aborted_embedder_unavailable", reason=reason)
        raise RuntimeError(f"Cannot ingest: {reason}")

    corpus_path, commit = sync_corpus(settings)
    stats.source_commit = commit

    episodes = discover_episodes(corpus_path)
    limit = limit if limit is not None else (settings.ingest_episode_limit or None)
    if limit:
        episodes = episodes[:limit]
        ingest_log.info("episode_limit_applied", limit=limit)

    stats.episodes_total = len(episodes)
    pool = await get_pool()
    run_id = await _start_run(commit, settings)

    try:
        existing = await _existing_hashes()

        for index, (slug, path) in enumerate(episodes, start=1):
            try:
                if not force and existing.get(slug) == _file_hash(path):
                    stats.episodes_skipped += 1
                    continue

                written = await _ingest_episode(
                    slug, path, commit, embedder, settings, corpus_path
                )
                stats.episodes_ingested += 1
                stats.chunks_written += written

                if index % 10 == 0 or index == len(episodes):
                    ingest_log.info(
                        "ingest_progress",
                        processed=index,
                        total=len(episodes),
                        ingested=stats.episodes_ingested,
                        skipped=stats.episodes_skipped,
                        chunks=stats.chunks_written,
                    )

            except ParseError as exc:
                # One unparseable episode must not abort a 300-episode run, but
                # it must be counted and reported — never silently dropped.
                stats.episodes_failed += 1
                stats.failures.append(f"{slug}: {exc}")
                ingest_log.error("episode_parse_failed", slug=slug, error=str(exc))
            except Exception as exc:
                stats.episodes_failed += 1
                stats.failures.append(f"{slug}: {exc}")
                ingest_log.error("episode_ingest_failed", slug=slug, error=str(exc))

        if stats.episodes_ingested:
            await _finalize_index(pool)

        stats.duration_seconds = round(time.monotonic() - started, 1)
        await _finish_run(run_id, stats, status="succeeded")

        ingest_log.info(
            "ingest_complete",
            commit=commit[:12],
            total=stats.episodes_total,
            ingested=stats.episodes_ingested,
            skipped=stats.episodes_skipped,
            failed=stats.episodes_failed,
            chunks=stats.chunks_written,
            duration_seconds=stats.duration_seconds,
        )
        return stats

    except Exception as exc:
        stats.duration_seconds = round(time.monotonic() - started, 1)
        await _finish_run(run_id, stats, status="failed", error=str(exc))
        raise


async def _ingest_episode(
    slug: str,
    path: Path,
    commit: str,
    embedder: EmbeddingClient,
    settings: Settings,
    corpus_path: Path,
) -> int:
    """Parse, chunk, embed and persist one episode. Returns chunks written."""
    parsed = parse_transcript(path, slug)
    chunks = chunk_turns(
        parsed.turns,
        target_tokens=settings.chunk_target_tokens,
        overlap_turns=settings.chunk_overlap_turns,
    )
    if not chunks:
        raise ParseError(f"{slug}: parsed {len(parsed.turns)} turns but produced no chunks")

    # kind="document": asymmetric embedding models need the indexing-side task
    # prefix here and the query-side prefix at search time. Passing this
    # explicitly (rather than relying on the default) keeps the asymmetry
    # visible at both call sites.
    vectors = await embedder.embed([c.text for c in chunks], kind="document")

    meta = parsed.metadata
    pool = await get_pool()

    async with pool.acquire() as conn, conn.transaction():
        episode_id: UUID = await conn.fetchval(
            """
            INSERT INTO episodes (
                slug, guest, title, youtube_url, video_id, publish_date,
                description, duration_seconds, view_count, channel, keywords,
                transcript_format, has_timestamps,
                source_commit, content_hash, source_path
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (slug) DO UPDATE SET
                guest = EXCLUDED.guest,
                title = EXCLUDED.title,
                youtube_url = EXCLUDED.youtube_url,
                video_id = EXCLUDED.video_id,
                publish_date = EXCLUDED.publish_date,
                description = EXCLUDED.description,
                duration_seconds = EXCLUDED.duration_seconds,
                view_count = EXCLUDED.view_count,
                channel = EXCLUDED.channel,
                keywords = EXCLUDED.keywords,
                transcript_format = EXCLUDED.transcript_format,
                has_timestamps = EXCLUDED.has_timestamps,
                source_commit = EXCLUDED.source_commit,
                content_hash = EXCLUDED.content_hash,
                source_path = EXCLUDED.source_path
            RETURNING id
            """,
            slug,
            str(meta.get("guest") or slug.replace("-", " ").title()),
            str(meta.get("title") or slug),
            _opt_str(meta.get("youtube_url")),
            _opt_str(meta.get("video_id")),
            _parse_date(meta.get("publish_date")),
            _opt_str(meta.get("description")),
            _opt_int(meta.get("duration_seconds")),
            _opt_int(meta.get("view_count")),
            _opt_str(meta.get("channel")),
            _keywords(meta.get("keywords")),
            parsed.transcript_format,
            parsed.has_timestamps,
            commit,
            parsed.content_hash,
            str(path.relative_to(corpus_path)),
        )

        # Replace-all rather than diff: chunk boundaries shift when a transcript
        # is corrected upstream, so ordinals are not stable across versions and
        # a partial update would leave orphaned passages behind.
        await conn.execute("DELETE FROM chunks WHERE episode_id = $1", episode_id)

        await conn.executemany(
            """
            INSERT INTO chunks (
                episode_id, ord, speaker, start_seconds, end_seconds,
                text, token_estimate, embedding, source_commit
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            [
                (
                    episode_id,
                    c.ord,
                    c.speaker,
                    c.start_seconds,
                    c.end_seconds,
                    c.text,
                    c.token_estimate,
                    # Passed as a plain list: pool.py registers pgvector's
                    # asyncpg codec on every connection, so asyncpg encodes the
                    # list into the `vector` wire format itself. An explicit
                    # ::vector cast with a string literal fights that codec and
                    # fails — the parameter type is already known from the column.
                    v,
                    commit,
                )
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )

    return len(chunks)


async def _finalize_index(pool) -> None:
    """Rebuild the IVFFlat index and refresh planner statistics.

    IVFFlat builds its centroid lists from the data present at build time. The
    index created by the migration on an empty table is effectively useless, so
    it must be rebuilt once the corpus is loaded or vector search silently
    degrades to a sequential scan.
    """
    ingest_log.info("index_rebuild_start")
    started = time.monotonic()
    async with pool.acquire() as conn:
        await conn.execute("REINDEX INDEX idx_chunks_embedding")
        await conn.execute("ANALYZE chunks")
    ingest_log.info("index_rebuild_complete", duration_seconds=round(time.monotonic() - started, 1))


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------


async def _start_run(commit: str, settings: Settings) -> UUID:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO ingest_runs (source_commit, source_ref, embedding_model)
            VALUES ($1, $2, $3) RETURNING id
            """,
            commit,
            settings.corpus_repo_ref,
            settings.ollama_embed_model,
        )


async def _finish_run(
    run_id: UUID, stats: IngestStats, *, status: str, error: str | None = None
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ingest_runs SET
                finished_at = now(), status = $2, error = $3,
                episodes_total = $4, episodes_ingested = $5,
                episodes_skipped = $6, chunks_written = $7
            WHERE id = $1
            """,
            run_id,
            status,
            error or ("; ".join(stats.failures[:5]) if stats.failures else None),
            stats.episodes_total,
            stats.episodes_ingested,
            stats.episodes_skipped,
            stats.chunks_written,
        )


async def _existing_hashes() -> dict[str, str]:
    """slug -> content_hash for every ingested episode, for skip decisions."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT slug, content_hash FROM episodes")
    return {r["slug"]: r["content_hash"] for r in rows}


async def corpus_status() -> dict:
    """Knowledge-base summary for /api/health/ready and the UI."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM episodes) AS episodes,
                (SELECT COUNT(*) FROM chunks)   AS chunks,
                (SELECT MAX(source_commit) FROM episodes) AS commit,
                (SELECT MAX(ingested_at) FROM episodes)   AS last_ingested
            """
        )
    return {
        "episodes": row["episodes"],
        "chunks": row["chunks"],
        "source_commit": (row["commit"] or "")[:12] or None,
        "last_ingested_at": row["last_ingested"].isoformat() if row["last_ingested"] else None,
        "ready": bool(row["chunks"]),
    }


# ---------------------------------------------------------------------------
# Coercion helpers — frontmatter is human-authored and inconsistently typed.
# ---------------------------------------------------------------------------


def _file_hash(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _opt_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    from datetime import date, datetime

    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _keywords(value) -> list[str]:
    if isinstance(value, list):
        return [str(k).strip() for k in value if str(k).strip()][:40]
    if isinstance(value, str):
        return [k.strip() for k in value.split(",") if k.strip()][:40]
    return []


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run_ingest())

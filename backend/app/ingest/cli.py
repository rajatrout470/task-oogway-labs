"""Ingestion CLI.

    python -m app.ingest.cli run              # incremental (skips unchanged)
    python -m app.ingest.cli run --force      # re-embed everything
    python -m app.ingest.cli run --limit 10   # quick smoke ingest
    python -m app.ingest.cli status           # what's currently indexed

A CLI rather than an API endpoint: ingestion is a minutes-long batch job, and
putting it behind HTTP would invite request timeouts and an accidental
denial-of-service on the embedding model.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging, ingest_log
from app.db.pool import close_pool


async def _run(force: bool, limit: int | None) -> int:
    from app.ingest.pipeline import run_ingest

    try:
        stats = await run_ingest(force=force, limit=limit)
    except Exception as exc:
        ingest_log.error("ingest_failed", error=str(exc))
        print(f"\n  ✗ Ingestion failed: {exc}\n", file=sys.stderr)
        return 1
    finally:
        await close_pool()

    print("\n  Ingestion complete")
    print(f"    source commit : {stats.source_commit[:12]}")
    print(f"    episodes      : {stats.episodes_ingested} ingested, "
          f"{stats.episodes_skipped} unchanged, {stats.episodes_failed} failed")
    print(f"    chunks written: {stats.chunks_written:,}")
    print(f"    duration      : {stats.duration_seconds}s\n")

    if stats.failures:
        print("  Failures:", file=sys.stderr)
        for failure in stats.failures[:10]:
            print(f"    - {failure}", file=sys.stderr)
        print(file=sys.stderr)

    # Non-zero exit when nothing landed, so CI and `make ingest` fail loudly
    # rather than leaving an empty knowledge base that only shows up as the
    # assistant claiming it knows nothing.
    return 1 if stats.episodes_failed and not stats.episodes_ingested else 0


async def _status() -> int:
    from app.ingest.pipeline import corpus_status

    try:
        status = await corpus_status()
    finally:
        await close_pool()

    print("\n  Knowledge base")
    print(f"    episodes      : {status['episodes']}")
    print(f"    chunks        : {status['chunks']:,}")
    print(f"    source commit : {status['source_commit'] or '—'}")
    print(f"    last ingested : {status['last_ingested_at'] or '—'}")
    print(f"    ready         : {'yes' if status['ready'] else 'NO — run `make ingest`'}\n")
    return 0 if status["ready"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="ingest", description="Transcript ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Ingest or refresh the corpus")
    run_cmd.add_argument(
        "--force",
        action="store_true",
        help="Re-embed every episode even if unchanged (needed after changing "
             "the embedding model or chunking parameters)",
    )
    run_cmd.add_argument("--limit", type=int, default=None, help="Only ingest N episodes")

    sub.add_parser("status", help="Show what is currently indexed")

    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    if args.command == "run":
        sys.exit(asyncio.run(_run(args.force, args.limit)))
    else:
        sys.exit(asyncio.run(_status()))


if __name__ == "__main__":
    main()

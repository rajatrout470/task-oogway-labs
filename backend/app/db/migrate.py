"""Migration runner.

Deliberately not Alembic. The schema is a handful of tables authored as plain
SQL, every statement is written `IF NOT EXISTS` / `CREATE OR REPLACE`, and the
files are applied in filename order exactly once each (tracked in
schema_migrations). That gives idempotent startup migrations with no autogenerate
machinery to misread the pgvector column type.

Run standalone:  python -m app.db.migrate
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import psycopg

from app.core.config import get_settings
from app.core.logging import configure_logging, db_log

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _discover() -> list[Path]:
    if not MIGRATIONS_DIR.exists():
        raise FileNotFoundError(f"migrations directory not found: {MIGRATIONS_DIR}")
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def run_migrations() -> int:
    """Apply every pending migration. Returns the number applied."""
    settings = get_settings()
    applied = 0

    with psycopg.connect(settings.sync_database_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(_TRACKING_TABLE)
            conn.commit()

            cur.execute("SELECT filename, checksum FROM schema_migrations")
            already = dict(cur.fetchall())

        for path in _discover():
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()[:16]

            if path.name in already:
                if already[path.name] != checksum:
                    # An edited applied migration means the DB no longer matches
                    # the repo. Warn loudly rather than silently re-running.
                    db_log.warning(
                        "migration_checksum_mismatch",
                        filename=path.name,
                        hint="An already-applied migration file was edited. "
                        "Create a new migration instead.",
                    )
                continue

            db_log.info("migration_applying", filename=path.name)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                        (path.name, checksum),
                    )
                conn.commit()
                applied += 1
                db_log.info("migration_applied", filename=path.name)
            except Exception as exc:
                conn.rollback()
                db_log.error("migration_failed", filename=path.name, error=str(exc))
                raise

    db_log.info("migrations_complete", applied=applied)
    return applied


if __name__ == "__main__":
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    try:
        run_migrations()
    except Exception as exc:
        db_log.error("migrations_aborted", error=str(exc))
        sys.exit(1)

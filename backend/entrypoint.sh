#!/usr/bin/env bash
# =============================================================================
# Container entrypoint.
#
# Responsibilities, in order:
#   1. Wait for Postgres to accept connections (compose healthcheck covers the
#      common case, but a slow first initdb can still race).
#   2. Apply migrations idempotently.
#   3. Exec the real command.
#
# Deliberately does NOT run ingestion: that is a minutes-long batch job and
# blocking startup on it would make the stack look hung.
# =============================================================================
set -euo pipefail

DB_HOST="${POSTGRES_HOST:-db}"
DB_PORT="${POSTGRES_PORT:-5432}"

echo "[entrypoint] waiting for postgres at ${DB_HOST}:${DB_PORT} ..."
for i in $(seq 1 60); do
    if python -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('${DB_HOST}', ${DB_PORT}))
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
        echo "[entrypoint] postgres is up (after ${i} attempt(s))"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "[entrypoint] ERROR: postgres unreachable after 60 attempts" >&2
        echo "[entrypoint] check that the 'db' service is healthy: docker compose ps" >&2
        exit 1
    fi
    sleep 1
done

echo "[entrypoint] applying migrations ..."
python -m app.db.migrate

echo "[entrypoint] starting: $*"
exec "$@"

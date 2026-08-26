# =============================================================================
# Backend image.
#
# Python is pinned to 3.12 rather than tracking latest: several of our
# dependencies (asyncpg, pgvector) do not consistently publish wheels for the
# newest interpreter, and a source build inside a slim image is a slow, silent
# failure mode. See PRD risk "Toolchain sharpness".
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# git: the ingestion pipeline clones the transcripts repo at runtime rather than
#      vendoring it (PRD A5).
# curl: container healthchecks and Ollama reachability probes.
# nodejs/npm: required by the Claude Agent SDK, which drives the `claude` CLI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# The Claude Agent SDK is a Python wrapper around the Claude Code CLI, so the
# CLI itself must be present for ANTHROPIC_AGENT_RUNTIME=sdk. Installation is
# best-effort: the mandated demo path is local Ollama, which needs none of this,
# and a network hiccup here must not break the fully-local build.
RUN npm install -g @anthropic-ai/claude-code || \
    echo "WARN: claude CLI unavailable; ANTHROPIC_AGENT_RUNTIME=sdk will fall back to 'messages'"

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e ".[dev]"

COPY . .

# Non-root: the container writes only to /app/data, which we chown explicitly.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data/corpus \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Migrations run at startup (idempotent, CREATE ... IF NOT EXISTS) so a fresh
# clone needs exactly one command. entrypoint.sh also waits for Postgres.
COPY --chown=appuser:appuser entrypoint.sh /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

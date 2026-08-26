"""Health endpoints.

Three levels, because they answer three different operational questions:

  GET /api/health/live    Is the process up? (container healthcheck)
  GET /api/health/ready   Can it actually serve a grounded answer?
  GET /api/health         Everything, with per-dependency detail.

The distinction matters. `live` must not depend on Postgres or Ollama — an
orchestrator that restarts the container because the database is slow makes an
outage worse. `ready` deliberately does depend on them, because a process that
is up but has no corpus and no model cannot do its job, and load balancers and
humans both need to know that.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.schemas import HealthResponse
from app.core.config import get_settings
from app.db import pool
from app.providers.registry import provider_status

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/live", summary="Liveness — is the process running?")
async def live() -> dict:
    """Intentionally dependency-free. Answers only 'is this process alive?'"""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness — can it serve a grounded answer?")
async def ready(response: Response) -> dict:
    """Checks the dependencies actually required to answer a question."""
    checks: dict = {}

    db_ok = await pool.ping()
    checks["database"] = {"ok": db_ok}

    corpus_ok = False
    if db_ok:
        try:
            from app.ingest.pipeline import corpus_status

            corpus = await corpus_status()
            corpus_ok = corpus["ready"]
            checks["knowledge_base"] = corpus
        except Exception as exc:
            checks["knowledge_base"] = {"ok": False, "error": str(exc)}
    else:
        checks["knowledge_base"] = {"ok": False, "error": "database unavailable"}

    providers = await provider_status()
    provider_ok = any(p["healthy"] for p in providers["providers"])
    checks["providers"] = providers

    ready_now = db_ok and corpus_ok and provider_ok
    if not ready_now:
        # 503 so orchestrators and probes see the failure, not just humans.
        response.status_code = 503

    return {"status": "ok" if ready_now else "unavailable", "checks": checks}


@router.get("", response_model=HealthResponse, summary="Full health detail")
async def health() -> HealthResponse:
    """Everything, with remediation hints. The endpoint to hit when debugging."""
    settings = get_settings()
    checks: dict = {"app_env": settings.app_env}

    db_ok = await pool.ping()
    checks["database"] = {
        "ok": db_ok,
        "host": settings.postgres_host,
        "port": settings.postgres_port,
        **({} if db_ok else {"remediation": "Is Postgres running? `docker compose ps db`"}),
    }

    if db_ok:
        try:
            from app.ingest.pipeline import corpus_status

            checks["knowledge_base"] = await corpus_status()
            if not checks["knowledge_base"]["ready"]:
                checks["knowledge_base"]["remediation"] = (
                    "Corpus not ingested. Run: make ingest"
                )
        except Exception as exc:
            checks["knowledge_base"] = {"ready": False, "error": str(exc)}

    providers = await provider_status(settings)
    checks["providers"] = providers

    # "degraded" is a real, distinct state: the app works, but not the way it
    # was configured to. Collapsing it into ok/unavailable would hide the most
    # common production condition — running on the fallback provider.
    if not db_ok:
        status = "unavailable"
    elif providers["degraded"] or not checks.get("knowledge_base", {}).get("ready"):
        status = "degraded"
    elif not any(p["healthy"] for p in providers["providers"]):
        status = "unavailable"
    else:
        status = "ok"

    return HealthResponse(status=status, checks=checks)

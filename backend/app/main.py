"""FastAPI application entrypoint.

Startup is deliberately non-fatal on dependency failure. A backend that refuses
to boot because Ollama isn't running is a bad operational citizen: the health
endpoints exist precisely to *report* that condition, and they can't report
anything if the process exited. So we log what's wrong, start anyway, and let
/api/health tell the operator what to fix.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import artifacts, chat, health, models, sessions
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler, unhandled_error_handler
from app.core.logging import api_log, configure_logging, request_id_var


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    api_log.info("app_starting", **settings.redacted())

    # Probe dependencies for the log, but never block startup on them.
    from app.db import pool

    if await pool.ping():
        try:
            from app.ingest.pipeline import corpus_status

            corpus = await corpus_status()
            if corpus["ready"]:
                api_log.info(
                    "knowledge_base_ready",
                    episodes=corpus["episodes"],
                    chunks=corpus["chunks"],
                    source_commit=corpus["source_commit"],
                )
            else:
                api_log.warning(
                    "knowledge_base_empty",
                    remediation="Run `make ingest` to load transcripts.",
                )
        except Exception as exc:
            api_log.warning("corpus_status_failed", error=str(exc))
    else:
        api_log.warning(
            "database_unavailable_at_startup",
            remediation="Check Postgres. The API will serve /api/health regardless.",
        )

    from app.providers.registry import provider_status

    try:
        status = await provider_status(settings)
        for provider in status["providers"]:
            level = api_log.info if provider["healthy"] else api_log.warning
            level(
                "provider_status",
                provider=provider["name"],
                model=provider["model"],
                healthy=provider["healthy"],
                reason=provider["reason"],
            )
    except Exception as exc:
        api_log.warning("provider_status_failed", error=str(exc))

    yield

    await pool.close_pool()
    api_log.info("app_stopped")


settings = get_settings()

app = FastAPI(
    title="The Lenny Growth Assistant",
    description=(
        "Grounded conversational assistant over 303 Lenny's Podcast transcripts. "
        "Answers are synthesised only from retrieved passages, with citations "
        "that deep-link to the exact second of the source episode."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Bind a request id and emit one structured access log per request.

    The id propagates into every downstream stage log (model, retrieval, db), so
    a single answer's full lifecycle can be reassembled from interleaved output —
    which is the whole point of debugging the stages separately.
    """
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request_id_var.set(request_id)
    started = time.monotonic()

    try:
        response = await call_next(request)
    except Exception:
        api_log.exception(
            "request_failed",
            method=request.method,
            path=request.url.path,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise

    duration_ms = int((time.monotonic() - started) * 1000)

    # Health probes fire constantly; logging them at INFO drowns the signal.
    if not request.url.path.startswith("/api/health"):
        api_log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )

    response.headers["X-Request-ID"] = request_id
    return response


def _serializable_errors(exc: RequestValidationError) -> list[dict]:
    """Convert Pydantic validation errors into JSON-safe dicts.

    Pydantic v2 puts the originating exception *object* in each error's `ctx`
    (e.g. {"error": ValueError(...)}), which json.dumps cannot encode. Passing
    exc.errors() straight to JSONResponse therefore raises inside the error
    handler itself, and the request ends as a 500 — so every endpoint with a
    custom field_validator returned "internal error" instead of a 422 telling
    the caller what was actually wrong.

    We keep only the fields a client needs and stringify anything else.
    """
    cleaned: list[dict] = []

    for error in exc.errors():
        entry = {
            "type": error.get("type"),
            # loc is a tuple; JSON needs a list.
            "field": ".".join(str(part) for part in error.get("loc", ())),
            "message": error.get("msg"),
        }
        # `input` is caller-supplied and may be any type. Truncated so a 50k
        # character body isn't echoed back in the error response.
        if (given := error.get("input")) is not None:
            entry["input"] = str(given)[:200]
        cleaned.append(entry)

    return cleaned


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    """Reshape FastAPI's validation errors into our error contract.

    Without this, clients would face two different error shapes depending on
    whether the failure was in validation or in application logic.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "The request body is invalid.",
                "detail": {"fields": _serializable_errors(exc)},
            }
        },
    )


app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(health.router)
app.include_router(models.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(artifacts.router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "name": "The Lenny Growth Assistant",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/health",
    }

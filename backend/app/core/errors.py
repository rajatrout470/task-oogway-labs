"""Structured error contract.

Every failure the client can encounter is one of these. The shape is fixed:

    {
      "error": {
        "code": "provider_unavailable",
        "message": "human readable, safe to display",
        "detail": {...optional structured context...},
        "remediation": "what the operator should actually do about it"
      }
    }

`remediation` exists because the most common failures here are *operational*,
not programming errors — Ollama isn't running, the model isn't pulled, the
corpus was never ingested. Returning a bare 503 makes a developer guess. The
API should say "run `ollama pull qwen2.5:7b-instruct`".
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for all expected, client-visible failures."""

    status_code: int = 500
    code: str = "internal_error"
    remediation: str | None = None

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        if remediation:
            self.remediation = remediation

    def to_response(self) -> JSONResponse:
        body: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.detail:
            body["error"]["detail"] = self.detail
        if self.remediation:
            body["error"]["remediation"] = self.remediation
        return JSONResponse(status_code=self.status_code, content=body)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class DatabaseUnavailableError(AppError):
    status_code = 503
    code = "database_unavailable"
    remediation = (
        "Check that PostgreSQL is running and reachable: `docker compose ps db`. "
        "If running natively, verify POSTGRES_HOST/PORT in .env."
    )


class ProviderUnavailableError(AppError):
    """The selected model provider cannot serve requests at all."""

    status_code = 503
    code = "provider_unavailable"
    remediation = (
        "For the local path: confirm Ollama is running (`ollama list`) and the model "
        "is pulled (`ollama pull qwen2.5:7b-instruct`). For the cloud path: set "
        "ANTHROPIC_API_KEY in .env. Live status is at GET /api/models."
    )


class ProviderTimeoutError(AppError):
    status_code = 504
    code = "provider_timeout"
    remediation = (
        "The model took too long to respond. Raise OLLAMA_TIMEOUT_SECONDS, lower "
        "RETRIEVAL_TOP_K to shrink the prompt, or switch to a smaller model such "
        "as llama3.2:3b."
    )


class KnowledgeBaseEmptyError(AppError):
    """Retrieval was asked to run against an unpopulated corpus.

    Distinct from 'no relevant results' — that is a normal, well-designed answer
    (the insufficient-evidence path), not an error. This is a setup problem.
    """

    status_code = 503
    code = "knowledge_base_empty"
    remediation = (
        "The transcript corpus has not been ingested yet. Run: "
        "`docker compose --profile ingest run --rm ingest` "
        "(or `make ingest`). This takes several minutes on first run."
    )


class ArtifactRenderError(AppError):
    status_code = 422
    code = "artifact_render_error"


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return exc.to_response()


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Last resort. Never leaks internals — the detail goes to logs, not the client."""
    from app.core.logging import api_log

    api_log.exception("unhandled_exception", error_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An unexpected error occurred.",
                "remediation": "Check backend logs: `docker compose logs backend`.",
            }
        },
    )

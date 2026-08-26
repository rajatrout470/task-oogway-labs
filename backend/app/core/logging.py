"""Structured logging.

The brief asks for "enough detail to debug model calls, retrieval, DB, and
artifact rendering *separately*". That word is doing real work: when a grounded
answer comes back wrong, the first question is always *which stage* failed —
did retrieval miss the passage, or did the model ignore it?

So each stage gets its own named logger and a shared event vocabulary. Filtering
one stage is then a grep:

    docker compose logs backend | grep '"stage": "retrieval"'

A request_id is bound once per HTTP request and propagates through every stage,
so a single answer's full lifecycle can be reassembled from interleaved logs.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

# Bound per-request by RequestContextMiddleware; read by the injector below.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)


def _inject_request_context(_logger, _method, event_dict: dict) -> dict:
    """Attach request/session identity to every event without explicit passing."""
    if rid := request_id_var.get():
        event_dict.setdefault("request_id", rid)
    if sid := session_id_var.get():
        event_dict.setdefault("session_id", sid)
    return event_dict


# Keys that must never reach a log sink, regardless of who sets them. Cheap
# insurance against a future contributor logging a whole settings object.
_REDACT_KEYS = {"api_key", "anthropic_api_key", "authorization", "password", "postgres_password"}


def _redact_secrets(_logger, _method, event_dict: dict) -> dict:
    for key in list(event_dict.keys()):
        if key.lower() in _REDACT_KEYS:
            event_dict[key] = "***redacted***"
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Install structlog. Idempotent — safe to call from app startup and tests."""
    renderer = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_request_context,
            _redact_secrets,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Uvicorn's access log duplicates our request middleware; keep it quiet so
    # the structured stream stays readable.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(stage: str) -> structlog.BoundLogger:
    """Return a logger permanently tagged with its pipeline stage.

    Use one of the canonical stages so the grep-by-stage contract holds:
        api | model | retrieval | db | ingest | agent | artifact
    """
    return structlog.get_logger().bind(stage=stage)


# Canonical stage loggers, imported directly by each subsystem.
api_log = get_logger("api")
model_log = get_logger("model")
retrieval_log = get_logger("retrieval")
db_log = get_logger("db")
ingest_log = get_logger("ingest")
agent_log = get_logger("agent")
artifact_log = get_logger("artifact")

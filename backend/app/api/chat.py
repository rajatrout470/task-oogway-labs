"""Chat endpoint — Server-Sent Events streaming.

SSE rather than WebSockets. The data flow is strictly one-directional
(server → client) for the duration of a turn, and SSE gives us that over plain
HTTP with automatic reconnection, no upgrade handshake, and no connection state
to manage. A WebSocket would be a bidirectional channel used in one direction.

Streaming is not cosmetic here: on the mandated local path, a 1,250-word essay
from a 7B model takes 30+ seconds. Without streaming the UI is
indistinguishable from a hang, and the honest fix is to show progress, not to
hide the latency.

Event types are documented in agent/skills/base.py.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest
from app.core.errors import AppError, NotFoundError
from app.core.logging import api_log, session_id_var
from app.db import repositories as repo

router = APIRouter(prefix="/api/sessions", tags=["chat"])


def _sse(event: dict) -> str:
    """Encode one SSE frame.

    `default=str` so UUIDs and datetimes serialise without every call site
    having to remember to convert them.
    """
    return f"data: {json.dumps(event, default=str)}\n\n"


@router.post("/{session_id}/messages", summary="Send a message (SSE stream)")
async def send_message(session_id: UUID, body: ChatRequest) -> StreamingResponse:
    """Post a user message and stream the assistant's response.

    Validation and the user-message write happen *before* the stream opens, so a
    bad request or a dead database returns a normal JSON error with a proper
    status code. Once a StreamingResponse begins, the status line is already
    sent and errors can only be reported inside the stream — a much worse
    experience for clients.
    """
    session = await repo.get_session(session_id)
    if session is None:
        raise NotFoundError(f"Session {session_id} not found.")

    session_id_var.set(str(session_id))

    await repo.add_message(session_id, "user", body.message)

    # Derive a title from the first message. No-ops when one already exists.
    if session["title"] is None:
        await repo.set_session_title(session_id, _derive_title(body.message))

    api_log.info(
        "chat_message_received",
        message_length=len(body.message),
        requested_skill=body.skill,
    )

    return StreamingResponse(
        _stream(session_id, body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering; nginx will otherwise hold the stream and
            # deliver it in one lump, defeating the point entirely.
            "X-Accel-Buffering": "no",
        },
    )


async def _stream(session_id: UUID, body: ChatRequest) -> AsyncIterator[str]:
    """Bridge orchestrator events onto the SSE wire."""
    from app.agent.orchestrator import Orchestrator

    session_id_var.set(str(session_id))
    orchestrator = Orchestrator()

    try:
        async for event in orchestrator.run_turn(
            session_id=session_id, query=body.message, requested_skill=body.skill
        ):
            yield _sse(event)

    except AppError as exc:
        api_log.error("chat_stream_app_error", code=exc.code, error=exc.message)
        yield _sse(
            {
                "type": "error",
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "remediation": exc.remediation,
                },
            }
        )
    except asyncio.CancelledError:
        # The client navigated away or closed the tab. Normal, not an error —
        # but logged, because a spike of these means users are giving up on
        # latency.
        api_log.info("chat_stream_cancelled")
        raise
    except Exception:
        api_log.exception("chat_stream_crashed")
        yield _sse(
            {
                "type": "error",
                "error": {
                    "code": "internal_error",
                    "message": "The assistant failed unexpectedly.",
                    "remediation": "Check backend logs: `docker compose logs backend`.",
                },
            }
        )
    finally:
        # Explicit terminator so the client can distinguish a completed stream
        # from a dropped connection.
        yield "event: close\ndata: {}\n\n"


def _derive_title(message: str) -> str:
    """First line, trimmed to a sensible sidebar length."""
    title = " ".join(message.strip().split())
    if len(title) <= 60:
        return title
    return title[:57].rsplit(" ", 1)[0] + "…"

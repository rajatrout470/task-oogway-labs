"""Session and conversation endpoints.

Sessions are independent context scopes: a new session starts with no history,
and nothing from one session can influence another. That isolation is a stated
requirement, and it is enforced structurally — every history read is scoped by
session_id, so there is no code path that could leak across sessions.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.api.schemas import (
    ArtifactSummary,
    CitationResponse,
    CreateSessionRequest,
    MessageResponse,
    SessionDetailResponse,
    SessionResponse,
)
from app.core.errors import NotFoundError
from app.core.logging import api_log
from app.db import repositories as repo
from app.providers.registry import provider_status

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse, status_code=201, summary="Start a new chat")
async def create_session(body: CreateSessionRequest) -> SessionResponse:
    """Create a session, minting an anonymous user if needed.

    The provider/model in force at creation is recorded on the session so the UI
    can later show what a past conversation was actually produced with — after
    the configuration has changed.
    """
    user_id = await repo.ensure_user(body.user_id, body.user_metadata)

    status = await provider_status()
    effective = status["effective_provider"]
    model = next(
        (p["model"] for p in status["providers"] if p["name"] == effective),
        None,
    )

    row = await repo.create_session(
        user_id, title=body.title, provider=effective, model=model
    )
    return SessionResponse(**dict(row), message_count=0)


@router.get("", response_model=list[SessionResponse], summary="List a user's sessions")
async def list_sessions(
    user_id: UUID = Query(..., description="Anonymous user id from the client"),
    limit: int = Query(50, ge=1, le=200),
) -> list[SessionResponse]:
    rows = await repo.list_sessions(user_id, limit)
    return [SessionResponse(**dict(r)) for r in rows]


@router.get("/{session_id}", response_model=SessionDetailResponse, summary="Full conversation")
async def get_session(session_id: UUID) -> SessionDetailResponse:
    """Session, all messages with citations, and its artifacts.

    Citations are fetched for the whole conversation in one query rather than
    per message, so reloading a long chat is a constant number of round trips.
    """
    session = await repo.get_session(session_id)
    if session is None:
        raise NotFoundError(f"Session {session_id} not found.")

    messages = await repo.get_messages(session_id)
    citations = await repo.get_citations([m["id"] for m in messages])
    artifacts = await repo.list_artifacts(session_id)

    return SessionDetailResponse(
        session=SessionResponse(**dict(session), message_count=len(messages)),
        messages=[
            MessageResponse(
                **{k: v for k, v in dict(m).items() if k != "token_usage"},
                citations=[
                    CitationResponse(
                        label=c["label"],
                        quote=c["quote"],
                        guest=c["guest"],
                        episode_title=c["episode_title"],
                        episode_slug=c["episode_slug"],
                        timestamp=_format_timestamp(c["start_seconds"]),
                        source_url=c["source_url"],
                        score=c["score"],
                    )
                    for c in citations.get(m["id"], [])
                ],
            )
            for m in messages
        ],
        artifacts=[ArtifactSummary(**dict(a)) for a in artifacts],
    )


@router.delete("/{session_id}", status_code=204, summary="Delete a chat")
async def delete_session(session_id: UUID) -> None:
    """Cascades to messages, citations and artifacts via FK constraints."""
    if not await repo.delete_session(session_id):
        raise NotFoundError(f"Session {session_id} not found.")
    api_log.info("session_deleted", deleted_session_id=str(session_id))


def _format_timestamp(seconds: int | None) -> str | None:
    """'1:04:22' for display. None when the source had no timestamps."""
    if seconds is None:
        return None
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

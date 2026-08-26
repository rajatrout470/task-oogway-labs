"""Request/response contracts.

Pydantic models rather than raw dicts so the API has a real, validated,
self-documenting contract — FastAPI derives OpenAPI from these, which means
/docs is accurate by construction rather than by maintenance.

Field constraints are deliberately tight. A 50,000-character "question" is not
a question; it is either a mistake or an attempt to blow the context budget, and
rejecting it at the edge with a clear 422 is cheaper and clearer than letting it
reach the model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    # Client-minted anonymous identity (PRD A3). Absent on a first-ever visit.
    user_id: UUID | None = None
    title: str | None = Field(default=None, max_length=120)
    user_metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    id: UUID
    user_id: UUID
    title: str | None
    provider: str | None
    model: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class MessageResponse(BaseModel):
    id: UUID
    seq: int
    role: str
    content: str
    skill: str | None = None
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    insufficient_evidence: bool = False
    created_at: datetime
    citations: list[CitationResponse] = Field(default_factory=list)


class CitationResponse(BaseModel):
    label: str
    quote: str | None
    guest: str | None
    episode_title: str | None
    episode_slug: str | None
    timestamp: str | None = None
    source_url: str | None
    score: float | None = None


class SessionDetailResponse(BaseModel):
    session: SessionResponse
    messages: list[MessageResponse]
    artifacts: list[ArtifactSummary]


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # Set by an explicit UI action ("Turn into essay"). Overrides the router's
    # inference, because a user's stated intent beats our guess at it.
    skill: Literal["answer_from_transcripts", "write_ship30_essay", "create_artifact"] | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("message cannot be empty or whitespace only")
        return cleaned


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class ArtifactSummary(BaseModel):
    id: UUID
    kind: str
    title: str
    template: str | None
    version: int
    word_count: int | None
    created_at: datetime
    updated_at: datetime


class ArtifactResponse(ArtifactSummary):
    content: str
    session_id: UUID
    message_id: UUID | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateArtifactRequest(BaseModel):
    content: str = Field(max_length=200_000)


# ---------------------------------------------------------------------------
# Models / health
# ---------------------------------------------------------------------------


class ProviderInfo(BaseModel):
    name: str
    model: str
    healthy: bool
    reason: str
    supports_native_tools: bool
    is_active: bool
    is_fallback: bool
    runtime: str | None = None


class ModelsResponse(BaseModel):
    configured_provider: str
    fallback_provider: str
    effective_provider: str
    # True when the active provider is unhealthy and we are running on the
    # fallback. Surfaced in the UI so a silent downgrade is never invisible.
    degraded: bool
    providers: list[ProviderInfo]
    skills: list[dict[str, Any]]
    retrieval: dict[str, Any]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    checks: dict[str, Any]

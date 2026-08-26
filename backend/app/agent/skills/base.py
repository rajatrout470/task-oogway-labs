"""Skill contract.

A *skill* is a named capability with its own retrieval strategy, prompt, and
output post-processing. Skills are first-class objects rather than prompt
strings so they can be routed to, tested in isolation, and — on the cloud path —
handed to the Claude Agent SDK as real tools.

Every skill emits the same event stream, which is what lets one API endpoint and
one frontend renderer serve all of them:

    {"type": "status",   "stage": "...", "message": "..."}   progress
    {"type": "evidence", "evidence": [...]}                  retrieved sources
    {"type": "token",    "text": "..."}                      streamed prose
    {"type": "artifact", "artifact": {...}}                  viewer payload
    {"type": "done",     "result": {...}}                    final metadata

Evidence is emitted *before* the first token on purpose: the user sees which
transcripts are being drawn on while the answer is still generating, which makes
the wait informative rather than dead time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.providers.base import BaseProvider
from app.retrieval.retriever import Evidence, Retriever


@dataclass
class SkillContext:
    """Everything a skill needs to run one turn."""

    query: str
    session_id: UUID
    provider: BaseProvider
    retriever: Retriever
    history: list[dict[str, str]] = field(default_factory=list)
    # Evidence from the previous turn. Lets the essay skill build on the answer
    # the user just read without re-retrieving and drifting to different sources.
    prior_evidence: list[Evidence] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """Terminal state of a skill run, persisted by the orchestrator."""

    text: str
    skill: str
    evidence: list[Evidence] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    insufficient_evidence: bool = False
    artifact: dict | None = None
    latency_ms: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


class Skill(ABC):
    """One capability of the assistant."""

    name: str
    description: str
    # JSON Schema for the cloud path, where the Agent SDK exposes this as a tool
    # and Claude selects it by reading these fields.
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "The user's request"}},
        "required": ["query"],
    }

    @abstractmethod
    def run(self, ctx: SkillContext) -> AsyncIterator[dict[str, Any]]:
        """Execute, yielding the event stream documented above."""

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def status(stage: str, message: str) -> dict:
    return {"type": "status", "stage": stage, "message": message}


def token(text: str) -> dict:
    return {"type": "token", "text": text}

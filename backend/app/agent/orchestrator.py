"""Agent orchestration — one turn, end to end.

Responsibilities:

  1. Select a healthy provider (with documented fallback).
  2. Load conversation history and the previous turn's evidence.
  3. Route to a skill.
  4. Stream the skill's events to the caller.
  5. Persist the message, its citations, and any artifact.

Persistence happens *after* streaming completes, from the terminal `done` event.
That ordering matters: the user sees tokens immediately rather than waiting on a
database write, and a DB failure cannot truncate an answer that was already
generated — it degrades to "answer shown but not saved", which is logged and far
better than losing the response.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.agent.router import SkillRouter
from app.agent.skills.base import SkillContext, SkillResult
from app.core.errors import AppError
from app.core.logging import agent_log
from app.db import repositories as repo
from app.providers.base import BaseProvider
from app.providers.registry import get_provider
from app.retrieval.retriever import Evidence, Retriever

_router = SkillRouter()


def get_router() -> SkillRouter:
    return _router


class Orchestrator:
    def __init__(self, retriever: Retriever | None = None) -> None:
        self.retriever = retriever or Retriever()
        self.router = _router

    async def run_turn(
        self,
        *,
        session_id: UUID,
        query: str,
        requested_skill: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute one conversational turn, yielding SSE-shaped events."""

        # ---- provider selection ----------------------------------------- #
        try:
            provider: BaseProvider = await get_provider()
        except AppError as exc:
            # A provider failure is expected and actionable, so it becomes a
            # structured error event rather than a stack trace.
            yield {
                "type": "error",
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "remediation": exc.remediation,
                },
            }
            return

        was_fallback = getattr(provider, "_was_fallback", False)
        yield {
            "type": "provider",
            "provider": provider.name,
            "model": provider.model,
            "was_fallback": was_fallback,
        }

        # ---- context ----------------------------------------------------- #
        history = await repo.get_history_for_prompt(session_id)
        prior_evidence = await self._load_prior_evidence(session_id)

        decision = self.router.route(
            query,
            requested_skill=requested_skill,
            has_prior_evidence=bool(prior_evidence),
        )

        yield {
            "type": "skill",
            "skill": decision.skill.name,
            "reason": decision.reason,
        }

        ctx = SkillContext(
            query=query,
            session_id=session_id,
            provider=provider,
            retriever=self.retriever,
            history=history,
            prior_evidence=prior_evidence if decision.reuse_prior_evidence else [],
            options=decision.options,
        )

        # ---- run --------------------------------------------------------- #
        result: SkillResult | None = None

        try:
            async for event in decision.skill.run(ctx):
                if event.get("type") == "done":
                    result = event["result"]
                    continue
                yield event
        except AppError as exc:
            agent_log.error(
                "skill_failed", skill=decision.skill.name, code=exc.code, error=exc.message
            )
            yield {
                "type": "error",
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "remediation": exc.remediation,
                },
            }
            return
        except Exception:
            agent_log.exception("skill_crashed", skill=decision.skill.name)
            yield {
                "type": "error",
                "error": {
                    "code": "skill_error",
                    "message": f"The {decision.skill.name} skill failed unexpectedly.",
                    "remediation": "Check backend logs: `docker compose logs backend`.",
                },
            }
            return

        if result is None:  # pragma: no cover - skills always emit `done`
            yield {"type": "error", "error": {"code": "skill_error", "message": "No result."}}
            return

        # ---- persist ----------------------------------------------------- #
        persisted = await self._persist(
            session_id=session_id,
            provider=provider,
            result=result,
            was_fallback=was_fallback,
        )

        yield {
            "type": "done",
            "message_id": persisted.get("message_id"),
            "artifact_id": persisted.get("artifact_id"),
            "skill": result.skill,
            "provider": provider.name,
            "model": provider.model,
            "was_fallback": was_fallback,
            "insufficient_evidence": result.insufficient_evidence,
            "latency_ms": result.latency_ms,
            "citations": [e.to_public() for e in result.evidence],
            "persisted": persisted.get("ok", False),
            "meta": result.meta,
        }

    # ------------------------------------------------------------------ #

    async def _persist(
        self,
        *,
        session_id: UUID,
        provider: BaseProvider,
        result: SkillResult,
        was_fallback: bool,
    ) -> dict:
        """Write the turn. Never raises — a save failure must not lose the answer.

        The user already has the response on screen at this point. Turning a
        database hiccup into a 500 would discard work the model already did and
        the user already read.
        """
        out: dict = {"ok": False}

        try:
            message = await repo.add_message(
                session_id,
                "assistant",
                result.text,
                skill=result.skill,
                provider=provider.name,
                model=provider.model,
                latency_ms=result.latency_ms,
                token_usage=result.token_usage or None,
                insufficient_evidence=result.insufficient_evidence,
            )
            out["message_id"] = str(message["id"])

            if result.citations:
                await repo.add_citations(message["id"], result.citations)

            if result.artifact:
                artifact = await repo.create_artifact(
                    session_id,
                    kind=result.artifact["kind"],
                    title=result.artifact["title"],
                    content=result.artifact["content"],
                    template=result.artifact.get("template"),
                    message_id=message["id"],
                    metadata=result.artifact.get("metadata"),
                )
                out["artifact_id"] = str(artifact["id"])

            out["ok"] = True

        except Exception as exc:
            # Logged loudly, and reported to the client as persisted=false so
            # the UI can warn that this turn will not survive a reload.
            agent_log.error(
                "turn_persist_failed",
                skill=result.skill,
                error=str(exc),
                impact="answer delivered but not saved",
            )

        return out

    async def _load_prior_evidence(self, session_id: UUID) -> list[Evidence]:
        """Rehydrate the previous assistant turn's citations as Evidence.

        Lets "turn that into an essay" build on exactly the passages the user
        just read, instead of re-retrieving and quietly drifting to different
        sources — which would make the essay not match the answer it came from.
        """
        messages = await repo.get_messages(session_id, limit=6)
        assistant_ids = [m["id"] for m in messages if m["role"] == "assistant"]
        if not assistant_ids:
            return []

        grouped = await repo.get_citations(assistant_ids)
        latest = next((grouped[mid] for mid in reversed(assistant_ids) if mid in grouped), None)
        if not latest:
            return []

        return [_evidence_from_citation(row) for row in latest]


def _evidence_from_citation(row) -> Evidence:
    """Reconstruct Evidence from a persisted citation snapshot.

    Uses the stored snapshot rather than re-reading the chunk, so this still
    works after a corpus refresh removed or renumbered the original passage.
    """
    from app.retrieval.store import RetrievedChunk

    chunk = RetrievedChunk(
        chunk_id=row["chunk_id"],
        episode_id=None,  # type: ignore[arg-type]
        ord=0,
        text=row["quote"] or "",
        speaker=row["guest"],
        start_seconds=row["start_seconds"],
        episode_slug=row["episode_slug"] or "",
        episode_title=row["episode_title"] or "",
        guest=row["guest"] or "",
        # Reconstructed rather than stored: source_url already carries the
        # timestamp fragment, and we must not append a second one.
        youtube_url=(row["source_url"] or "").split("&t=")[0] or None,
        publish_date=None,
        has_timestamps=row["start_seconds"] is not None,
        similarity=row["score"] or 0.0,
    )
    return Evidence(label=row["label"], chunk=chunk, score=row["score"] or 0.0)

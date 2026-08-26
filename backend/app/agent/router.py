"""Skill routing.

The honest engineering answer to a real asymmetry between our two providers.

**The problem.** The brief mandates both the Claude Agent SDK *and* a local
7-8B model as the demo default. Those two want opposite routing strategies:

  * Claude selects tools reliably. Given four well-described skills it picks
    correctly, so letting the model route is both simpler and better — it can
    chain (search, then read a full episode, then write) in ways a fixed
    pipeline cannot.
  * A 7B local model does not. Asked to choose between four skills it picks
    wrong often enough to matter, and a wrong choice here is severe: the user
    asks a question and gets a 1,250-word essay, or asks for an essay and gets
    a paragraph.

**The resolution.** Route by provider capability, declared on the provider
itself via `supports_native_tools`:

  * `True`  -> model-driven tool selection through the Agent SDK.
  * `False` -> deterministic intent classification (this module).

This is not a workaround, it is capability-appropriate design: use the model's
judgement where it is reliable, and code where it is not. The alternative —
one strategy for both — means either crippling the cloud path or shipping a
local path that misroutes.

The classifier is intentionally rule-based rather than a second LLM call.
Rules are inspectable, instant, free, and unit-testable; a classification call
would add a full round-trip of local-inference latency to every turn to answer a
question that a dozen regexes answer correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agent.skills.artifact import ArtifactSkill
from app.agent.skills.base import Skill
from app.agent.skills.grounded_qa import GroundedQASkill
from app.agent.skills.ship30_essay import Ship30EssaySkill
from app.core.logging import agent_log

# ---------------------------------------------------------------------------
# Intent patterns. Ordered by specificity: essay and artifact intents are
# narrow and explicit, so they are tested first; grounded Q&A is the default
# because it is what an unmarked question almost always is.
# ---------------------------------------------------------------------------

_ESSAY_PATTERNS = [
    r"\b(?:write|draft|compose|turn (?:this|that) into)\b.{0,30}"
    r"\b(?:essay|post|article|piece|blog)\b",
    r"\bship\s*30\b",
    r"\b(?:essay|blog post|article)\b.{0,20}\b(?:about|on)\b",
    r"\b(?:make|turn) (?:this|that|it) (?:into )?(?:publishable|an essay|a post)\b",
    r"\b1,?250\b",
]

_ARTIFACT_PATTERNS = [
    r"\b(?:create|make|build|generate|give me|produce)\b.{0,40}"
    r"\b(?:document|page|table|checklist|summary|brief|one[- ]pager|cheat sheet|"
    r"comparison|matrix|template|html|web ?page|report)\b",
    r"\bas (?:an? )?(?:html|markdown|table|document|page)\b",
    r"\b(?:render|format) (?:this|that|it) as\b",
]

# A follow-up like "make it shorter" or "add a section on pricing" refers to the
# artifact already open, not a fresh request. Detected so the router can reuse
# the previous turn's evidence instead of re-retrieving and drifting.
_REVISION_PATTERNS = [
    r"^\s*(?:make|can you make) (?:it|this|that)\b",
    r"^\s*(?:shorter|longer|tighten|expand|punch(?:ier)?|rewrite)\b",
    r"\b(?:add|remove|cut|change|revise|edit)\b.{0,30}\b(?:section|paragraph|intro|headline|ending)\b",
]

_ESSAY_RE = [re.compile(p, re.I) for p in _ESSAY_PATTERNS]
_ARTIFACT_RE = [re.compile(p, re.I) for p in _ARTIFACT_PATTERNS]
_REVISION_RE = [re.compile(p, re.I) for p in _REVISION_PATTERNS]


@dataclass
class RoutingDecision:
    skill: Skill
    reason: str
    options: dict
    # True when the turn should build on the previous turn's evidence rather
    # than retrieving fresh.
    reuse_prior_evidence: bool = False


class SkillRouter:
    """Owns the skill registry and the deterministic routing rules."""

    def __init__(self) -> None:
        self.grounded_qa = GroundedQASkill()
        self.essay = Ship30EssaySkill()
        self.artifact = ArtifactSkill()

        self._registry: dict[str, Skill] = {
            s.name: s for s in (self.grounded_qa, self.essay, self.artifact)
        }

    # ---- registry ------------------------------------------------------- #

    @property
    def skills(self) -> list[Skill]:
        return list(self._registry.values())

    def get(self, name: str) -> Skill | None:
        return self._registry.get(name)

    def describe(self) -> list[dict]:
        """Skill catalogue, used for the Agent SDK tool definitions and /api/models."""
        return [s.describe() for s in self._registry.values()]

    # ---- routing -------------------------------------------------------- #

    def route(
        self,
        query: str,
        *,
        requested_skill: str | None = None,
        has_prior_evidence: bool = False,
    ) -> RoutingDecision:
        """Choose the skill for this turn.

        `requested_skill` comes from an explicit UI action ("Turn into essay"
        button). An explicit user choice always wins over inference — the
        classifier exists to guess when nobody told us, not to second-guess.
        """
        if requested_skill and (skill := self.get(requested_skill)):
            agent_log.info("routing_explicit", skill=skill.name)
            return RoutingDecision(
                skill=skill,
                reason="explicitly_requested",
                options=self._options_for(skill, query),
                # An explicit "turn this into an essay" is by definition about
                # what the user just read.
                reuse_prior_evidence=has_prior_evidence
                and skill.name != self.grounded_qa.name,
            )

        # A short revision request refers to what is already on screen.
        is_revision = has_prior_evidence and any(r.search(query) for r in _REVISION_RE)

        if any(r.search(query) for r in _ESSAY_RE):
            decision = RoutingDecision(
                skill=self.essay,
                reason="matched_essay_intent",
                options=self._options_for(self.essay, query),
                reuse_prior_evidence=has_prior_evidence,
            )
        elif any(r.search(query) for r in _ARTIFACT_RE):
            decision = RoutingDecision(
                skill=self.artifact,
                reason="matched_artifact_intent",
                options=self._options_for(self.artifact, query),
                reuse_prior_evidence=has_prior_evidence,
            )
        elif is_revision:
            decision = RoutingDecision(
                skill=self.artifact,
                reason="matched_revision_intent",
                options=self._options_for(self.artifact, query),
                reuse_prior_evidence=True,
            )
        else:
            # Default. An unmarked question is a question — and defaulting to
            # grounded Q&A is also the safest miss: a short cited answer when
            # the user wanted an essay costs seconds, whereas the reverse costs
            # a minute of local inference and a confusing result.
            decision = RoutingDecision(
                skill=self.grounded_qa,
                reason="default_grounded_qa",
                options={},
            )

        agent_log.info(
            "routing_decision",
            skill=decision.skill.name,
            reason=decision.reason,
            reuse_prior_evidence=decision.reuse_prior_evidence,
            query_length=len(query),
        )
        return decision

    # ---- per-skill option extraction ------------------------------------ #

    def _options_for(self, skill: Skill, query: str) -> dict:
        """Pull skill-specific options out of the query text."""
        if skill.name == self.artifact.name:
            from app.agent.skills.artifact import _infer_format

            return {"format": _infer_format(query)}

        if skill.name == self.essay.name:
            return {"angle": _detect_angle(query)}

        return {}


_ANGLE_HINTS = {
    "analytical": re.compile(r"\b(?:data|numbers|metrics|analysis|analytical|benchmark)\b", re.I),
    "aspirational": re.compile(r"\b(?:inspir\w+|motivat\w+|aspiration\w*|you can)\b", re.I),
    "anthropological": re.compile(r"\b(?:why|culture|history|behaviou?r|psychology)\b", re.I),
}


def _detect_angle(query: str) -> str:
    """Map the query onto one of Ship 30's 4A content angles.

    Defaults to 'actionable', which is the right default for an operator
    audience asking how to do something.
    """
    for angle, pattern in _ANGLE_HINTS.items():
        if pattern.search(query):
            return angle
    return "actionable"

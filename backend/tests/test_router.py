"""Skill routing tests.

Routing is deterministic on the local path because a 7B model chooses badly
between four skills, and a wrong choice is severe: the user asks a question and
receives a 1,250-word essay. These tests pin the classifier's behaviour.
"""

from __future__ import annotations

import pytest

from app.agent.router import SkillRouter, _detect_angle

router = SkillRouter()


QUESTION_QUERIES = [
    "How do you know if you have product-market fit?",
    "What separates great PMs from good ones?",
    "Tell me about growth loops",
    "What did Sean Ellis say about the 40% benchmark?",
    "why do most product launches fail",
]

ESSAY_QUERIES = [
    "Write an essay about early-stage growth channels",
    "Turn this into an essay",
    "Draft a blog post on retention",
    "Write a ship 30 piece about onboarding",
    "Make this publishable",
]

ARTIFACT_QUERIES = [
    "Make a one-page checklist for user interviews",
    "Create a comparison table of pricing models",
    "Build an HTML page summarising this",
    "Give me a document I can share with my team",
    "Generate a cheat sheet on activation metrics",
]


@pytest.mark.parametrize("query", QUESTION_QUERIES)
def test_questions_route_to_grounded_qa(query: str) -> None:
    assert router.route(query).skill.name == "answer_from_transcripts"


@pytest.mark.parametrize("query", ESSAY_QUERIES)
def test_essay_intent_routes_to_ship30(query: str) -> None:
    assert router.route(query).skill.name == "write_ship30_essay"


@pytest.mark.parametrize("query", ARTIFACT_QUERIES)
def test_artifact_intent_routes_to_artifact_skill(query: str) -> None:
    assert router.route(query).skill.name == "create_artifact"


def test_explicit_skill_overrides_inference() -> None:
    """A stated user intent always beats our guess at it."""
    decision = router.route(
        "How do you find product-market fit?",
        requested_skill="write_ship30_essay",
    )
    assert decision.skill.name == "write_ship30_essay"
    assert decision.reason == "explicitly_requested"


def test_unknown_requested_skill_falls_back_to_inference() -> None:
    decision = router.route("How do you find PMF?", requested_skill="not_a_real_skill")
    assert decision.skill.name == "answer_from_transcripts"


def test_essay_reuses_prior_evidence() -> None:
    """'Turn that into an essay' must build on the passages the user just read,
    not silently re-retrieve and drift to different sources."""
    decision = router.route("Turn this into an essay", has_prior_evidence=True)
    assert decision.reuse_prior_evidence is True


def test_grounded_qa_never_reuses_prior_evidence() -> None:
    """A new question needs a fresh search, even mid-conversation."""
    decision = router.route(
        "What about pricing?",
        requested_skill="answer_from_transcripts",
        has_prior_evidence=True,
    )
    assert decision.reuse_prior_evidence is False


def test_revision_request_reuses_context() -> None:
    decision = router.route("make it shorter", has_prior_evidence=True)
    assert decision.reuse_prior_evidence is True


def test_revision_without_context_is_treated_as_a_question() -> None:
    """With nothing to revise, 'make it shorter' has no referent — falling back
    to grounded Q&A is the safe miss."""
    decision = router.route("make it shorter", has_prior_evidence=False)
    assert decision.skill.name == "answer_from_transcripts"


def test_html_request_sets_html_format() -> None:
    decision = router.route("Build an HTML page summarising this")
    assert decision.options.get("format") == "html"


def test_plain_document_defaults_to_markdown() -> None:
    decision = router.route("Make a checklist for user interviews")
    assert decision.options.get("format") == "markdown"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("write an essay with the data and metrics", "analytical"),
        ("write an inspiring essay about what you can achieve", "aspirational"),
        ("write an essay about why culture forms the way it does", "anthropological"),
        ("write an essay about growth channels", "actionable"),
    ],
)
def test_ship30_angle_detection(query: str, expected: str) -> None:
    assert _detect_angle(query) == expected


def test_all_skills_are_registered_and_described() -> None:
    """The catalogue backs both /api/models and the Agent SDK tool definitions."""
    described = router.describe()
    names = {item["name"] for item in described}

    assert names == {"answer_from_transcripts", "write_ship30_essay", "create_artifact"}
    for item in described:
        assert item["description"], f"{item['name']} needs a description for tool selection"
        assert item["input_schema"]["type"] == "object"

"""Citation validator tests.

This is the mechanism that makes grounding a *guarantee* rather than a request.
Every system prompt asks the model to cite only real evidence; these tests
verify what happens when it doesn't — which, on a 7B local model, it eventually
will.
"""

from __future__ import annotations

from app.agent.citations import (
    strip_all_citations,
    used_evidence,
    validate_citations,
)
from app.retrieval.retriever import Evidence
from app.retrieval.store import RetrievedChunk


def _evidence(labels: list[str]) -> list[Evidence]:
    return [
        Evidence(
            label=label,
            chunk=RetrievedChunk(
                chunk_id=None,  # type: ignore[arg-type]
                episode_id=None,  # type: ignore[arg-type]
                ord=index,
                text=f"passage for {label}",
                speaker="Guest",
                start_seconds=60 * index,
                episode_slug=f"ep-{index}",
                episode_title=f"Episode {index}",
                guest=f"Guest {index}",
                youtube_url="https://www.youtube.com/watch?v=abc",
                publish_date=None,
                has_timestamps=True,
            ),
            score=0.7,
        )
        for index, label in enumerate(labels)
    ]


def test_valid_citations_are_preserved() -> None:
    evidence = _evidence(["E1", "E2", "E3"])
    report = validate_citations("PMF is a spectrum [E1]. Retention matters [E3].", evidence)

    assert report.valid_labels == ["E1", "E3"]
    assert report.invalid_labels == []
    assert report.is_grounded
    assert "[E1]" in report.text


def test_hallucinated_citation_is_stripped() -> None:
    """A dangling citation is worse than none — it looks verified."""
    evidence = _evidence(["E1", "E2"])
    report = validate_citations("A claim [E1]. An invented one [E9].", evidence)

    assert report.valid_labels == ["E1"]
    assert report.invalid_labels == ["E9"]
    assert "E9" not in report.text
    assert "[E1]" in report.text


def test_multi_label_bracket_keeps_only_valid_labels() -> None:
    evidence = _evidence(["E1", "E2"])
    report = validate_citations("Both agree [E1, E7].", evidence)

    assert report.valid_labels == ["E1"]
    assert report.invalid_labels == ["E7"]
    assert "[E1]" in report.text
    assert "E7" not in report.text


def test_bracket_removed_entirely_when_nothing_valid() -> None:
    """Prose must not end up peppered with empty '[]'."""
    evidence = _evidence(["E1"])
    report = validate_citations("An unsupported claim [E8, E9].", evidence)

    assert "[]" not in report.text
    assert "[" not in report.text
    assert report.text.endswith("claim.")


def test_whitespace_is_tidied_after_removal() -> None:
    evidence = _evidence(["E1"])
    report = validate_citations("A claim [E9] . And another  [E9]  here.", evidence)

    assert "  " not in report.text
    assert " ." not in report.text


def test_answer_with_no_valid_citations_is_ungrounded() -> None:
    evidence = _evidence(["E1"])
    report = validate_citations("Confident but entirely unsourced prose.", evidence)

    assert not report.is_grounded
    assert report.citation_count == 0


def test_hallucination_rate_is_measurable() -> None:
    """Feeds the Cited Answer Accuracy metric — must be a number, not a vibe."""
    evidence = _evidence(["E1", "E2"])
    report = validate_citations("[E1] and [E2] and [E5] and [E6].", evidence)

    assert report.hallucinated_citation_rate == 0.5


def test_case_insensitive_label_matching() -> None:
    evidence = _evidence(["E1"])
    report = validate_citations("A claim [e1].", evidence)
    assert report.valid_labels == ["E1"]


def test_used_evidence_preserves_citation_order() -> None:
    """The UI lists sources in the order the model actually invoked them."""
    evidence = _evidence(["E1", "E2", "E3"])
    report = validate_citations("First [E3]. Then [E1].", evidence)
    used = used_evidence(evidence, report)

    assert [e.label for e in used] == ["E3", "E1"]


def test_used_evidence_excludes_uncited_passages() -> None:
    """Showing all retrieved passages would overstate how grounded an answer is."""
    evidence = _evidence(["E1", "E2", "E3"])
    report = validate_citations("Only one source [E2].", evidence)

    assert len(used_evidence(evidence, report)) == 1


def test_strip_all_citations_for_essay_prose() -> None:
    """Essays keep grounding via a source list, not inline [E1] noise."""
    cleaned = strip_all_citations("Growth compounds [E1]. Retention is the engine [E2, E3].")

    assert "[E1]" not in cleaned
    assert "E2" not in cleaned
    assert cleaned.startswith("Growth compounds.")


def test_empty_input_is_safe() -> None:
    report = validate_citations("", _evidence(["E1"]))
    assert report.text == ""
    assert not report.is_grounded

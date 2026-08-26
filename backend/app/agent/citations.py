"""Citation validation — grounding enforced in code, not requested in a prompt.

This is the most important module in the agent layer.

Every system prompt in this app tells the model to cite evidence as [E1], [E2].
Prompts are requests, not guarantees. Models — and a 7B local model especially —
invent citation labels, cite evidence that was never retrieved, and occasionally
cite [E9] when only four passages exist. If we merely asked nicely, the product's
central promise would rest on the least reliable component in the stack.

So the model's output is treated as untrusted and mechanically checked:

  1. Extract every [E*] label the model emitted.
  2. Any label with no corresponding retrieved passage is **removed from the
     text** — a dangling citation is worse than none, because it looks verified.
  3. Report what was stripped, so hallucination rate is measurable rather than
     anecdotal (this feeds the PRD's Cited Answer Accuracy metric).
  4. An answer that ends up with zero valid citations is flagged ungrounded and
     handled by the caller.

The result: a fabricated citation cannot reach the user, regardless of which
model produced it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import agent_log
from app.retrieval.retriever import Evidence

# Matches [E1], [E1, E3], [E1][E2] and the common malformed variants models
# emit. Kept permissive on input so we *catch* sloppy citations rather than
# letting them slip through unrecognised as plain text.
_CITATION_PATTERN = re.compile(r"\[\s*(E\d+(?:\s*,\s*E?\d+)*)\s*\]", re.IGNORECASE)


@dataclass
class ValidationReport:
    text: str
    valid_labels: list[str] = field(default_factory=list)
    invalid_labels: list[str] = field(default_factory=list)
    citation_count: int = 0

    @property
    def is_grounded(self) -> bool:
        return bool(self.valid_labels)

    @property
    def hallucinated_citation_rate(self) -> float:
        total = len(self.valid_labels) + len(self.invalid_labels)
        return round(len(self.invalid_labels) / total, 4) if total else 0.0


def validate_citations(text: str, evidence: list[Evidence]) -> ValidationReport:
    """Strip unsupported citations from generated text.

    Returns the cleaned text plus an audit of what was kept and removed.
    """
    known = {e.label.upper() for e in evidence}
    valid: list[str] = []
    invalid: list[str] = []

    def replace(match: re.Match) -> str:
        # One bracket may carry several labels: "[E1, E3]".
        raw_labels = [part.strip().upper() for part in match.group(1).split(",")]
        normalised = [label if label.startswith("E") else f"E{label}" for label in raw_labels]

        kept = []
        for label in normalised:
            if label in known:
                if label not in valid:
                    valid.append(label)
                kept.append(label)
            elif label not in invalid:
                invalid.append(label)

        # Drop the bracket entirely when nothing in it survived, so the prose
        # doesn't end up peppered with empty "[]".
        return f"[{', '.join(kept)}]" if kept else ""

    cleaned = _CITATION_PATTERN.sub(replace, text)

    # Removing a citation can leave doubled spaces or a space before a period.
    cleaned = re.sub(r" {2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)

    if invalid:
        agent_log.warning(
            "citations_hallucinated",
            invalid_labels=invalid,
            valid_labels=valid,
            available_labels=sorted(known),
            hallucination_rate=round(len(invalid) / (len(valid) + len(invalid)), 4),
        )

    return ValidationReport(
        text=cleaned.strip(),
        valid_labels=valid,
        invalid_labels=invalid,
        citation_count=len(valid),
    )


def used_evidence(evidence: list[Evidence], report: ValidationReport) -> list[Evidence]:
    """The evidence actually cited, in the order the model cited it.

    Only these are persisted and shown in the UI. Displaying all eight retrieved
    passages when the answer used three would overstate how grounded it is.
    """
    by_label = {e.label.upper(): e for e in evidence}
    return [by_label[label] for label in report.valid_labels if label in by_label]


def strip_all_citations(text: str) -> str:
    """Remove every citation marker.

    Used for artifact prose (essays), where inline [E2] markers would be
    editorial noise. The essay's claims are still traceable — the citations are
    persisted against the message and rendered as a source list.
    """
    cleaned = _CITATION_PATTERN.sub("", text)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return re.sub(r"\s+([.,;:!?])", r"\1", cleaned).strip()

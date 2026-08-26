"""Transcript parsing and chunking tests.

Chunking is where citation accuracy is won or lost: if a chunk carries the
wrong speaker or the wrong timestamp, every citation built from it is subtly
wrong in a way no downstream check can catch.

The three-format coverage here is a regression suite. An earlier parser handled
only the dominant layout and produced *zero turns* for the other two — the
episodes silently vanished from the knowledge base while the ingest reported
success. Hence `test_unparseable_transcript_raises`: silent corpus loss is the
worst failure mode this product has.
"""

from __future__ import annotations

import pytest

from app.ingest.chunker import (
    ParseError,
    Turn,
    _parse_turns,
    _split_frontmatter,
    _timestamp_to_seconds,
    chunk_turns,
    estimate_tokens,
    parse_transcript,
)

# --- Format A: "Speaker (HH:MM:SS):" on its own line (301 of 303 episodes) ---
FORMAT_A = """---
guest: Test Guest
title: A Test Episode
youtube_url: https://www.youtube.com/watch?v=test
publish_date: 2024-09-05
keywords:
- growth
---

Test Guest (00:00:58):
First thing said here.

(00:01:03):
Same speaker continuing after a pause.

Lenny Rachitsky (00:01:30):
A question from the host.
"""

# --- Format B: "[HH:MM:SS] Speaker: text" inline ---
FORMAT_B = """---
guest: Inline Guest
title: Inline Episode
---

# Heading

## Transcript

[00:00:00] Ryan: An opening statement.
[00:00:28] Lenny: A follow-up question.
"""

# --- Format C: "Speaker:" alone, NO timestamps anywhere ---
FORMAT_C = """---
guest: Untimed Guest
title: Untimed Episode
---

# Heading

## Transcript

Untimed Guest:
Something said without any timestamp.

Lenny:
A reply, also untimed.
"""


def test_timestamp_parsing() -> None:
    assert _timestamp_to_seconds("01:44:26") == 6266
    assert _timestamp_to_seconds("00:00:58") == 58
    assert _timestamp_to_seconds("04:26") == 266


def test_frontmatter_split() -> None:
    metadata, body = _split_frontmatter(FORMAT_A)
    assert metadata["guest"] == "Test Guest"
    assert metadata["keywords"] == ["growth"]
    assert "First thing said here." in body


def test_malformed_frontmatter_costs_metadata_not_the_episode() -> None:
    """A bad YAML header must not take down ingestion of the whole file."""
    metadata, body = _split_frontmatter("---\n: : : not valid\n---\n\nBody text.\n")
    assert metadata == {}
    assert "Body text." in body


def test_format_a_parses_with_speaker_continuation() -> None:
    _, body = _split_frontmatter(FORMAT_A)
    turns, fmt = _parse_turns(body)

    assert fmt == "header_timestamp"
    assert len(turns) == 3
    assert turns[0].speaker == "Test Guest"
    assert turns[0].start_seconds == 58
    # A bare "(00:01:03):" continues the previous speaker.
    assert turns[1].speaker == "Test Guest"
    assert turns[1].start_seconds == 63
    assert turns[2].speaker == "Lenny Rachitsky"


def test_format_b_inline_timestamps() -> None:
    _, body = _split_frontmatter(FORMAT_B)
    turns, fmt = _parse_turns(body)

    assert fmt == "inline_timestamp"
    assert len(turns) == 2
    assert turns[0].speaker == "Ryan"
    assert turns[0].start_seconds == 0
    assert turns[1].start_seconds == 28


def test_format_c_has_no_timestamps() -> None:
    """Absent timestamps must stay absent — never defaulted to zero."""
    _, body = _split_frontmatter(FORMAT_C)
    turns, fmt = _parse_turns(body)

    assert fmt == "speaker_only"
    assert len(turns) == 2
    assert all(turn.start_seconds is None for turn in turns)
    assert turns[0].speaker == "Untimed Guest"


def test_markdown_headings_are_not_speakers() -> None:
    """'## Transcript' looks superficially like a speaker line."""
    _, body = _split_frontmatter(FORMAT_C)
    turns, _ = _parse_turns(body)
    assert all(turn.speaker not in {"Heading", "Transcript"} for turn in turns)


def test_parse_transcript_raises_on_unknown_layout(tmp_path) -> None:
    """Silent corpus loss is the failure this guard exists to prevent."""
    path = tmp_path / "transcript.md"
    path.write_text("---\nguest: X\n---\n\nJust prose with no speaker structure at all.\n")

    with pytest.raises(ParseError):
        parse_transcript(path, "mystery-guest")


def test_parse_transcript_records_format_and_hash(tmp_path) -> None:
    path = tmp_path / "transcript.md"
    path.write_text(FORMAT_A)

    parsed = parse_transcript(path, "test-guest")

    assert parsed.transcript_format == "header_timestamp"
    assert parsed.has_timestamps is True
    assert len(parsed.content_hash) == 64  # sha256 hex
    assert parsed.metadata["guest"] == "Test Guest"


def test_content_hash_is_stable_and_change_sensitive(tmp_path) -> None:
    """The hash drives incremental re-ingest, so it must change iff content does."""
    path = tmp_path / "transcript.md"
    path.write_text(FORMAT_A)
    first = parse_transcript(path, "x").content_hash

    path.write_text(FORMAT_A)
    assert parse_transcript(path, "x").content_hash == first

    path.write_text(FORMAT_A + "\nSpeaker (00:02:00):\nExtra.\n")
    assert parse_transcript(path, "x").content_hash != first


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def _turns(count: int, words_each: int = 60) -> list[Turn]:
    """Build synthetic turns with DISTINCT text per turn.

    Using identical filler for every turn makes any duplicate-detection test
    vacuous — overlapping chunks would share text simply because every turn is
    the same string. Seeding each turn with its index keeps the content
    genuinely different.
    """
    return [
        Turn(
            speaker=f"S{i % 2}",
            start_seconds=i * 30,
            text=f"turn{i} " + " ".join([f"w{i}"] * words_each),
        )
        for i in range(count)
    ]


def test_chunks_never_split_a_turn() -> None:
    """A chunk must have one true speaker and one true start timestamp."""
    chunks = chunk_turns(_turns(10), target_tokens=100, overlap_turns=0)

    assert chunks
    for chunk in chunks:
        assert chunk.start_seconds is not None
        assert chunk.start_seconds % 30 == 0  # a real turn boundary


def test_chunk_ordinals_are_sequential() -> None:
    chunks = chunk_turns(_turns(12), target_tokens=100, overlap_turns=1)
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_overlap_carries_context_forward() -> None:
    with_overlap = chunk_turns(_turns(12), target_tokens=100, overlap_turns=1)
    without = chunk_turns(_turns(12), target_tokens=100, overlap_turns=0)
    assert len(with_overlap) >= len(without)


def test_oversized_single_turn_is_not_truncated() -> None:
    """A long uninterrupted answer is usually the most substantive passage;
    dropping part of it would silently lose content."""
    long_turn = [Turn(speaker="A", start_seconds=0, text=" ".join(["word"] * 2000))]
    chunks = chunk_turns(long_turn, target_tokens=100)

    assert len(chunks) == 1
    assert len(chunks[0].text.split()) >= 2000


def test_speaker_attributed_to_majority_contributor() -> None:
    """With overlap a chunk can span speakers; attributing to the first turn
    would misattribute a passage that is mostly someone else's words."""
    turns = [
        Turn(speaker="Quiet", start_seconds=0, text="one two"),
        Turn(speaker="Talkative", start_seconds=10, text=" ".join(["word"] * 300)),
    ]
    chunks = chunk_turns(turns, target_tokens=100, overlap_turns=0)
    assert chunks[0].speaker == "Talkative"


def test_empty_turns_produce_no_chunks() -> None:
    assert chunk_turns([]) == []


def test_no_duplicate_trailing_chunk() -> None:
    """The overlap tail must not be re-emitted as its own final chunk."""
    chunks = chunk_turns(_turns(9), target_tokens=150, overlap_turns=1)
    texts = [c.text for c in chunks]
    assert len(texts) == len(set(texts))


def test_token_estimate_is_conservative() -> None:
    assert estimate_tokens("one two three four five") >= 5

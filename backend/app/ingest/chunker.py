"""Transcript parsing and chunking.

The single most consequential design decision in the retrieval stack.

The source transcripts are already segmented into timestamped speaker turns:

    Sean Ellis (00:00:58):
    In my experience-

    (00:01:03):
    ...continuation by the same speaker...

    Lenny Rachitsky (00:01:05):
    ...

Blind fixed-width splitting would destroy that structure, and with it the
ability to say *who* said something and *when*. Instead we parse turns first and
only then pack whole turns into chunks. Every chunk therefore inherits a real
speaker and a real start timestamp — which is precisely what lets a citation
deep-link to the exact second of the source video.

Chunks carry one turn of overlap so a claim spanning a turn boundary is still
retrievable as a coherent unit.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# The corpus is NOT homogeneous. Parsing all 303 episodes revealed three
# distinct transcript layouts. An earlier version of this parser handled only
# FORMAT A and silently produced zero turns for the other two — the episodes
# simply vanished from the knowledge base with no error. For a product whose
# entire value is grounding, silent corpus loss is the worst possible bug, so
# we now detect the layout per file and hard-fail on an unparseable one
# (see ParseError and the zero-turn guard in parse_transcript).
# ---------------------------------------------------------------------------

# FORMAT A (301/303 episodes) — header line, optional speaker:
#   "Sean Ellis (00:00:58):"   -> speaker="Sean Ellis"
#   "(00:01:03):"              -> continuation of the previous speaker
# Anchored to the whole line so a mid-sentence parenthetical timestamp cannot
# be mistaken for a turn boundary.
_TURN_HEADER = re.compile(
    r"^(?P<speaker>[^()\n]{0,80}?)\s*\((?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\):\s*$"
)

# FORMAT B (e.g. ryan-hoover) — bracketed timestamp and speaker inline with the
# text: "[00:00:28] Lenny: Ryan Hoover is the founder ..."
_INLINE_TURN = re.compile(
    r"^\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<speaker>[^:\n]{1,60}?):\s*(?P<text>.*)$"
)

# FORMAT C (e.g. adriel-frederick) — speaker name alone on a line, NO timestamps
# anywhere in the file. Deliberately strict: a short, name-shaped, title-cased
# string ending in a colon. A loose pattern here would shred normal prose that
# happens to contain a colon.
_NAME_PART = r"[A-Z][A-Za-z.'\-]{1,20}"
_SPEAKER_ONLY = re.compile(rf"^(?P<speaker>{_NAME_PART}(?: {_NAME_PART}){{0,3}}):\s*$")


class ParseError(Exception):
    """A transcript could not be parsed into any known layout."""


def _timestamp_to_seconds(ts: str) -> int:
    """'01:44:26' -> 6266.  '04:26' -> 266."""
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    m, s = parts
    return m * 60 + s


def estimate_tokens(text: str) -> int:
    """Cheap, model-agnostic token estimate.

    We deliberately avoid a real tokenizer: it would tie chunk sizing to one
    model's vocabulary, and chunk boundaries must stay stable when the model
    changes (the whole point of the provider abstraction). ~1.33 tokens/word is
    close enough for budgeting and is consistently *conservative*.
    """
    return int(len(text.split()) * 1.33) + 1


@dataclass
class Turn:
    """One speaker turn.

    `start_seconds` is None for FORMAT C transcripts, which carry no timestamps
    at all. That absence is propagated honestly all the way to the citation:
    such a citation links to the episode but omits the `&t=` deep link, rather
    than inventing a plausible-looking timestamp.
    """

    speaker: str | None
    start_seconds: int | None
    text: str

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class Chunk:
    """A retrievable passage: one or more consecutive turns."""

    ord: int
    speaker: str | None
    start_seconds: int | None
    end_seconds: int | None
    text: str
    token_estimate: int


@dataclass
class ParsedEpisode:
    slug: str
    metadata: dict[str, Any]
    turns: list[Turn]
    content_hash: str
    source_path: str
    transcript_format: str = "unknown"
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def has_timestamps(self) -> bool:
        """False for layouts with no timestamps; such citations omit deep links."""
        return any(t.start_seconds is not None for t in self.turns)


def parse_transcript(path: Path, slug: str) -> ParsedEpisode:
    """Parse one transcript.md into frontmatter + ordered turns.

    Raises ParseError if no known layout yields any turns. Raising rather than
    returning an empty episode is deliberate: a silently empty transcript would
    disappear from the knowledge base while the ingest reported success, and the
    assistant would then confidently claim the corpus doesn't cover a guest it
    actually does.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    metadata, body = _split_frontmatter(raw)
    turns, fmt = _parse_turns(body)

    if not turns:
        raise ParseError(
            f"{slug}: no turns parsed from {path.name} — the transcript layout is "
            f"not one of the three known formats. Inspect the file and add a "
            f"parser rather than allowing the episode to be dropped."
        )

    return ParsedEpisode(
        slug=slug,
        metadata=metadata,
        turns=turns,
        content_hash=content_hash,
        source_path=str(path),
        transcript_format=fmt,
    )


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Separate YAML frontmatter from transcript body.

    Hand-rolled rather than using a library: some transcripts contain unescaped
    colons and smart quotes in the description field, and we want a malformed
    header to cost us metadata for one episode, never the whole ingest.
    """
    if not raw.startswith("---"):
        return {}, raw

    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw

    header = raw[3:end]
    body = raw[end + 4 :]

    try:
        metadata = yaml.safe_load(header) or {}
        if not isinstance(metadata, dict):
            metadata = {}
    except yaml.YAMLError:
        metadata = {}

    return metadata, body


def _parse_turns(body: str) -> tuple[list[Turn], str]:
    """Parse the body into turns, auto-detecting the transcript layout.

    Formats are tried most-specific first and the first one that yields turns
    wins. Returns (turns, format_name) so the pipeline can record which layout
    each episode used — useful when a future corpus refresh changes format and
    we need to know what shifted.
    """
    for name, parser in (
        ("header_timestamp", _parse_header_format),
        ("inline_timestamp", _parse_inline_format),
        ("speaker_only", _parse_speaker_only_format),
    ):
        turns = parser(body)
        if turns:
            return turns, name
    return [], "unknown"


def _parse_header_format(body: str) -> list[Turn]:
    """FORMAT A: 'Speaker (HH:MM:SS):' on its own line."""
    turns: list[Turn] = []
    current_speaker: str | None = None
    pending_ts: int | None = None
    buffer: list[str] = []

    def flush() -> None:
        if pending_ts is None:
            return
        text = " ".join(" ".join(buffer).split())
        if text:
            turns.append(Turn(speaker=current_speaker, start_seconds=pending_ts, text=text))

    for line in body.splitlines():
        match = _TURN_HEADER.match(line)
        if match:
            flush()
            buffer = []
            speaker = (match.group("speaker") or "").strip()
            # A bare "(00:01:58):" continues the previous speaker.
            if speaker:
                current_speaker = speaker
            pending_ts = _timestamp_to_seconds(match.group("ts"))
        else:
            buffer.append(line)

    flush()
    return turns


def _parse_inline_format(body: str) -> list[Turn]:
    """FORMAT B: '[HH:MM:SS] Speaker: text' — timestamp, speaker and text on one line."""
    turns: list[Turn] = []
    current: Turn | None = None

    for line in body.splitlines():
        match = _INLINE_TURN.match(line)
        if match:
            if current:
                turns.append(current)
            current = Turn(
                speaker=match.group("speaker").strip(),
                start_seconds=_timestamp_to_seconds(match.group("ts")),
                text=match.group("text").strip(),
            )
        elif current and line.strip():
            # Wrapped continuation of the current turn.
            current.text = f"{current.text} {line.strip()}".strip()

    if current:
        turns.append(current)
    return [t for t in turns if t.text]


def _parse_speaker_only_format(body: str) -> list[Turn]:
    """FORMAT C: 'Speaker Name:' alone on a line, no timestamps anywhere.

    Timestamps are genuinely absent from the source, so start_seconds stays
    None and downstream citations degrade to episode-level links.
    """
    turns: list[Turn] = []
    current_speaker: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current_speaker is None:
            return
        text = " ".join(" ".join(buffer).split())
        if text:
            turns.append(Turn(speaker=current_speaker, start_seconds=None, text=text))

    for line in body.splitlines():
        # Markdown headings look superficially similar; never treat them as speakers.
        if line.startswith("#"):
            continue
        match = _SPEAKER_ONLY.match(line)
        if match:
            flush()
            buffer = []
            current_speaker = match.group("speaker").strip()
        else:
            buffer.append(line)

    flush()
    return turns


def chunk_turns(
    turns: list[Turn],
    *,
    target_tokens: int = 350,
    overlap_turns: int = 1,
) -> list[Chunk]:
    """Pack turns into chunks of roughly `target_tokens`.

    Rules, in priority order:
      1. Never split a turn across chunks — a chunk always has one true speaker
         and one true start timestamp.
      2. Emit once the target is reached.
      3. Carry `overlap_turns` trailing turns into the next chunk so a claim
         straddling a boundary stays retrievable.

    A single turn longer than the target becomes its own oversized chunk. That
    is intentional: truncating it would silently drop content, and a long
    uninterrupted answer is usually the most substantive passage in an episode.
    """
    chunks: list[Chunk] = []
    if not turns:
        return chunks

    window: list[Turn] = []
    window_tokens = 0
    ordinal = 0

    def emit(ts: list[Turn]) -> None:
        nonlocal ordinal
        if not ts:
            return
        # The chunk's attributed speaker is whoever contributed the most words;
        # with overlap a chunk can span speakers, and attributing it to the
        # first turn would misattribute a quote that is mostly someone else's.
        by_speaker: dict[str | None, int] = {}
        for t in ts:
            by_speaker[t.speaker] = by_speaker.get(t.speaker, 0) + len(t.text.split())
        speaker = max(by_speaker.items(), key=lambda kv: kv[1])[0]

        text = "\n\n".join(f"{t.speaker or 'Speaker'}: {t.text}" for t in ts)
        chunks.append(
            Chunk(
                ord=ordinal,
                speaker=speaker,
                start_seconds=ts[0].start_seconds,
                end_seconds=ts[-1].start_seconds,
                text=text,
                token_estimate=estimate_tokens(text),
            )
        )
        ordinal += 1

    for turn in turns:
        window.append(turn)
        window_tokens += turn.token_estimate

        if window_tokens >= target_tokens:
            emit(window)
            if overlap_turns > 0 and len(window) > overlap_turns:
                window = window[-overlap_turns:]
                window_tokens = sum(t.token_estimate for t in window)
            else:
                window = []
                window_tokens = 0

    # Trailing remainder. Skipped if it is only the overlap we already emitted,
    # which would otherwise create a duplicate chunk at the end of every episode.
    if window and (len(window) > overlap_turns or not chunks):
        emit(window)

    return chunks

"""Retrieval orchestration: search, fuse, threshold, and assemble evidence.

This module owns the decision that defines the product: **is there enough
supporting evidence to answer at all?** That judgement is made here, in
deterministic code, *before* the model is invoked — never delegated to the
model's own sense of whether it knows something.

That ordering is the core grounding guarantee. A 7B local model asked "answer
only if the context supports it" will often answer anyway. A 7B model that is
never called because retrieval fell below threshold cannot.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.core.logging import retrieval_log
from app.retrieval import store
from app.retrieval.embeddings import EmbeddingClient
from app.retrieval.store import RetrievedChunk

# Reciprocal Rank Fusion constant. 60 is the value from the original RRF paper
# and is deliberately large relative to our result-set size: it flattens the
# contribution curve so a result ranked 1st by one retriever doesn't
# automatically dominate one ranked 2nd by both.
RRF_K = 60

# Hard cap on how much of a transcript any single citation may quote. The corpus
# is third-party copyrighted material (PRD A5): we surface short attributed
# excerpts that point back to the source, never redistribute passages wholesale.
MAX_QUOTE_CHARS = 600

# Cap on how much of each passage is sent to the model.
#
# This is a measured latency control, not a guess. On a 7B local model, time to
# first token is dominated by prompt evaluation (~190 tok/s prefill on an
# M-series laptop): a ~3,500-token evidence block costs ~18s before a single
# word appears. Chunks have a median of ~420 tokens, so capping each at ~1,200
# characters (~300 tokens) meaningfully shortens the prefill.
#
# Truncation is on a paragraph or sentence boundary, never mid-word, so the
# model never receives a passage that stops mid-thought and "completes" it from
# imagination. Retrieval still ranks on the FULL passage text — only what is
# shown to the model is trimmed.
MAX_PROMPT_CHARS = 1200


@dataclass
class Evidence:
    """One numbered passage as presented to the model and to the user.

    The label ("E1", "E2", ...) is the contract between three parties: the
    prompt, the model's inline citations, and the citation validator that
    checks them. It is assigned here and nowhere else.
    """

    label: str
    chunk: RetrievedChunk
    score: float

    @property
    def quote(self) -> str:
        """Display excerpt, truncated on a word boundary."""
        text = self.chunk.text.strip()
        if len(text) <= MAX_QUOTE_CHARS:
            return text
        return text[:MAX_QUOTE_CHARS].rsplit(" ", 1)[0] + "…"

    def to_citation(self, rank: int) -> dict:
        """Persistable citation record (see repositories.add_citations)."""
        return {
            "chunk_id": self.chunk.chunk_id,
            "label": self.label,
            "rank": rank,
            "score": round(self.score, 4),
            "quote": self.quote,
            "episode_slug": self.chunk.episode_slug,
            "episode_title": self.chunk.episode_title,
            "guest": self.chunk.guest,
            "start_seconds": self.chunk.start_seconds,
            "source_url": self.chunk.source_url,
        }

    def to_public(self) -> dict:
        """Shape sent to the UI."""
        return {
            "label": self.label,
            "quote": self.quote,
            "guest": self.chunk.guest,
            "episode_title": self.chunk.episode_title,
            "episode_slug": self.chunk.episode_slug,
            "speaker": self.chunk.speaker,
            "timestamp": self.chunk.timestamp_label,
            "source_url": self.chunk.source_url,
            "score": round(self.score, 4),
        }

    def to_prompt_block(self) -> str:
        """Rendering shown to the model.

        Includes speaker and episode so the model can attribute correctly in
        prose, and is wrapped in explicit delimiters — retrieved text is
        untrusted data, and a transcript could contain instruction-shaped
        sentences (prompt-injection risk, PRD §8).
        """
        who = self.chunk.speaker or self.chunk.guest
        when = f" at {self.chunk.timestamp_label}" if self.chunk.timestamp_label else ""
        return (
            f"<evidence id=\"{self.label}\">\n"
            f"Episode: {self.chunk.episode_title}\n"
            f"Guest: {self.chunk.guest} | Speaking: {who}{when}\n"
            f"---\n"
            f"{self._prompt_text()}\n"
            f"</evidence>"
        )

    def _prompt_text(self) -> str:
        """Passage text trimmed to MAX_PROMPT_CHARS on a clean boundary.

        Prefers a paragraph break, then a sentence end, and only falls back to a
        word boundary. A passage that stops mid-sentence invites the model to
        finish the thought itself — which is exactly the fabrication this whole
        pipeline exists to prevent — so the marker makes the truncation explicit.
        """
        text = self.chunk.text.strip()
        if len(text) <= MAX_PROMPT_CHARS:
            return text

        window = text[:MAX_PROMPT_CHARS]

        # Only accept a boundary in the last third, otherwise we discard far
        # more of the passage than the cap requires.
        floor = int(MAX_PROMPT_CHARS * 0.6)
        for boundary in ("\n\n", ". ", "? ", "! "):
            cut = window.rfind(boundary)
            if cut > floor:
                return window[: cut + len(boundary)].strip() + "\n[…passage continues]"

        return window.rsplit(" ", 1)[0].strip() + "\n[…passage continues]"


@dataclass
class RetrievalResult:
    """Everything the agent layer needs to decide what to do next."""

    query: str
    evidence: list[Evidence] = field(default_factory=list)
    sufficient: bool = False
    reason: str = ""
    best_similarity: float = 0.0
    episodes_covered: int = 0
    latency_ms: int = 0
    adjacent_topics: list[str] = field(default_factory=list)

    def prompt_context(self) -> str:
        return "\n\n".join(e.to_prompt_block() for e in self.evidence)


class Retriever:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.embedder = EmbeddingClient(self.settings)

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        guest: str | None = None,
        published_after: str | None = None,
    ) -> RetrievalResult:
        """Run hybrid retrieval and decide whether the evidence suffices."""
        started = time.monotonic()
        settings = self.settings
        top_k = top_k or settings.retrieval_top_k

        # An empty knowledge base is a setup error, not "no results". Detecting
        # it here produces an actionable message instead of an assistant that
        # inexplicably claims to know nothing about everything.
        if await store.count_chunks() == 0:
            from app.core.errors import KnowledgeBaseEmptyError

            raise KnowledgeBaseEmptyError("The transcript corpus has not been ingested.")

        filters = {"guest": guest, "published_after": published_after}
        embedding = await self.embedder.embed_one(query, kind="query")

        # Both retrievers run against the same filters; results are fused below.
        dense = await store.vector_search(embedding, settings.retrieval_candidates, **filters)
        sparse = await store.keyword_search(query, settings.retrieval_candidates, **filters)

        fused = self._fuse(dense, sparse)
        best_similarity = max((c.similarity for c in dense), default=0.0)

        selected = self._diversify(fused, top_k, settings.retrieval_max_episodes)
        evidence = [
            Evidence(label=f"E{i}", chunk=c, score=c.similarity or c.fused_score)
            for i, c in enumerate(selected, start=1)
        ]

        sufficient, reason = self._assess(evidence, best_similarity, settings)

        result = RetrievalResult(
            query=query,
            evidence=evidence if sufficient else [],
            sufficient=sufficient,
            reason=reason,
            best_similarity=round(best_similarity, 4),
            episodes_covered=len({e.chunk.episode_slug for e in evidence}),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

        if not sufficient:
            # Showing what the corpus *does* cover is more useful than an apology.
            result.adjacent_topics = await store.adjacent_topics(limit=10)

        retrieval_log.info(
            "retrieval_complete",
            query_length=len(query),
            dense_candidates=len(dense),
            sparse_candidates=len(sparse),
            selected=len(selected),
            best_similarity=result.best_similarity,
            threshold=settings.retrieval_min_score,
            sufficient=sufficient,
            reason=reason,
            episodes_covered=result.episodes_covered,
            latency_ms=result.latency_ms,
        )
        return result

    # ------------------------------------------------------------------ #

    def _fuse(
        self, dense: list[RetrievedChunk], sparse: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        """Reciprocal Rank Fusion over the two result lists.

        RRF combines ranks, not scores, so we never have to calibrate cosine
        similarity against ts_rank — two quantities on incomparable scales. A
        chunk found by both retrievers accumulates both contributions and rises
        above one found by either alone, which is exactly the desired bias.
        """
        merged: dict = {}

        for results in (dense, sparse):
            for rank, chunk in enumerate(results, start=1):
                key = chunk.chunk_id
                if key not in merged:
                    merged[key] = chunk
                    chunk.fused_score = 0.0
                else:
                    # Carry across whichever score the other retriever computed,
                    # so a chunk found by both retains its true similarity.
                    existing = merged[key]
                    existing.similarity = max(existing.similarity, chunk.similarity)
                    existing.text_rank = max(existing.text_rank, chunk.text_rank)
                merged[key].fused_score += 1.0 / (RRF_K + rank)

        return sorted(merged.values(), key=lambda c: c.fused_score, reverse=True)

    def _diversify(
        self, ranked: list[RetrievedChunk], top_k: int, max_episodes: int
    ) -> list[RetrievedChunk]:
        """Select up to top_k passages, spread across at most max_episodes.

        Two caps with deliberately different strengths:

        * **max_episodes is hard.** An answer never draws on more episodes than
          this, so the citation list stays legible.
        * **The per-episode cap is soft.** It spreads evidence on the first
          pass, but backfill may exceed it to reach top_k.

        The asymmetry is the point. When a topic is genuinely covered by one
        episode, returning 2 passages instead of 6 would starve the model of
        context to protect a diversity target that the corpus cannot satisfy.
        But backfill draws only from episodes already selected — otherwise a
        long tail of weakly-related episodes would creep in and quietly widen
        the answer's apparent sourcing.

        Without the first-pass cap, a single long episode that returns to a
        topic repeatedly fills every slot, and one guest's view is presented as
        consensus — which is exactly what the product promises not to do.
        """
        # Aim to spread evidence across the episode budget rather than letting
        # one talkative guest fill it. With top_k=6 and max_episodes=5 this is
        # 2 passages per episode. An earlier formula allowed 4, and in practice
        # a single episode supplied half the evidence for a question several
        # operators had answered — presenting one person's view as consensus.
        per_episode_cap = max(2, -(-top_k // max(1, max_episodes)))
        counts: dict[str, int] = {}
        selected: list[RetrievedChunk] = []
        overflow: list[RetrievedChunk] = []

        for chunk in ranked:
            if len(selected) >= top_k:
                break
            slug = chunk.episode_slug
            if counts.get(slug, 0) >= per_episode_cap:
                overflow.append(chunk)
                continue
            if slug not in counts and len(counts) >= max_episodes:
                overflow.append(chunk)
                continue
            counts[slug] = counts.get(slug, 0) + 1
            selected.append(chunk)

        # Backfill rather than returning fewer than top_k when the corpus
        # genuinely concentrates a topic in a few episodes — but only from
        # episodes already chosen, so max_episodes stays a hard guarantee.
        for chunk in overflow:
            if len(selected) >= top_k:
                break
            if chunk.episode_slug in counts:
                selected.append(chunk)

        return selected

    def _assess(
        self, evidence: list[Evidence], best_similarity: float, settings: Settings
    ) -> tuple[bool, str]:
        """The abstention decision.

        Deliberately conservative and deliberately *deterministic*: this runs
        before the model sees anything, so a low-quality match can never be
        talked into becoming an answer.
        """
        if not evidence:
            return False, "no_results"

        if best_similarity < settings.retrieval_min_score:
            return False, "below_relevance_threshold"

        # A lone weak passage is the classic setup for a confident, wrong,
        # over-extrapolated answer. Require either a strong single match or
        # corroboration across passages.
        strong = [e for e in evidence if e.score >= settings.retrieval_min_score]
        if len(strong) < 2 and best_similarity < settings.retrieval_min_score + 0.15:
            return False, "insufficient_corroboration"

        return True, "ok"


# ---------------------------------------------------------------------------
# Lightweight query analysis
# ---------------------------------------------------------------------------

_AFTER_YEAR = re.compile(r"\b(?:since|after|from)\s+(20\d{2})\b", re.I)


def extract_filters(query: str) -> dict[str, str | None]:
    """Pull cheap structured filters out of a natural-language query.

    Intentionally minimal: a regex for years, and guest detection delegated to
    the database's trigram index. Anything more ambitious becomes a second
    place where query understanding lives, and it would drift from the agent's
    own tool-calling.
    """
    filters: dict[str, str | None] = {"guest": None, "published_after": None}

    if match := _AFTER_YEAR.search(query):
        filters["published_after"] = f"{match.group(1)}-01-01"

    return filters

"""Retrieval logic tests — fusion, diversification, and the abstention gate.

These run without a database or an embedding model: they exercise the pure
decision logic, which is where the grounding guarantees actually live. The SQL
itself is covered by the integration tests.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.retrieval.retriever import MAX_PROMPT_CHARS, Evidence, Retriever
from app.retrieval.store import RetrievedChunk


def _chunk(
    slug: str,
    ord_: int = 0,
    similarity: float = 0.7,
    text: str = "a passage",
    start_seconds: int | None = 60,
    has_timestamps: bool = True,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{slug}-{ord_}",  # type: ignore[arg-type]
        episode_id=slug,  # type: ignore[arg-type]
        ord=ord_,
        text=text,
        speaker="Guest",
        start_seconds=start_seconds,
        episode_slug=slug,
        episode_title=f"Episode {slug}",
        guest=f"Guest {slug}",
        youtube_url="https://www.youtube.com/watch?v=abc",
        publish_date=None,
        has_timestamps=has_timestamps,
        similarity=similarity,
    )


@pytest.fixture
def retriever() -> Retriever:
    return Retriever(Settings(retrieval_min_score=0.6, retrieval_top_k=6, retrieval_max_episodes=5))


# ---------------------------------------------------------------------------
# Source links
# ---------------------------------------------------------------------------


def test_source_url_deep_links_to_exact_second() -> None:
    chunk = _chunk("ep", start_seconds=1223)
    assert chunk.source_url == "https://www.youtube.com/watch?v=abc&t=1223s"


def test_source_url_omits_timestamp_when_source_has_none() -> None:
    """A missing timestamp must never be invented as a plausible-looking one."""
    chunk = _chunk("ep", start_seconds=None, has_timestamps=False)
    assert chunk.source_url == "https://www.youtube.com/watch?v=abc"
    assert "&t=" not in chunk.source_url


def test_timestamp_label_formatting() -> None:
    assert _chunk("e", start_seconds=3862).timestamp_label == "1:04:22"
    assert _chunk("e", start_seconds=125).timestamp_label == "2:05"
    assert _chunk("e", start_seconds=None).timestamp_label is None


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def test_fusion_ranks_chunks_found_by_both_retrievers_higher(retriever: Retriever) -> None:
    """A passage matched semantically AND lexically is the strongest signal."""
    shared = _chunk("shared", 0)
    dense = [shared, _chunk("dense-only", 1)]
    sparse = [_chunk("shared", 0), _chunk("sparse-only", 2)]

    fused = retriever._fuse(dense, sparse)
    assert fused[0].episode_slug == "shared"


def test_fusion_deduplicates(retriever: Retriever) -> None:
    dense = [_chunk("a", 0), _chunk("b", 1)]
    sparse = [_chunk("a", 0)]

    fused = retriever._fuse(dense, sparse)
    assert len({c.chunk_id for c in fused}) == len(fused)


def test_fusion_preserves_similarity_from_dense_side(retriever: Retriever) -> None:
    """Keyword hits carry similarity 0.0; the threshold reads similarity, so a
    chunk found by both must keep its real semantic score."""
    dense = [_chunk("a", 0, similarity=0.81)]
    sparse = [_chunk("a", 0, similarity=0.0)]

    fused = retriever._fuse(dense, sparse)
    assert fused[0].similarity == pytest.approx(0.81)


# ---------------------------------------------------------------------------
# Diversification
# ---------------------------------------------------------------------------


def test_diversify_prevents_one_episode_dominating(retriever: Retriever) -> None:
    """Otherwise one guest's view is presented as consensus.

    The dominant episode is ranked highest across the board, so without a cap
    it would take every slot. Other episodes ARE available here — that is what
    distinguishes this from the single-episode backfill case below.
    """
    ranked = [_chunk("hoggy", i, similarity=0.9) for i in range(10)]
    ranked += [_chunk(f"other{i}", 100 + i, similarity=0.7) for i in range(6)]

    selected = retriever._diversify(ranked, top_k=6, max_episodes=5)

    assert sum(1 for c in selected if c.episode_slug == "hoggy") <= 2
    assert len({c.episode_slug for c in selected}) >= 3


def test_diversify_spreads_across_episodes(retriever: Retriever) -> None:
    ranked = [_chunk(f"ep{i // 2}", i) for i in range(12)]
    selected = retriever._diversify(ranked, top_k=6, max_episodes=5)

    assert len({c.episode_slug for c in selected}) >= 3


def test_diversify_backfills_rather_than_returning_too_few(retriever: Retriever) -> None:
    """When a topic genuinely lives in one episode, return top_k anyway."""
    ranked = [_chunk("only-episode", i) for i in range(10)]
    selected = retriever._diversify(ranked, top_k=6, max_episodes=5)

    assert len(selected) == 6


def test_diversify_respects_max_episodes(retriever: Retriever) -> None:
    ranked = [_chunk(f"ep{i}", i) for i in range(20)]
    selected = retriever._diversify(ranked, top_k=6, max_episodes=3)

    assert len({c.episode_slug for c in selected}) <= 3


# ---------------------------------------------------------------------------
# The abstention gate — the product's central guarantee
# ---------------------------------------------------------------------------


def _evidence(scores: list[float]) -> list[Evidence]:
    return [
        Evidence(label=f"E{i + 1}", chunk=_chunk(f"ep{i}", i, similarity=s), score=s)
        for i, s in enumerate(scores)
    ]


def test_no_results_is_insufficient(retriever: Retriever) -> None:
    sufficient, reason = retriever._assess([], 0.0, retriever.settings)
    assert not sufficient
    assert reason == "no_results"


def test_below_threshold_is_insufficient(retriever: Retriever) -> None:
    """The Kubernetes case: plausible-looking similarity, no real coverage."""
    evidence = _evidence([0.55, 0.54, 0.52])
    sufficient, reason = retriever._assess(evidence, 0.55, retriever.settings)

    assert not sufficient
    assert reason == "below_relevance_threshold"


def test_single_weak_passage_requires_corroboration(retriever: Retriever) -> None:
    """One marginal passage is the classic setup for a confident, over-
    extrapolated answer, so it is refused."""
    evidence = _evidence([0.62, 0.40, 0.38])
    sufficient, reason = retriever._assess(evidence, 0.62, retriever.settings)

    assert not sufficient
    assert reason == "insufficient_corroboration"


def test_single_very_strong_passage_is_accepted(retriever: Retriever) -> None:
    """A decisive match doesn't need a second opinion."""
    evidence = _evidence([0.88, 0.40])
    sufficient, _ = retriever._assess(evidence, 0.88, retriever.settings)
    assert sufficient


def test_corroborated_evidence_is_accepted(retriever: Retriever) -> None:
    evidence = _evidence([0.72, 0.68, 0.65])
    sufficient, reason = retriever._assess(evidence, 0.72, retriever.settings)

    assert sufficient
    assert reason == "ok"


def test_threshold_is_configurable(retriever: Retriever) -> None:
    """Operators must be able to trade recall against hallucination risk."""
    strict = Settings(retrieval_min_score=0.9)
    evidence = _evidence([0.72, 0.68])

    sufficient, _ = retriever._assess(evidence, 0.72, strict)
    assert not sufficient


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_prompt_block_is_delimited_and_labelled() -> None:
    """Retrieved text is untrusted data; delimiters separate it from instructions."""
    evidence = _evidence([0.8])[0]
    block = evidence.to_prompt_block()

    assert block.startswith('<evidence id="E1">')
    assert block.endswith("</evidence>")
    assert "Guest" in block


def test_long_passage_is_trimmed_for_the_prompt() -> None:
    """Prefill cost dominates TTFT on a 7B; passages are capped."""
    long_text = ". ".join(["a sentence of some length here"] * 200)
    evidence = Evidence(label="E1", chunk=_chunk("ep", text=long_text), score=0.8)

    block = evidence.to_prompt_block()
    assert len(block) < MAX_PROMPT_CHARS + 500
    assert "[…passage continues]" in block


def test_short_passage_is_not_trimmed() -> None:
    evidence = Evidence(label="E1", chunk=_chunk("ep", text="Short passage."), score=0.8)
    assert "[…passage continues]" not in evidence.to_prompt_block()


def test_display_quote_is_capped() -> None:
    """Excerpts stay short and attributed — they point at the source, not
    replace it."""
    evidence = Evidence(label="E1", chunk=_chunk("ep", text="word " * 500), score=0.8)
    assert len(evidence.quote) <= 601


def test_public_shape_carries_everything_the_ui_needs() -> None:
    public = _evidence([0.8])[0].to_public()

    for key in ("label", "quote", "guest", "episode_title", "timestamp", "source_url", "score"):
        assert key in public

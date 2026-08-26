"""Calibrate RETRIEVAL_MIN_SCORE against the live index.

    python -m scripts.calibrate_threshold

The relevance floor is the single most important number in the grounding
stack — it is what decides whether the assistant answers or abstains — and it
is **not portable across embedding models**. Each model puts similarity on its
own scale, so a value tuned for one is meaningless for another.

This script measures the actual separation between questions the corpus covers
and questions it does not, then reports whether a usable threshold exists and
what it should be. Run it after changing OLLAMA_EMBED_MODEL, after changing
chunking parameters, or whenever abstention behaviour looks wrong.

A negative gap means the distributions OVERLAP and *no* threshold can separate
them. That is a real finding, not a tuning problem: it means the embedding
setup is broken. (It is how we discovered that nomic-embed-text was being used
without its required task prefixes — see retrieval/embeddings.py.)
"""

from __future__ import annotations

import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.pool import close_pool
from app.retrieval import store
from app.retrieval.embeddings import EmbeddingClient

# Questions the corpus genuinely covers. Deliberately spread across topics and
# phrasings, including a couple that are only tangentially covered — the floor
# must not be tuned so high that real questions get refused.
IN_CORPUS = [
    "how do you know if you have product market fit?",
    "how should I structure a growth team?",
    "what makes a good product manager?",
    "how do you run effective user interviews?",
    "how do you set company operating cadence?",
    "how do you price a b2b saas product?",
    "when should a startup hire its first PM?",
    "how do you improve user onboarding and activation?",
]

# Questions the corpus genuinely does not cover. Mixed distance: some adjacent
# to tech (so plausibly confusable), some far away.
OUT_OF_CORPUS = [
    "what is the best CI/CD tool for kubernetes clusters?",
    "how do I fix a segfault in my C++ pointer arithmetic?",
    "what is the airspeed velocity of an unladen swallow?",
    "best recipe for sourdough starter hydration",
    "how do I replace the timing belt on a 2004 Honda Civic?",
    "what were the causes of the War of the Spanish Succession?",
    "how do I treat a sprained ankle at home?",
    "what is the offside rule in football?",
]


async def main() -> int:
    settings = get_settings()
    configure_logging("WARNING", "console")

    if await store.count_chunks() == 0:
        print("\n  ✗ The knowledge base is empty. Run `make ingest` first.\n", file=sys.stderr)
        return 1

    embedder = EmbeddingClient(settings)
    ok, reason = await embedder.health()
    if not ok:
        print(f"\n  ✗ {reason}\n", file=sys.stderr)
        return 1

    print(f"\n  Embedding model : {embedder.model}")
    print(f"  Task prefixes   : {'yes' if embedder.uses_task_prefixes else 'no'}")
    print(f"  Indexed passages: {await store.count_chunks():,}")
    print(f"  Current floor   : {settings.retrieval_min_score}\n")

    results: dict[str, list[float]] = {}

    for label, questions in (("IN", IN_CORPUS), ("OUT", OUT_OF_CORPUS)):
        scores = []
        print(f"  {label}-CORPUS")
        for question in questions:
            vector = await embedder.embed_one(question, kind="query")
            rows = await store.vector_search(vector, 8)
            top1 = rows[0].similarity if rows else 0.0
            scores.append(top1)
            print(f"    {top1:6.3f}  {question[:58]}")
        results[label] = scores
        print()

    in_min, in_max = min(results["IN"]), max(results["IN"])
    out_min, out_max = min(results["OUT"]), max(results["OUT"])
    gap = in_min - out_max

    print(f"  in-corpus     : {in_min:.3f} – {in_max:.3f}")
    print(f"  out-of-corpus : {out_min:.3f} – {out_max:.3f}")
    print(f"  separation gap: {gap:+.3f}\n")

    if gap <= 0:
        print("  ✗ OVERLAP — no threshold can separate these distributions.")
        print("    Do NOT just pick a number. Something upstream is wrong:")
        print("      - Is the embedding model asymmetric and missing its task prefixes?")
        print("      - Were documents and queries embedded with the same model?")
        print("      - Did the index get rebuilt after the last ingest?\n")
        return 1

    suggested = round((in_min + out_max) / 2, 2)
    print(f"  ✓ Suggested RETRIEVAL_MIN_SCORE = {suggested}")

    if gap < 0.05:
        print(f"    ⚠ Margin is narrow ({gap:.3f}). The corroboration rule in")
        print("      retriever._assess() is doing real work here — keep it.")

    current = settings.retrieval_min_score
    if not (out_max < current < in_min):
        print(f"\n    ⚠ The configured floor ({current}) is OUTSIDE the safe band")
        print(f"      ({out_max:.3f} – {in_min:.3f}). Update RETRIEVAL_MIN_SCORE in .env.")

    print()
    await close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

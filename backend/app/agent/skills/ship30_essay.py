"""Ship 30 for 30 essay skill.

Turns a grounded answer into a publishable ~1,250-word essay.

Three things make this a *skill* rather than a prompt string:

1. **The writing principles live in a versioned Markdown file**
   (ship30_principles.md), loaded at runtime and composed into the prompt. A
   writer can tune the craft rules without touching Python, and changes are
   reviewable as a diff.

2. **It has its own retrieval strategy.** An essay needs broader, more varied
   evidence than a direct answer — several operators' perspectives rather than
   the tightest match — so it retrieves a wider set at a lower diversity cap.

3. **It has its own post-processing.** Inline [E1] markers are editorial noise
   in prose, so they are stripped from the essay body while the citations stay
   attached to the message and are rendered as a source list. The essay stays
   traceable without reading like a lab report.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agent.citations import strip_all_citations, used_evidence, validate_citations
from app.agent.skills.base import Skill, SkillContext, SkillResult, status, token
from app.core.logging import agent_log
from app.providers.base import Message

_PRINCIPLES_PATH = Path(__file__).parent / "ship30_principles.md"

TARGET_WORDS = 1250
# Below this the piece is a summary, not an essay; above it, padding. The
# validator reports where a draft landed rather than silently accepting it.
ACCEPTABLE_RANGE = (900, 1600)


@lru_cache(maxsize=1)
def load_principles() -> str:
    """Read the encoded writing principles.

    Cached because the file is read on every essay generation and never changes
    within a process. Restart to pick up edits.
    """
    try:
        return _PRINCIPLES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:  # pragma: no cover
        agent_log.error("ship30_principles_missing", path=str(_PRINCIPLES_PATH))
        raise


SYSTEM_PROMPT_TEMPLATE = """You are a ghostwriter trained in the Ship 30 for 30 \
method, writing for an experienced product/growth operator who publishes to \
build their reputation.

You write essays grounded ENTIRELY in transcript excerpts from Lenny's Podcast. \
Your authority comes from curating named practitioners — never from claiming \
personal operating experience you do not have.

Below are the writing principles you must follow. They are not suggestions; \
they are the house style.

<writing_principles>
{principles}
</writing_principles>

## Your task

Write a complete essay of approximately {target_words} words.

Structure it as a spine of 4-6 self-contained units, each of which could stand \
alone as an atomic essay. Length must come from stacking complete thoughts — \
never from inflating one thought with restatement.

## Output format

Return Markdown only:

- Start with `# ` and the headline. Build it deliberately per the headline \
principles. Clear beats clever.
- Use `## ` subheadings for each major section. Every subheading must be \
meaningful when read alone.
- Bold exactly one key sentence per section.
- Use bullets for any list of three or more items.
- End with a takeaway section that leaves one actionable insight.

## Grounding rules — these override every writing principle above

- Every substantive claim must come from the evidence provided.
- Attribute ideas to the operator by name and mention their episode naturally \
in the prose: "Sean Ellis, who coined the term growth hacking, argues...".
- Cite evidence labels inline as [E1] while drafting. They will be removed \
before the reader sees the essay, but they are how your grounding is verified — \
so cite honestly and completely.
- Invent NOTHING. No statistics, company outcomes, or quotations that are not \
in the evidence. If the evidence has no number, the essay has no number.
- Quote only briefly, and always attributed.
- If the evidence supports only a narrower essay, write the narrower essay well \
rather than padding to reach the word count.
"""


class Ship30EssaySkill(Skill):
    name = "write_ship30_essay"
    description = (
        "Turn a topic or a previous grounded answer into a publishable "
        "~1,250-word essay in the Ship 30 for 30 style, grounded in Lenny's "
        "Podcast transcripts. Use when the user asks to write an essay, post, "
        "article, or to turn an answer into something publishable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The essay topic or angle"},
            "angle": {
                "type": "string",
                "enum": ["actionable", "analytical", "aspirational", "anthropological"],
                "description": "Ship 30's 4A content angle. Defaults to actionable.",
            },
        },
        "required": ["query"],
    }

    async def run(self, ctx: SkillContext) -> AsyncIterator[dict[str, Any]]:
        started = time.monotonic()

        yield status("retrieving", "Gathering evidence across episodes…")

        evidence = ctx.prior_evidence
        retrieval = None

        if not evidence:
            # Essays need breadth — several operators, not the single tightest
            # match — so we retrieve a wider set than grounded Q&A does.
            retrieval = await ctx.retriever.retrieve(ctx.query, top_k=12)

            if not retrieval.sufficient:
                text = (
                    "**I can't write this essay from the transcripts.**\n\n"
                    "I searched all 303 episodes and didn't find enough grounded "
                    "material on this topic to write something I'd put your name on. "
                    "Writing it anyway would mean inventing the specifics — exactly "
                    "what makes AI-written essays not worth publishing.\n\n"
                )
                if retrieval.adjacent_topics:
                    text += (
                        f"**Well-covered topics:** "
                        f"{', '.join(retrieval.adjacent_topics[:8])}."
                    )
                yield token(text)
                yield {
                    "type": "done",
                    "result": SkillResult(
                        text=text,
                        skill=self.name,
                        insufficient_evidence=True,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        meta={"reason": retrieval.reason},
                    ),
                }
                return

            evidence = retrieval.evidence

        yield {"type": "evidence", "evidence": [e.to_public() for e in evidence]}
        yield status("writing", "Drafting a ~1,250 word essay…")

        system = SYSTEM_PROMPT_TEMPLATE.format(
            principles=load_principles(), target_words=TARGET_WORDS
        )

        angle = ctx.options.get("angle", "actionable")
        evidence_block = "\n\n".join(e.to_prompt_block() for e in evidence)

        user_content = (
            f"<evidence_set>\n{evidence_block}\n</evidence_set>\n\n"
            f"Essay topic: {ctx.query}\n"
            f"Content angle (Ship 30 4A): {angle}\n\n"
            f"Write the complete ~{TARGET_WORDS} word essay in Markdown now."
        )

        # Essays are long-form: temperature is a little higher than grounded Q&A
        # so the prose has some life, but still low enough that the model stays
        # anchored to the evidence rather than embellishing.
        raw = []
        async for chunk in ctx.provider.stream(
            system=system,
            messages=[Message(role="user", content=user_content)],
            max_tokens=3000,
            temperature=0.4,
        ):
            raw.append(chunk)
            yield token(chunk)

        draft = "".join(raw)

        # Verify grounding against the labels the model cited, THEN strip the
        # markers so the reader gets clean prose. Order matters: stripping first
        # would discard the evidence needed to verify.
        report = validate_citations(draft, evidence)
        cited = used_evidence(evidence, report)
        essay = strip_all_citations(report.text)

        title = _extract_title(essay)
        word_count = len(essay.split())
        in_range = ACCEPTABLE_RANGE[0] <= word_count <= ACCEPTABLE_RANGE[1]

        agent_log.info(
            "essay_complete",
            word_count=word_count,
            target=TARGET_WORDS,
            within_range=in_range,
            evidence_cited=len(cited),
            invalid_citations=len(report.invalid_labels),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

        artifact = {
            "kind": "markdown",
            "title": title,
            "content": essay,
            "template": "essay",
            "metadata": {
                "word_count": word_count,
                "target_words": TARGET_WORDS,
                "within_target_range": in_range,
                "angle": angle,
                "sources": [
                    {
                        "guest": e.chunk.guest,
                        "episode": e.chunk.episode_title,
                        "url": e.chunk.source_url,
                    }
                    for e in cited
                ],
            },
        }
        yield {"type": "artifact", "artifact": artifact}

        yield {
            "type": "done",
            "result": SkillResult(
                text=(
                    f"I've drafted **{title}** — {word_count} words, grounded in "
                    f"{len({e.chunk.episode_slug for e in cited})} episodes. "
                    "It's open in the Artifact Viewer where you can read, edit, or copy it."
                ),
                skill=self.name,
                evidence=cited,
                citations=[e.to_citation(i) for i, e in enumerate(cited)],
                artifact=artifact,
                latency_ms=int((time.monotonic() - started) * 1000),
                meta={
                    "word_count": word_count,
                    "within_target_range": in_range,
                    "hallucinated_citations": report.invalid_labels,
                },
            ),
        }


def _extract_title(markdown: str) -> str:
    """Pull the H1 for the artifact title, with a sane fallback."""
    if match := re.search(r"^#\s+(.+)$", markdown, re.MULTILINE):
        return match.group(1).strip()[:120]
    return "Untitled Essay"

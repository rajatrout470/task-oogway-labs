"""Artifact generation skill — Markdown or HTML/CSS documents.

Distinct from the essay skill: this handles "make me a one-page summary",
"build a comparison table", "give me a checklist I can share" — structured
documents rather than long-form prose.

HTML output is where the security posture matters. The model is instructed to
emit self-contained HTML with no scripts, but that instruction is *not* the
control. Generated HTML is sanitised server-side (core/sanitize.py) and
rendered inside a sandboxed iframe with a restrictive CSP client-side. The
prompt is a preference; the sanitiser and the sandbox are the guarantees.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from typing import Any

from app.agent.citations import strip_all_citations, used_evidence, validate_citations
from app.agent.skills.base import Skill, SkillContext, SkillResult, status, token
from app.core.logging import agent_log
from app.core.sanitize import sanitize_html
from app.providers.base import Message

MARKDOWN_SYSTEM = """You produce clean, well-structured Markdown documents \
grounded strictly in the Lenny's Podcast transcript evidence provided.

Rules:
- Use ONLY facts present in the evidence. Invent nothing.
- Cite evidence labels inline as [E1] as you draft — they are how grounding is \
verified, and they are removed before display.
- Attribute ideas to the operator who said them, by name.
- Structure for scanning: a clear H1, meaningful H2 sections, tables where data \
is comparative, bullets for lists.
- Be specific and concise. No filler, no preamble, no meta-commentary.

Output Markdown only. No code fences around the whole document.
"""

HTML_SYSTEM = """You produce a single self-contained HTML document, grounded \
strictly in the Lenny's Podcast transcript evidence provided.

Content rules:
- Use ONLY facts present in the evidence. Invent nothing.
- Cite evidence labels inline as [E1] as you draft — they are removed before \
display but are how grounding is verified.
- Attribute ideas to the named operator.

Technical rules — these are strict:
- Output ONE complete HTML document. No Markdown, no code fences, no commentary.
- ALL styling in a single <style> tag in the head. No external stylesheets, no \
external fonts, no CDN links, no remote images.
- NO JavaScript. No <script> tags, no inline event handlers (onclick etc.), no \
javascript: URLs. Scripts are blocked at render time and their absence is \
enforced, so including any is wasted output.
- Design for readability: generous line height, a constrained measure \
(max-width around 720px), clear type hierarchy, restrained colour.
- Define colours so the document reads well on a light background.
- Make it responsive: relative units, and any wide table wrapped in a container \
with overflow-x: auto.
"""


class ArtifactSkill(Skill):
    name = "create_artifact"
    description = (
        "Generate a document artifact — a Markdown or HTML/CSS page — from "
        "transcript evidence. Use for summaries, comparison tables, checklists, "
        "briefs, or when the user asks for a document, page, table, or "
        "'something I can share'. Not for long-form essays (use "
        "write_ship30_essay for those)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What the document should contain"},
            "format": {
                "type": "string",
                "enum": ["markdown", "html"],
                "description": "Output format. Default markdown; html when the "
                "user wants a styled or visual page.",
            },
        },
        "required": ["query"],
    }

    async def run(self, ctx: SkillContext) -> AsyncIterator[dict[str, Any]]:
        started = time.monotonic()
        kind = ctx.options.get("format") or _infer_format(ctx.query)

        yield status("retrieving", "Gathering supporting evidence…")

        evidence = ctx.prior_evidence
        if not evidence:
            retrieval = await ctx.retriever.retrieve(ctx.query, top_k=10)
            if not retrieval.sufficient:
                text = (
                    "**I can't build this from the transcripts.**\n\n"
                    "I didn't find enough grounded material on this topic. I'd rather "
                    "tell you that than generate a document that looks authoritative "
                    "and isn't."
                )
                if retrieval.adjacent_topics:
                    text += (
                        f"\n\n**Well-covered topics:** "
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
                    ),
                }
                return
            evidence = retrieval.evidence

        yield {"type": "evidence", "evidence": [e.to_public() for e in evidence]}
        yield status("generating", f"Building {kind} artifact…")

        system = HTML_SYSTEM if kind == "html" else MARKDOWN_SYSTEM
        evidence_block = "\n\n".join(e.to_prompt_block() for e in evidence)

        raw = []
        async for chunk in ctx.provider.stream(
            system=system,
            messages=[
                Message(
                    role="user",
                    content=(
                        f"<evidence_set>\n{evidence_block}\n</evidence_set>\n\n"
                        f"Request: {ctx.query}\n\n"
                        f"Produce the {kind} document now."
                    ),
                )
            ],
            max_tokens=3000,
            temperature=0.3,
        ):
            raw.append(chunk)
            yield token(chunk)

        draft = "".join(raw)

        report = validate_citations(draft, evidence)
        cited = used_evidence(evidence, report)
        content = strip_all_citations(report.text)
        content = _strip_code_fences(content)

        sanitize_report = None
        if kind == "html":
            # Server-side sanitisation. The client sandbox is defence in depth,
            # not the only defence — an artifact fetched via the API by any
            # other consumer must already be safe.
            content, sanitize_report = sanitize_html(content)
            if sanitize_report.removed:
                agent_log.warning(
                    "artifact_html_sanitized",
                    removed=sanitize_report.removed,
                    counts=sanitize_report.counts,
                )

        title = _extract_title(content, kind) or _fallback_title(ctx.query)

        artifact = {
            "kind": kind,
            "title": title,
            "content": content,
            "template": "document",
            "metadata": {
                "word_count": len(content.split()),
                "sanitized": bool(sanitize_report and sanitize_report.removed),
                "sanitizer_removed": sanitize_report.removed if sanitize_report else [],
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

        note = ""
        if sanitize_report and sanitize_report.removed:
            note = (
                f" I stripped {len(sanitize_report.removed)} unsafe element(s) "
                "during sanitisation."
            )

        agent_log.info(
            "artifact_complete",
            kind=kind,
            title=title,
            evidence_cited=len(cited),
            sanitized=bool(sanitize_report and sanitize_report.removed),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

        yield {
            "type": "done",
            "result": SkillResult(
                text=(
                    f"I've created **{title}** as "
                    f"{'an HTML page' if kind == 'html' else 'a Markdown document'}, "
                    f"grounded in {len({e.chunk.episode_slug for e in cited})} episodes. "
                    f"It's open in the Artifact Viewer.{note}"
                ),
                skill=self.name,
                evidence=cited,
                citations=[e.to_citation(i) for i, e in enumerate(cited)],
                artifact=artifact,
                latency_ms=int((time.monotonic() - started) * 1000),
                meta={"kind": kind},
            ),
        }


# ---------------------------------------------------------------------------

_HTML_HINTS = re.compile(
    r"\b(html|web ?page|styled|css|landing page|visual|one[- ]pager|dashboard|infographic)\b",
    re.I,
)


def _infer_format(query: str) -> str:
    """Guess the output format when the caller didn't specify one."""
    return "html" if _HTML_HINTS.search(query) else "markdown"


def _strip_code_fences(text: str) -> str:
    """Remove a wrapping ```lang ... ``` fence.

    Smaller models frequently wrap their whole output in a fence despite being
    told not to. Rendering that verbatim would show the user raw markup, which
    is precisely the 'raw code dump' failure the brief calls out.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def _extract_title(content: str, kind: str) -> str | None:
    if kind == "html":
        if match := re.search(r"<title>(.*?)</title>", content, re.I | re.S):
            return match.group(1).strip()[:120]
        if match := re.search(r"<h1[^>]*>(.*?)</h1>", content, re.I | re.S):
            return re.sub(r"<[^>]+>", "", match.group(1)).strip()[:120]
        return None
    if match := re.search(r"^#\s+(.+)$", content, re.MULTILINE):
        return match.group(1).strip()[:120]
    return None


def _fallback_title(query: str) -> str:
    words = query.strip().split()[:8]
    return " ".join(words).rstrip(".,?!").title()[:120] or "Untitled Document"

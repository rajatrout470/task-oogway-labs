"""Artifact endpoints.

Artifacts are generated as a side effect of a chat turn, so there is no "create"
endpoint here — creating one out of band would bypass the grounding pipeline
that makes artifacts trustworthy in the first place. These routes read, update,
and export what the agent produced.

Every HTML artifact is sanitised before storage (core/sanitize.py), so anything
these endpoints return is already safe regardless of how the caller renders it.
The `/render` route additionally serves it with a hard CSP, for the iframe path.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response

from app.api.schemas import ArtifactResponse, ArtifactSummary, UpdateArtifactRequest
from app.core.errors import NotFoundError
from app.core.logging import artifact_log
from app.core.sanitize import sanitize_html
from app.db import repositories as repo

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}", response_model=ArtifactResponse, summary="Get an artifact")
async def get_artifact(artifact_id: UUID) -> ArtifactResponse:
    row = await repo.get_artifact(artifact_id)
    if row is None:
        raise NotFoundError(f"Artifact {artifact_id} not found.")
    return ArtifactResponse(**dict(row))


@router.get("/by-session/{session_id}", response_model=list[ArtifactSummary])
async def list_session_artifacts(session_id: UUID) -> list[ArtifactSummary]:
    """Summaries only — content is deliberately excluded.

    A session can hold several 1,250-word essays; sending every body just to
    populate a picker would waste bandwidth on content the user has not opened.
    """
    rows = await repo.list_artifacts(session_id)
    return [ArtifactSummary(**dict(r)) for r in rows]


@router.patch("/{artifact_id}", response_model=ArtifactResponse, summary="Edit an artifact")
async def update_artifact(
    artifact_id: UUID, body: UpdateArtifactRequest
) -> ArtifactResponse:
    """Save a user edit from the viewer.

    User-supplied HTML is re-sanitised on the way in. The content arriving here
    is untrusted for exactly the same reason model output is — more so, since it
    can be crafted deliberately.
    """
    existing = await repo.get_artifact(artifact_id)
    if existing is None:
        raise NotFoundError(f"Artifact {artifact_id} not found.")

    content = body.content
    if existing["kind"] == "html":
        content, report = sanitize_html(content)
        if report.removed:
            artifact_log.warning(
                "artifact_update_sanitized",
                artifact_id=str(artifact_id),
                removed=report.removed,
            )

    row = await repo.update_artifact(artifact_id, content)
    if row is None:  # pragma: no cover - existence checked above
        raise NotFoundError(f"Artifact {artifact_id} not found.")
    return ArtifactResponse(**dict(row))


@router.get("/{artifact_id}/render", summary="Render HTML for the sandboxed iframe")
async def render_artifact(artifact_id: UUID) -> Response:
    """Serve an HTML artifact for iframe embedding, under a restrictive CSP.

    This is the third defence layer (prompt → sanitiser → sandbox). The CSP is
    set here, server-side, rather than in a <meta> tag inside the generated
    document, because a meta CSP is part of the content the model produced and
    could in principle be omitted or malformed. A response header cannot be.

    Policy:
        default-src 'none'    nothing loads unless explicitly allowed
        style-src 'unsafe-inline'  inline <style> only — no stylesheet URLs
        img-src data:         embedded images only, never remote
        script-src            omitted entirely, so scripts cannot execute
        frame-ancestors 'self'  only our own app may frame this
        sandbox               no scripts, no same-origin, no forms, no top-level
                              navigation — even if everything above failed
    """
    row = await repo.get_artifact(artifact_id)
    if row is None:
        raise NotFoundError(f"Artifact {artifact_id} not found.")

    if row["kind"] != "html":
        # Markdown is rendered client-side by the viewer; serving it as HTML
        # here would mean a second, divergent rendering path.
        raise NotFoundError("Only HTML artifacts can be rendered. This one is Markdown.")

    # Re-sanitise on read. Stored content was sanitised at write time, but this
    # is the response that actually reaches a browser, and the cost is trivial
    # next to the consequence of being wrong.
    content, report = sanitize_html(row["content"])
    if report.removed:
        artifact_log.warning(
            "artifact_render_sanitized",
            artifact_id=str(artifact_id),
            removed=report.removed,
            note="stored content contained unsafe markup at read time",
        )

    csp = "; ".join([
        "default-src 'none'",
        "style-src 'unsafe-inline'",
        "img-src data:",
        "font-src data:",
        "form-action 'none'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
        "sandbox allow-popups",
    ])

    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )


@router.get("/{artifact_id}/download", summary="Download an artifact")
async def download_artifact(artifact_id: UUID) -> Response:
    """Serve the raw source as a file attachment.

    Content-Disposition: attachment plus a text/plain content type, so a browser
    saves the file rather than rendering it — a download must never become an
    execution path.
    """
    row = await repo.get_artifact(artifact_id)
    if row is None:
        raise NotFoundError(f"Artifact {artifact_id} not found.")

    extension = "html" if row["kind"] == "html" else "md"
    filename = _safe_filename(row["title"], extension)

    return Response(
        content=row["content"],
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _safe_filename(title: str, extension: str) -> str:
    """Build a filename that cannot escape a directory or break the header.

    Quotes, path separators and control characters are all removed rather than
    escaped — a generated title has no legitimate need for any of them.
    """
    import re

    stem = re.sub(r"[^A-Za-z0-9 _-]", "", title).strip() or "artifact"
    stem = re.sub(r"\s+", "-", stem)[:80]
    return f"{stem}.{extension}"

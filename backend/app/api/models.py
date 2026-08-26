"""Model/provider introspection.

Backs the UI's provider indicator. The brief requires the active provider and
model to be visible, and this is how the frontend learns it — nothing is
hardcoded client-side, so the indicator cannot drift from reality.

Deliberately read-only. Switching providers is a *configuration* action
(edit .env, restart), not an API call. Exposing a mutating endpoint would create
a second source of truth for model selection and quietly break the "switch
models without touching application code" contract by moving the decision into
runtime state that no config file records.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import ModelsResponse
from app.core.config import get_settings
from app.providers.registry import provider_status

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models", response_model=ModelsResponse, summary="Provider and skill status")
async def get_models() -> ModelsResponse:
    """Live provider health, the effective model, and the skill catalogue.

    Secrets are never returned — provider health reports whether a key is
    *present*, never any part of its value (see Settings.redacted).
    """
    from app.agent.orchestrator import get_router

    settings = get_settings()
    status = await provider_status(settings)

    return ModelsResponse(
        **status,
        skills=[
            {"name": s["name"], "description": s["description"]}
            for s in get_router().describe()
        ],
        retrieval={
            "top_k": settings.retrieval_top_k,
            "candidates": settings.retrieval_candidates,
            "min_score": settings.retrieval_min_score,
            "max_episodes": settings.retrieval_max_episodes,
            "embedding_model": settings.ollama_embed_model,
        },
    )


@router.get("/corpus", summary="Knowledge base status")
async def get_corpus() -> dict:
    """What is indexed, and from which source commit.

    Shown in the UI so a user can tell whether the assistant is answering from
    the full 303-episode corpus or a partial ingest — which materially changes
    how much to trust an 'I don't have evidence for that'.
    """
    from app.ingest.pipeline import corpus_status

    return await corpus_status()

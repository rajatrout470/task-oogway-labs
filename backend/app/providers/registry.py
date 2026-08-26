"""Provider selection and fallback.

The one place that decides *which* model serves a request. Everything above
calls `get_provider()` and receives something implementing BaseProvider.

Fallback policy, in full:

  1. Try the configured LLM_PROVIDER.
  2. If it fails its health check, and LLM_FALLBACK_PROVIDER names a different
     provider, try that.
  3. If that also fails, raise ProviderUnavailableError carrying *both*
     failure reasons, so the operator sees the whole picture rather than
     debugging one layer at a time.

A fallback is never silent: the resulting provider is flagged `was_fallback`,
logged at WARNING, and surfaced in the UI's model indicator. A user must always
be able to tell which model actually answered them — a local 7B and a frontier
cloud model produce visibly different quality, and hiding the switch would make
that difference look like the product being randomly unreliable.

Health results are cached briefly: an HTTP probe before every message would add
latency to every turn, but a stale cache would keep routing to a dead provider.
"""

from __future__ import annotations

import time

from app.core.config import Settings, get_settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import model_log
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import BaseProvider
from app.providers.ollama_provider import OllamaProvider

_BUILDERS = {
    "ollama": OllamaProvider,
    "anthropic": AnthropicProvider,
}

# name -> (healthy, reason, checked_at_monotonic)
_health_cache: dict[str, tuple[bool, str, float]] = {}
_HEALTH_TTL_SECONDS = 30.0


def build_provider(name: str, settings: Settings | None = None) -> BaseProvider:
    """Instantiate a provider by name. Does not check health."""
    settings = settings or get_settings()
    builder = _BUILDERS.get(name)
    if builder is None:
        raise ProviderUnavailableError(
            f"Unknown provider '{name}'.",
            detail={"known_providers": sorted(_BUILDERS)},
            remediation=f"Set LLM_PROVIDER to one of: {', '.join(sorted(_BUILDERS))}.",
        )
    return builder(settings)


async def check_health(name: str, *, force: bool = False) -> tuple[bool, str]:
    """Health for one provider, cached for _HEALTH_TTL_SECONDS."""
    now = time.monotonic()
    if not force and (cached := _health_cache.get(name)):
        healthy, reason, checked_at = cached
        if now - checked_at < _HEALTH_TTL_SECONDS:
            return healthy, reason

    try:
        healthy, reason = await build_provider(name).health()
    except Exception as exc:
        healthy, reason = False, f"Health check raised: {exc}"

    _health_cache[name] = (healthy, reason, now)
    return healthy, reason


async def get_provider(settings: Settings | None = None) -> BaseProvider:
    """Return a healthy provider, falling back if configured to."""
    settings = settings or get_settings()
    primary = settings.llm_provider
    fallback = settings.llm_fallback_provider

    healthy, reason = await check_health(primary)
    if healthy:
        return build_provider(primary, settings)

    model_log.warning("provider_unhealthy", provider=primary, reason=reason)

    if fallback in (None, "none", "", primary):
        raise ProviderUnavailableError(
            f"Provider '{primary}' is unavailable: {reason}",
            detail={"provider": primary, "fallback_configured": False},
        )

    fallback_healthy, fallback_reason = await check_health(fallback)
    if not fallback_healthy:
        raise ProviderUnavailableError(
            f"Both providers are unavailable. "
            f"'{primary}': {reason} | '{fallback}': {fallback_reason}",
            detail={
                "primary": {"provider": primary, "reason": reason},
                "fallback": {"provider": fallback, "reason": fallback_reason},
            },
        )

    model_log.warning(
        "provider_fallback_engaged",
        primary=primary,
        primary_reason=reason,
        fallback=fallback,
    )
    provider = build_provider(fallback, settings)
    # Read by the API layer to tell the UI that a downgrade happened.
    provider._was_fallback = True  # type: ignore[attr-defined]
    return provider


async def provider_status(settings: Settings | None = None) -> dict:
    """Full provider picture for GET /api/models and the UI indicator.

    Reports every provider's health, not just the active one, so the operator
    can see that (say) the cloud path is one API key away from working.
    """
    settings = settings or get_settings()
    providers = []

    for name in sorted(_BUILDERS):
        healthy, reason = await check_health(name)
        instance = build_provider(name, settings)
        providers.append(
            {
                "name": name,
                "model": instance.model,
                "healthy": healthy,
                "reason": reason,
                "supports_native_tools": instance.supports_native_tools,
                "is_active": name == settings.llm_provider,
                "is_fallback": name == settings.llm_fallback_provider,
                **(
                    {"runtime": getattr(instance, "runtime", None)}
                    if name == "anthropic"
                    else {}
                ),
            }
        )

    active_healthy = next(
        (p["healthy"] for p in providers if p["name"] == settings.llm_provider), False
    )
    effective = settings.llm_provider
    if not active_healthy and settings.llm_fallback_provider not in ("none", ""):
        if any(
            p["healthy"] and p["name"] == settings.llm_fallback_provider for p in providers
        ):
            effective = settings.llm_fallback_provider

    return {
        "configured_provider": settings.llm_provider,
        "fallback_provider": settings.llm_fallback_provider,
        "effective_provider": effective,
        "degraded": effective != settings.llm_provider,
        "providers": providers,
    }


def reset_health_cache() -> None:
    """Test hook."""
    _health_cache.clear()

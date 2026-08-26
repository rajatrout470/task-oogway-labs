"""API contract tests.

Cover the request/response contract and the failure modes the brief calls out —
missing keys, unavailable providers, empty retrieval, database down — without
requiring any of those dependencies to actually be present.

Tests needing a live PostgreSQL are marked `integration` and skipped by default:
    pytest                      # unit only
    pytest -m integration       # requires a database
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import KnowledgeBaseEmptyError, ProviderUnavailableError
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_liveness_never_depends_on_dependencies() -> None:
    """An orchestrator restarting the container because Postgres is slow makes
    an outage worse, so /live must answer without touching anything."""
    response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_advertises_docs_and_health() -> None:
    body = client.get("/").json()
    assert body["docs"] == "/docs"
    assert body["health"] == "/api/health"


def test_openapi_schema_is_generated() -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/sessions" in schema["paths"]
    assert "/api/models" in schema["paths"]
    assert "/api/artifacts/{artifact_id}/render" in schema["paths"]


# ---------------------------------------------------------------------------
# Validation contract
# ---------------------------------------------------------------------------


def test_empty_message_is_rejected() -> None:
    response = client.post(
        "/api/sessions/00000000-0000-0000-0000-000000000000/messages",
        json={"message": "   "},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_oversized_message_is_rejected() -> None:
    """A 50k-character 'question' is a mistake or an attack, not a question."""
    response = client.post(
        "/api/sessions/00000000-0000-0000-0000-000000000000/messages",
        json={"message": "x" * 50_000},
    )
    assert response.status_code == 422


def test_unknown_skill_is_rejected() -> None:
    response = client.post(
        "/api/sessions/00000000-0000-0000-0000-000000000000/messages",
        json={"message": "hello", "skill": "delete_everything"},
    )
    assert response.status_code == 422


def test_malformed_uuid_is_rejected() -> None:
    assert client.get("/api/sessions/not-a-uuid").status_code == 422


def test_validation_errors_use_the_shared_error_shape() -> None:
    """Clients must not face two different error shapes."""
    response = client.post(
        "/api/sessions/00000000-0000-0000-0000-000000000000/messages", json={}
    )
    body = response.json()

    assert "error" in body
    assert {"code", "message"} <= set(body["error"])


# ---------------------------------------------------------------------------
# Error contract
# ---------------------------------------------------------------------------


def test_provider_error_carries_actionable_remediation() -> None:
    """The most common failures here are operational, so a bare 503 forces the
    developer to guess. Every such error names the fix."""
    error = ProviderUnavailableError("Ollama is not running.")
    body = error.to_response().body.decode()

    assert "ollama" in body.lower()
    assert error.remediation and "pull" in error.remediation


def test_empty_knowledge_base_is_distinguished_from_no_results() -> None:
    """A setup problem, deliberately distinct from the valid answer
    that is 'no relevant results'. Conflating them hides a real failure."""
    error = KnowledgeBaseEmptyError("not ingested")

    assert error.status_code == 503
    assert error.code == "knowledge_base_empty"
    assert "ingest" in (error.remediation or "")


def test_every_response_carries_a_request_id() -> None:
    """The id ties together model, retrieval and db logs for one answer."""
    assert client.get("/api/health/live").headers.get("X-Request-ID")


def test_supplied_request_id_is_echoed() -> None:
    response = client.get("/api/health/live", headers={"X-Request-ID": "trace-me-123"})
    assert response.headers["X-Request-ID"] == "trace-me-123"


# ---------------------------------------------------------------------------
# Configuration — the "switch models without touching code" contract
# ---------------------------------------------------------------------------


def test_settings_never_leak_secrets() -> None:
    settings = Settings(anthropic_api_key="sk-ant-super-secret-value")
    redacted = settings.redacted()

    assert "sk-ant-super-secret-value" not in str(redacted)
    assert redacted["anthropic"]["api_key_present"] is True


def test_missing_api_key_is_detected_without_raising() -> None:
    assert Settings(anthropic_api_key="").anthropic_configured is False
    assert Settings(anthropic_api_key="  ").anthropic_configured is False


def test_model_is_switchable_by_configuration_alone() -> None:
    """The core requirement: no application code names a model."""
    settings = Settings(llm_provider="ollama", ollama_model="llama3.1:8b")
    assert settings.ollama_model == "llama3.1:8b"
    assert settings.redacted()["ollama"]["model"] == "llama3.1:8b"


def test_embedding_dimension_mismatch_fails_at_boot() -> None:
    """A silent mismatch surfaces as an opaque pgvector error deep in ingestion;
    failing at construction gives an actionable message instead."""
    with pytest.raises(ValueError, match="768"):
        Settings(embedding_dimensions=1024)


def test_database_url_overrides_discrete_settings() -> None:
    settings = Settings(database_url="postgresql://u:p@remote:5432/db")
    assert settings.async_database_url == "postgresql://u:p@remote:5432/db"


def test_cors_origins_are_parsed_from_csv() -> None:
    settings = Settings(cors_allow_origins="http://a.com, http://b.com")
    assert settings.cors_origins == ["http://a.com", "http://b.com"]


# ---------------------------------------------------------------------------
# Provider registry and fallback
# ---------------------------------------------------------------------------


async def test_unknown_provider_names_the_valid_options() -> None:
    from app.providers.registry import build_provider

    with pytest.raises(ProviderUnavailableError) as excinfo:
        build_provider("gpt-5-turbo-ultra")

    assert "ollama" in str(excinfo.value.detail)


async def test_fallback_engages_when_primary_is_unhealthy(monkeypatch) -> None:
    """Missing cloud key must degrade to local, not 500."""
    from app.providers import registry

    registry.reset_health_cache()

    async def fake_health(name: str, *, force: bool = False) -> tuple[bool, str]:
        return (False, "no api key") if name == "anthropic" else (True, "ok")

    monkeypatch.setattr(registry, "check_health", fake_health)

    settings = Settings(llm_provider="anthropic", llm_fallback_provider="ollama")
    provider = await registry.get_provider(settings)

    assert provider.name == "ollama"
    # The downgrade must be visible, never silent.
    assert getattr(provider, "_was_fallback", False) is True


async def test_both_providers_down_reports_both_reasons(monkeypatch) -> None:
    """Reporting one failure at a time makes the operator debug in layers."""
    from app.providers import registry

    registry.reset_health_cache()

    async def fake_health(name: str, *, force: bool = False) -> tuple[bool, str]:
        return False, f"{name} is unreachable"

    monkeypatch.setattr(registry, "check_health", fake_health)

    settings = Settings(llm_provider="anthropic", llm_fallback_provider="ollama")
    with pytest.raises(ProviderUnavailableError) as excinfo:
        await registry.get_provider(settings)

    message = str(excinfo.value)
    assert "anthropic" in message and "ollama" in message


async def test_no_fallback_configured_fails_loudly(monkeypatch) -> None:
    from app.providers import registry

    registry.reset_health_cache()

    async def fake_health(name: str, *, force: bool = False) -> tuple[bool, str]:
        return False, "down"

    monkeypatch.setattr(registry, "check_health", fake_health)

    settings = Settings(llm_provider="ollama", llm_fallback_provider="none")
    with pytest.raises(ProviderUnavailableError):
        await registry.get_provider(settings)


def test_ollama_declares_no_native_tool_support() -> None:
    """Drives the routing strategy — a 7B picks skills badly, so we route in code."""
    from app.providers.ollama_provider import OllamaProvider

    assert OllamaProvider(Settings()).supports_native_tools is False


def test_anthropic_declares_native_tool_support() -> None:
    from app.providers.anthropic_provider import AnthropicProvider

    assert AnthropicProvider(Settings()).supports_native_tools is True


def test_agent_sdk_runtime_degrades_when_cli_absent(monkeypatch) -> None:
    """The SDK drives the `claude` CLI as a subprocess. If it isn't installed we
    must fall back at construction, not fail at request time."""
    from app.providers import anthropic_provider

    monkeypatch.setattr(anthropic_provider, "_sdk_available", lambda: False)

    provider = anthropic_provider.AnthropicProvider(
        Settings(anthropic_agent_runtime="sdk", anthropic_api_key="sk-test")
    )
    assert provider.runtime == "messages"


# ---------------------------------------------------------------------------
# Integration — require a live database
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_session_lifecycle_persists() -> None:
    """Sessions must survive a restart, which means actually hitting the DB."""
    from app.db import repositories as repo

    user_id = await repo.ensure_user(None)
    session = await repo.create_session(user_id, title="test session")

    await repo.add_message(session["id"], "user", "first question")
    await repo.add_message(session["id"], "assistant", "an answer", skill="answer_from_transcripts")

    messages = await repo.get_messages(session["id"])
    assert [m["seq"] for m in messages] == [1, 2]
    assert messages[0]["role"] == "user"

    assert await repo.delete_session(session["id"]) is True
    assert await repo.get_session(session["id"]) is None


@pytest.mark.integration
async def test_sessions_are_independent_context_scopes() -> None:
    """A stated requirement: nothing may leak between sessions."""
    from app.db import repositories as repo

    user_id = await repo.ensure_user(None)
    first = await repo.create_session(user_id)
    second = await repo.create_session(user_id)

    await repo.add_message(first["id"], "user", "secret to session one")

    assert await repo.get_history_for_prompt(second["id"]) == []
    assert len(await repo.get_history_for_prompt(first["id"])) == 1

    await repo.delete_session(first["id"])
    await repo.delete_session(second["id"])


@pytest.mark.integration
async def test_deleting_a_session_cascades() -> None:
    from app.db import repositories as repo

    user_id = await repo.ensure_user(None)
    session = await repo.create_session(user_id)
    message = await repo.add_message(session["id"], "assistant", "answer")

    await repo.add_citations(
        message["id"],
        [{"label": "E1", "quote": "q", "episode_slug": "ep", "guest": "G", "rank": 0}],
    )
    await repo.create_artifact(session["id"], kind="markdown", title="T", content="body")

    await repo.delete_session(session["id"])

    assert await repo.get_citations([message["id"]]) == {}
    assert await repo.list_artifacts(session["id"]) == []


@pytest.mark.integration
async def test_artifact_versions_increment_rather_than_overwrite() -> None:
    """Losing a 1,250-word essay to a regenerate would be a bad experience."""
    from app.db import repositories as repo

    user_id = await repo.ensure_user(None)
    session = await repo.create_session(user_id)

    first = await repo.create_artifact(session["id"], kind="markdown", title="Essay", content="v1")
    second = await repo.create_artifact(session["id"], kind="markdown", title="Essay", content="v2")

    assert first["version"] == 1
    assert second["version"] == 2
    assert len(await repo.list_artifacts(session["id"])) == 2

    await repo.delete_session(session["id"])

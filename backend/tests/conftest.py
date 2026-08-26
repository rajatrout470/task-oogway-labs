"""Shared pytest configuration.

Unit tests must run on a machine with no database, no Ollama, and no API keys —
that is what makes them useful in CI and to a new contributor five minutes after
cloning. Tests that genuinely need infrastructure are marked and skipped unless
that infrastructure is actually reachable, so a default `pytest` run is always
green or genuinely broken, never "red because you didn't start Postgres".
"""

from __future__ import annotations

import socket

import pytest

from app.core.config import get_settings


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _database_reachable() -> bool:
    settings = get_settings()
    return _port_open(settings.postgres_host, settings.postgres_port)


def _ollama_reachable() -> bool:
    settings = get_settings()
    from urllib.parse import urlparse

    parsed = urlparse(settings.ollama_base_url)
    return _port_open(parsed.hostname or "localhost", parsed.port or 11434)


def pytest_collection_modifyitems(config, items) -> None:
    """Skip infrastructure-dependent tests when the infrastructure is absent."""
    db_ok = _database_reachable()
    ollama_ok = _ollama_reachable()

    skip_db = pytest.mark.skip(reason="no PostgreSQL reachable — run `make db-up`")
    skip_ollama = pytest.mark.skip(reason="Ollama not reachable — run `ollama serve`")

    for item in items:
        if "integration" in item.keywords and not db_ok:
            item.add_marker(skip_db)
        if "ollama" in item.keywords and not ollama_ok:
            item.add_marker(skip_ollama)


@pytest.fixture(autouse=True)
def _reset_provider_health_cache():
    """Provider health is cached for 30s. Without clearing it between tests, one
    test's monkeypatched health leaks into the next."""
    from app.providers.registry import reset_health_cache

    reset_health_cache()
    yield
    reset_health_cache()

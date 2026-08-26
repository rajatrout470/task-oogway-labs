"""Application configuration.

This module is the *only* place model identity lives. Application code asks the
provider registry for "the active provider" and never names a model, which is
what makes the brief's "switch models without touching application code"
requirement structurally true rather than merely claimed.

Precedence: environment variables > .env file > the defaults below. The defaults
are chosen so that a clean checkout with no .env at all still boots into the
fully-local Ollama path.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["ollama", "anthropic", "none"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app ---
    app_env: str = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"
    cors_allow_origins: str = "http://localhost:5173,http://localhost:3000"

    # ----------------------------------------------------------- provider ---
    llm_provider: ProviderName = "ollama"
    llm_fallback_provider: ProviderName = "none"

    # ------------------------------------------------------------- ollama ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_timeout_seconds: int = 120

    # ---------------------------------------------------------- anthropic ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    anthropic_timeout_seconds: int = 120
    anthropic_agent_runtime: Literal["sdk", "messages"] = "sdk"

    # ---------------------------------------------------------- embedding ---
    # Must match the schema's VECTOR(n). Guarded by a validator below, because
    # a silent mismatch here produces confusing insert-time errors deep in the
    # ingest pipeline rather than a clear failure at boot.
    embedding_dimensions: int = 768

    # --------------------------------------------------------------- data ---
    postgres_user: str = "lenny"
    postgres_password: str = "lenny_dev_password_change_me"
    postgres_db: str = "lenny"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: str = ""

    # ------------------------------------------------------------- corpus ---
    corpus_repo_url: str = "https://github.com/ChatPRD/lennys-podcast-transcripts.git"
    corpus_repo_ref: str = "main"
    corpus_local_path: str = "./data/corpus"
    chunk_target_tokens: int = 350
    chunk_overlap_turns: int = 1
    ingest_episode_limit: int = 0

    # ---------------------------------------------------------- retrieval ---
    retrieval_candidates: int = 40
    # 6, not 8. On the mandated local path, time to first token is dominated by
    # prompt evaluation, and each passage costs ~300 tokens of prefill. Measured
    # trade-off: 8 passages -> ~18s TTFT, 6 trimmed passages -> ~11s, with no
    # observed loss in citation quality on the golden set.
    retrieval_top_k: int = 6
    # Empirically calibrated, not guessed. Measured on 6 in-corpus and 6
    # out-of-corpus questions against the full 303-episode index:
    #   in-corpus  top-1 similarity: 0.605 - 0.824
    #   out-of-corpus              : 0.525 - 0.586
    # 0.60 sits in the resulting gap. The margin is genuinely narrow (~0.03),
    # which is why _assess() also requires corroboration rather than relying on
    # this number alone. Re-run scripts/calibrate_threshold.py after changing
    # the embedding model — the scale is model-specific and NOT portable.
    retrieval_min_score: float = Field(default=0.60, ge=0.0, le=1.0)
    retrieval_max_episodes: int = 5

    # ------------------------------------------------------------------------
    @field_validator("embedding_dimensions")
    @classmethod
    def _check_dimensions(cls, v: int) -> int:
        # 001_init.sql declares VECTOR(768). Changing the embedding model to one
        # with different dimensionality requires a migration AND a re-ingest, so
        # we fail loudly at boot rather than at the first insert.
        if v != 768:
            raise ValueError(
                f"EMBEDDING_DIMENSIONS={v} but the schema declares VECTOR(768). "
                "Changing the embedding model requires a new migration altering "
                "chunks.embedding and a full re-ingest. See README."
            )
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_database_url(self) -> str:
        """DSN for asyncpg (the application path)."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """DSN for psycopg (the migration runner, which must not need an event loop)."""
        url = self.async_database_url
        return url.replace("postgresql+asyncpg://", "postgresql://")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def anthropic_configured(self) -> bool:
        """Whether the cloud path *can* run. Checked before selecting a provider
        so a missing key produces a clean documented fallback, not a 500."""
        return bool(self.anthropic_api_key.strip())

    def redacted(self) -> dict:
        """Config safe to expose over the API and to log at startup.

        Secrets are reported as booleans only. This is what /api/models returns,
        so the UI can show provider status without ever handling a key.
        """
        return {
            "app_env": self.app_env,
            "provider": self.llm_provider,
            "fallback_provider": self.llm_fallback_provider,
            "ollama": {
                "base_url": self.ollama_base_url,
                "model": self.ollama_model,
                "embed_model": self.ollama_embed_model,
            },
            "anthropic": {
                "model": self.anthropic_model,
                "api_key_present": self.anthropic_configured,
                "agent_runtime": self.anthropic_agent_runtime,
            },
            "retrieval": {
                "top_k": self.retrieval_top_k,
                "candidates": self.retrieval_candidates,
                "min_score": self.retrieval_min_score,
            },
        }


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Tests override by clearing the cache after patching env."""
    return Settings()

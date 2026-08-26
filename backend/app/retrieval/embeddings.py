"""Embedding generation via Ollama.

Embeddings run locally by design (PRD assumption A7): a demo that requires a
cloud API key merely to *ingest* the corpus is not really a local demo. The
same model embeds both documents at ingest time and queries at search time —
they must match exactly or cosine similarity is meaningless, which is why there
is one client class used by both paths rather than two call sites.

Failure handling is explicit because the two most common setup mistakes —
Ollama not running, and the embedding model not pulled — are indistinguishable
from a generic connection error unless you look for them.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import retrieval_log

# ---------------------------------------------------------------------------
# Task prefixes.
#
# nomic-embed-text is an *asymmetric* embedding model: it was trained with task
# prefixes and expects "search_document: " on indexed passages and
# "search_query: " on queries. Omitting them is not a subtle quality loss — it
# collapses the separation between relevant and irrelevant results, because
# every pair of English texts lands in a narrow high-similarity band.
#
# That directly breaks the abstention gate, which is our #2 success metric: with
# no prefixes, an out-of-corpus question about Kubernetes scored 0.52 while a
# genuine in-corpus question scored 0.50, leaving no threshold that could
# separate them. With prefixes, the distributions pull apart.
#
# Keyed by model family, because this is a property of the model, not of us.
# Models without an entry get no prefix, which is correct for symmetric models
# like mxbai-embed-large.
# ---------------------------------------------------------------------------
_TASK_PREFIXES: dict[str, tuple[str, str]] = {
    # family: (document_prefix, query_prefix)
    "nomic-embed-text": ("search_document: ", "search_query: "),
}


class EmbeddingClient:
    """Async client for Ollama's /api/embed endpoint."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.ollama_embed_model
        self.base_url = self.settings.ollama_base_url.rstrip("/")
        self.dimensions = self.settings.embedding_dimensions

        family = self.model.split(":")[0]
        self._doc_prefix, self._query_prefix = _TASK_PREFIXES.get(family, ("", ""))

    @property
    def uses_task_prefixes(self) -> bool:
        return bool(self._doc_prefix or self._query_prefix)

    async def health(self) -> tuple[bool, str]:
        """Check the daemon is up AND the embedding model is actually pulled.

        Returns (ok, human-readable reason). Distinguishing these two cases is
        the difference between a useful error and a support ticket.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                available = {m["name"] for m in resp.json().get("models", [])}
        except httpx.HTTPError as exc:
            return False, f"Ollama unreachable at {self.base_url}: {exc}"

        # Ollama reports "nomic-embed-text:latest" for a bare "nomic-embed-text".
        wanted = self.model.split(":")[0]
        if not any(n == self.model or n.split(":")[0] == wanted for n in available):
            return False, (
                f"Embedding model '{self.model}' is not pulled. "
                f"Run: ollama pull {self.model}"
            )
        return True, "ok"

    async def embed(
        self,
        texts: list[str],
        *,
        kind: str = "document",
        batch_size: int = 16,
    ) -> list[list[float]]:
        """Embed a list of texts, preserving order.

        `kind` must be "document" when indexing corpus passages and "query" when
        embedding a user's question. For asymmetric models the two get different
        task prefixes, and mixing them up silently degrades every search — so
        the argument is explicit rather than inferred.

        Batched because embedding ~22k chunks one HTTP request at a time is
        dominated by round-trip overhead; batches of 16 keep memory flat while
        cutting wall-clock time substantially.
        """
        if not texts:
            return []

        prefix = self._doc_prefix if kind == "document" else self._query_prefix
        prepared = [f"{prefix}{t}" for t in texts] if prefix else texts

        vectors: list[list[float]] = []
        timeout = httpx.Timeout(self.settings.ollama_timeout_seconds, connect=10.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            for start in range(0, len(prepared), batch_size):
                batch = prepared[start : start + batch_size]
                vectors.extend(await self._embed_batch(client, batch))

        return vectors

    async def embed_one(self, text: str, *, kind: str = "query") -> list[float]:
        """Embed a single text. Defaults to "query" — the overwhelmingly common
        single-text case is a user's question at search time."""
        result = await self.embed([text], kind=kind)
        return result[0]

    async def _embed_batch(
        self, client: httpx.AsyncClient, batch: list[str], *, attempts: int = 3
    ) -> list[list[float]]:
        """POST one batch, retrying transient failures with backoff."""
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                resp = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": batch},
                )

                if resp.status_code == 404:
                    # Ollama returns 404 when the model isn't pulled — a setup
                    # problem, not a transient one. Retrying wastes time.
                    raise ProviderUnavailableError(
                        f"Embedding model '{self.model}' is not available in Ollama.",
                        detail={"model": self.model, "base_url": self.base_url},
                        remediation=f"Run: ollama pull {self.model}",
                    )

                resp.raise_for_status()
                embeddings = resp.json().get("embeddings", [])

                if len(embeddings) != len(batch):
                    raise ValueError(
                        f"Ollama returned {len(embeddings)} embeddings for {len(batch)} inputs"
                    )

                # Guard the dimension contract here rather than at INSERT time,
                # where the failure surfaces as an opaque pgvector type error.
                if embeddings and len(embeddings[0]) != self.dimensions:
                    raise ProviderUnavailableError(
                        f"Embedding model '{self.model}' returns "
                        f"{len(embeddings[0])}-dimensional vectors, but the schema "
                        f"and EMBEDDING_DIMENSIONS expect {self.dimensions}.",
                        remediation=(
                            "Either set OLLAMA_EMBED_MODEL back to a 768-dim model "
                            "(nomic-embed-text), or add a migration altering "
                            "chunks.embedding and re-ingest."
                        ),
                    )

                return embeddings

            except ProviderUnavailableError:
                raise  # Setup errors are never retried.
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < attempts:
                    backoff = 2 ** (attempt - 1)
                    retrieval_log.warning(
                        "embed_batch_retry",
                        attempt=attempt,
                        max_attempts=attempts,
                        backoff_seconds=backoff,
                        error=str(exc),
                    )
                    await asyncio.sleep(backoff)

        raise ProviderUnavailableError(
            f"Embedding failed after {attempts} attempts: {last_error}",
            detail={"model": self.model, "base_url": self.base_url},
        )

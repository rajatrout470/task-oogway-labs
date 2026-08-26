"""Ollama provider — the mandated local demo path.

Talks to Ollama's native /api/chat rather than its OpenAI-compatibility shim:
the native endpoint exposes `options` (context length, temperature, repeat
penalty) that materially affect grounded-answer quality on a 7B model, and the
compatibility layer hides them.

`supports_native_tools` is False on purpose. Ollama can technically emit tool
calls for models that support them, but a 7B model choosing between four skills
is measurably unreliable, and a wrong choice here means the user's question is
answered by the essay writer. Deterministic routing is the honest engineering
answer; see agent/router.py.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import ProviderTimeoutError, ProviderUnavailableError
from app.core.logging import model_log
from app.providers.base import BaseProvider, CompletionResult, Message


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.ollama_model
        self.base_url = self.settings.ollama_base_url.rstrip("/")
        self.timeout = self.settings.ollama_timeout_seconds

    async def health(self) -> tuple[bool, str]:
        """Distinguish 'daemon down' from 'model not pulled' — different fixes."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                available = [m["name"] for m in resp.json().get("models", [])]
        except httpx.HTTPError as exc:
            return False, (
                f"Ollama is not reachable at {self.base_url}. "
                f"Start it with `ollama serve`. ({type(exc).__name__})"
            )

        if not self._model_present(available):
            return False, (
                f"Ollama is running but '{self.model}' is not pulled. "
                f"Run: ollama pull {self.model}"
            )
        return True, "ok"

    def _model_present(self, available: list[str]) -> bool:
        """Ollama reports 'qwen2.5:7b-instruct' or bare names with ':latest'."""
        wanted = self.model.split(":")[0]
        return any(n == self.model or n.split(":")[0] == wanted for n in available)

    def _payload(
        self, system: str, messages: list[Message], max_tokens: int, temperature: float
    ) -> dict:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                *[{"role": m.role, "content": m.content} for m in messages],
            ],
            # Keep the model resident between turns. Measured on an M-series
            # laptop, a cold load of qwen2.5:7b costs ~27s before a single token
            # appears; Ollama's default 5-minute idle unload means a user who
            # pauses to read an answer pays that again on their next question.
            # 30 minutes covers a realistic working session.
            "keep_alive": "30m",
            "options": {
                # Low temperature: this product synthesises cited evidence, it
                # does not brainstorm. Creativity here shows up as invention.
                "temperature": temperature,
                "num_predict": max_tokens,
                # 8192 context. Our prompts run ~3-5k tokens with 8 evidence
                # passages; the default 2048 would silently truncate the
                # evidence block and produce ungrounded answers that look fine.
                "num_ctx": 8192,
                "repeat_penalty": 1.05,
            },
        }

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> CompletionResult:
        started = time.monotonic()
        payload = self._payload(system, messages, max_tokens, temperature) | {"stream": False}

        model_log.info(
            "generation_start",
            provider=self.name,
            model=self.model,
            prompt_chars=len(system) + sum(len(m.content) for m in messages),
            max_tokens=max_tokens,
        )

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0)) as c:
                resp = await c.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as exc:
            model_log.error("generation_timeout", provider=self.name, timeout=self.timeout)
            raise ProviderTimeoutError(
                f"Ollama did not respond within {self.timeout}s.",
                detail={"model": self.model},
            ) from exc
        except httpx.HTTPError as exc:
            model_log.error("generation_failed", provider=self.name, error=str(exc))
            raise ProviderUnavailableError(
                f"Ollama request failed: {exc}", detail={"model": self.model}
            ) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        usage = {
            "input_tokens": data.get("prompt_eval_count", 0),
            "output_tokens": data.get("eval_count", 0),
        }

        model_log.info(
            "generation_complete",
            provider=self.name,
            model=self.model,
            latency_ms=latency_ms,
            **usage,
        )

        return CompletionResult(
            text=data.get("message", {}).get("content", ""),
            provider=self.name,
            model=self.model,
            latency_ms=latency_ms,
            token_usage=usage,
        )

    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        started = time.monotonic()
        payload = self._payload(system, messages, max_tokens, temperature) | {"stream": True}
        first_token_ms: int | None = None

        model_log.info("stream_start", provider=self.name, model=self.model)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0)) as c:
                async with c.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            # A partial line at a chunk boundary is normal; a
                            # dropped token is preferable to a failed stream.
                            continue

                        if token := event.get("message", {}).get("content"):
                            if first_token_ms is None:
                                first_token_ms = int((time.monotonic() - started) * 1000)
                                # TTFT is a named guardrail metric in the PRD,
                                # so it is logged as its own event.
                                model_log.info(
                                    "stream_first_token",
                                    provider=self.name,
                                    model=self.model,
                                    ttft_ms=first_token_ms,
                                )
                            yield token

                        if event.get("done"):
                            model_log.info(
                                "stream_complete",
                                provider=self.name,
                                model=self.model,
                                ttft_ms=first_token_ms,
                                total_ms=int((time.monotonic() - started) * 1000),
                                output_tokens=event.get("eval_count", 0),
                            )
                            return

        except httpx.TimeoutException as exc:
            model_log.error("stream_timeout", provider=self.name, timeout=self.timeout)
            raise ProviderTimeoutError(
                f"Ollama stopped responding after {self.timeout}s.",
                detail={"model": self.model},
            ) from exc
        except httpx.HTTPError as exc:
            model_log.error("stream_failed", provider=self.name, error=str(exc))
            raise ProviderUnavailableError(
                f"Ollama stream failed: {exc}", detail={"model": self.model}
            ) from exc

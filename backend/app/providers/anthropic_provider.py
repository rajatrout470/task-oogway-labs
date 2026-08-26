"""Anthropic provider — the cloud path.

Supports two runtimes, selected by ANTHROPIC_AGENT_RUNTIME:

  "sdk"      Claude Agent SDK. The brief's default. The SDK is a Python wrapper
             that drives the Claude Code CLI as a subprocess, giving us its
             agent loop, tool orchestration and permission model for free.

  "messages" Direct Anthropic Messages API. Fewer moving parts, no subprocess,
             no Node dependency.

Both are implemented because the SDK's subprocess requirement is a real
deployment constraint (it needs the `claude` CLI present in the image), and an
engagement that hard-depends on it would fail in environments where that
install is unavailable. The runtime degrades from "sdk" to "messages"
automatically if the SDK or CLI is missing, and says so in the logs.

See architecture.md "Agent SDK vs. direct transport" for the full trade-off.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from app.core.config import Settings, get_settings
from app.core.errors import ProviderTimeoutError, ProviderUnavailableError
from app.core.logging import model_log
from app.providers.base import BaseProvider, CompletionResult, Message


def _sdk_available() -> bool:
    """Whether claude_agent_sdk can actually be used.

    Importable is necessary but not sufficient — the SDK shells out to the
    `claude` CLI, so we check for the binary too. Discovering this at request
    time instead would turn a deployment gap into a user-facing 500.
    """
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False

    import shutil

    return shutil.which("claude") is not None


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.anthropic_model
        self.timeout = self.settings.anthropic_timeout_seconds

        requested = self.settings.anthropic_agent_runtime
        if requested == "sdk" and not _sdk_available():
            model_log.warning(
                "agent_sdk_unavailable",
                requested_runtime="sdk",
                effective_runtime="messages",
                reason="claude_agent_sdk or the `claude` CLI is not installed",
            )
            self.runtime = "messages"
        else:
            self.runtime = requested

    @property
    def supports_native_tools(self) -> bool:
        """Claude does model-driven tool selection reliably, so the agent layer
        lets it choose skills rather than routing deterministically."""
        return True

    async def health(self) -> tuple[bool, str]:
        if not self.settings.anthropic_configured:
            return False, (
                "ANTHROPIC_API_KEY is not set. Set it in .env, or use the local "
                "path with LLM_PROVIDER=ollama."
            )
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "The `anthropic` package is not installed."
        return True, "ok"

    # ------------------------------------------------------------------ #
    # Messages API runtime
    # ------------------------------------------------------------------ #

    def _client(self):
        import anthropic

        if not self.settings.anthropic_configured:
            raise ProviderUnavailableError(
                "ANTHROPIC_API_KEY is not configured.",
                remediation="Set ANTHROPIC_API_KEY in .env, or set LLM_PROVIDER=ollama.",
            )
        return anthropic.AsyncAnthropic(
            api_key=self.settings.anthropic_api_key, timeout=float(self.timeout)
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> CompletionResult:
        started = time.monotonic()
        model_log.info(
            "generation_start",
            provider=self.name,
            model=self.model,
            runtime=self.runtime,
            max_tokens=max_tokens,
        )

        try:
            client = self._client()
            response = await client.messages.create(
                model=self.model,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            raise self._translate(exc) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        model_log.info(
            "generation_complete",
            provider=self.name,
            model=self.model,
            latency_ms=latency_ms,
            **usage,
        )
        return CompletionResult(
            text=text,
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
        first_token_ms: int | None = None
        model_log.info("stream_start", provider=self.name, model=self.model, runtime=self.runtime)

        try:
            client = self._client()
            async with client.messages.stream(
                model=self.model,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens,
                temperature=temperature,
            ) as stream:
                async for token in stream.text_stream:
                    if first_token_ms is None:
                        first_token_ms = int((time.monotonic() - started) * 1000)
                        model_log.info(
                            "stream_first_token",
                            provider=self.name,
                            model=self.model,
                            ttft_ms=first_token_ms,
                        )
                    yield token
        except Exception as exc:
            raise self._translate(exc) from exc

        model_log.info(
            "stream_complete",
            provider=self.name,
            model=self.model,
            ttft_ms=first_token_ms,
            total_ms=int((time.monotonic() - started) * 1000),
        )

    # ------------------------------------------------------------------ #
    # Claude Agent SDK runtime
    # ------------------------------------------------------------------ #

    async def run_agent(self, prompt: str, *, system: str, tools) -> AsyncIterator[str]:
        """Run a genuine agent loop through the Claude Agent SDK.

        Our skills are exposed as in-process SDK tools, so Claude itself decides
        when to search transcripts, when to read a full episode, and when to
        write an essay — rather than us routing deterministically as we must on
        the local path.

        Yields assistant text as it is produced.
        """
        if self.runtime != "sdk":
            raise ProviderUnavailableError(
                "Agent SDK runtime is not active.",
                remediation="Set ANTHROPIC_AGENT_RUNTIME=sdk and ensure the "
                "`claude` CLI is installed in the image.",
            )

        from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query

        server = create_sdk_mcp_server(name="lenny-kb", version="1.0.0", tools=tools)

        options = ClaudeAgentOptions(
            system_prompt=system,
            mcp_servers={"lenny": server},
            allowed_tools=[f"mcp__lenny__{t.name}" for t in tools],
            # The agent's only capability is our knowledge base. It must not be
            # able to read the filesystem or run shell commands: retrieved
            # transcript text is untrusted input, and a prompt-injection payload
            # inside a transcript must have nothing dangerous to reach for.
            permission_mode="default",
            max_turns=6,
            model=self.model,
        )

        model_log.info("agent_sdk_run_start", model=self.model, tool_count=len(tools))

        try:
            async for message in query(prompt=prompt, options=options):
                for block in getattr(message, "content", []) or []:
                    if getattr(block, "type", None) == "text":
                        yield block.text
                    elif getattr(block, "type", None) == "tool_use":
                        model_log.info("agent_sdk_tool_use", tool=getattr(block, "name", "?"))
        except Exception as exc:
            model_log.error("agent_sdk_run_failed", error=str(exc))
            raise self._translate(exc) from exc

        model_log.info("agent_sdk_run_complete", model=self.model)

    # ------------------------------------------------------------------ #

    def _translate(self, exc: Exception) -> Exception:
        """Map vendor exceptions onto our error contract.

        Kept in one place so the API's error shape never depends on which SDK
        version happens to be installed.
        """
        name = type(exc).__name__

        if isinstance(exc, ProviderUnavailableError | ProviderTimeoutError):
            return exc
        if "Timeout" in name:
            return ProviderTimeoutError(
                f"Anthropic did not respond within {self.timeout}s.",
                detail={"model": self.model},
            )
        if "Authentication" in name or "PermissionDenied" in name:
            return ProviderUnavailableError(
                "Anthropic rejected the API key.",
                remediation="Check ANTHROPIC_API_KEY in .env.",
            )
        if "RateLimit" in name:
            return ProviderUnavailableError(
                "Anthropic rate limit reached.",
                remediation="Wait and retry, or switch to LLM_PROVIDER=ollama.",
            )
        return ProviderUnavailableError(
            f"Anthropic request failed ({name}): {exc}", detail={"model": self.model}
        )

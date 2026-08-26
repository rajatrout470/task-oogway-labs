"""Provider interface.

Every model provider implements this, and application code above it never names
a model or a vendor. That is what makes the brief's "switch models without
touching application code" requirement structurally true: the only place a model
id appears is config.py, and the only place a vendor SDK appears is one
subclass of BaseProvider.

The interface is deliberately narrow — `complete` and `stream` — because that is
the entire surface the agent layer needs. Tool-calling is *not* in the base
interface, since the two providers differ fundamentally in how capably they do
it (see providers/registry.py and agent/router.py for how that asymmetry is
handled honestly rather than pretended away).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class CompletionResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    token_usage: dict[str, int] = field(default_factory=dict)
    # True when this provider was used because the primary one was unavailable.
    # Surfaced to the UI so a silent downgrade is never invisible to the user.
    was_fallback: bool = False


class BaseProvider(ABC):
    """A text-generation backend."""

    name: str
    model: str

    @abstractmethod
    async def health(self) -> tuple[bool, str]:
        """(reachable, human-readable reason). Must never raise."""

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> CompletionResult:
        """Generate a full response."""

    @abstractmethod
    def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        """Yield response text incrementally.

        Streaming is not a nicety here. On the mandated local path a full
        1,250-word essay can take 30+ seconds; without streaming the UI is
        indistinguishable from a hang.
        """

    @property
    def supports_native_tools(self) -> bool:
        """Whether this provider does reliable model-driven tool selection.

        Drives routing strategy: providers that return False get deterministic
        skill routing instead of being asked to pick tools they choose badly.
        """
        return False

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "model": self.model,
            "supports_native_tools": self.supports_native_tools,
        }

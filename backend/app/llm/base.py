"""Provider-agnostic LLM interface.

Everything above this layer (voice NLU, ticket summaries) speaks only in
JSON Schema and plain strings, so swapping providers means adding one module
and changing LLM_PROVIDER -- no changes to business logic.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """What the rest of the application needs from a model provider."""

    name: str

    @property
    def is_configured(self) -> bool:
        """False when no API key is set, so callers can degrade gracefully."""
        ...

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any] | None:
        """Return JSON matching `schema`, or None if the call could not be completed.

        Returning None rather than raising is deliberate: callers treat an
        uninterpretable turn as a normal conversational failure (re-prompt,
        then escalate) rather than an exception to handle.
        """
        ...

    async def text(
        self,
        *,
        system: str,
        user: str,
        max_output_tokens: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> str | None:
        """Return plain text, or None if the call could not be completed."""
        ...

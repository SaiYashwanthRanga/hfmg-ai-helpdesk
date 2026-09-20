"""Provider-agnostic email interface.

Mirrors app/llm/base.py: everything above this layer speaks only in plain
strings, so swapping providers means adding one module and changing
EMAIL_PROVIDER -- no changes to notification logic.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmailProvider(Protocol):
    """What the rest of the application needs from an email provider."""

    name: str

    @property
    def is_configured(self) -> bool:
        """False when no API key is set, so callers can degrade gracefully."""
        ...

    async def send(
        self,
        *,
        to: str,
        from_email: str,
        subject: str,
        body: str,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> bool:
        """Send one email. Returns True on success, False otherwise.

        Never raises -- this is called from a FastAPI BackgroundTask, where an
        unhandled exception is only logged by the ASGI server and would
        otherwise be silently swallowed.
        """
        ...

"""SendGrid provider, via SendGrid's v3 Mail Send API directly (httpx).

Deliberately not the `sendgrid` SDK, which is synchronous -- a blocking HTTP
call here would stall the event loop for every concurrent request, including
an in-progress Twilio voice webhook (Twilio times those out around 15s). This
keeps the async discipline established for the LLM provider (app/llm/).
"""

import asyncio
import logging
import random

import httpx

from app.core.config import get_settings

logger = logging.getLogger("hfmg.notifications.sendgrid")

settings = get_settings()

SENDGRID_ENDPOINT = "https://api.sendgrid.com/v3/mail/send"

# Only these are worth retrying. A 400 (malformed request) or 401/403 (bad
# key) fails identically on every attempt.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class SendGridProvider:
    name = "sendgrid"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        return bool(settings.sendgrid_api_key)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

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
        if not self.is_configured:
            logger.warning("SENDGRID_API_KEY is not set; cannot send email")
            return False

        request_timeout = timeout if timeout is not None else settings.sendgrid_timeout_seconds
        attempts = (max_retries if max_retries is not None else settings.sendgrid_max_retries) + 1

        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        headers = {
            "Authorization": f"Bearer {settings.sendgrid_api_key}",
            "Content-Type": "application/json",
        }

        client = self._get_client()

        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(
                    SENDGRID_ENDPOINT, json=payload, headers=headers, timeout=request_timeout
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                if attempt >= attempts:
                    # Never log recipient, subject, or body -- only what kind
                    # of failure this was.
                    logger.warning(
                        "SendGrid request failed after %s attempt(s): %s", attempt, type(exc).__name__
                    )
                    return False
                await self._backoff(attempt)
                continue
            except Exception:
                logger.exception("SendGrid request failed with an unexpected error")
                return False

            if response.status_code == 202:
                message_id = response.headers.get("X-Message-Id", "unknown")
                logger.info("SendGrid accepted message %s", message_id)
                return True

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                logger.info(
                    "SendGrid returned %s on attempt %s/%s; retrying",
                    response.status_code,
                    attempt,
                    attempts,
                )
                await self._backoff(attempt)
                continue

            # Non-retryable, or retries exhausted. Log the status code only --
            # SendGrid error bodies can echo back request content, which would
            # put the ticket description or recipient into the log.
            logger.warning(
                "SendGrid send failed with status %s after %s attempt(s)", response.status_code, attempt
            )
            return False

        return False

    @staticmethod
    async def _backoff(attempt: int) -> None:
        delay = min(0.25 * (2 ** (attempt - 1)), 2.0) * (0.5 + random.random() / 2)
        await asyncio.sleep(delay)

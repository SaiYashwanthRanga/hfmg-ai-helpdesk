"""OpenAI provider built on the Responses API."""

import asyncio
import json
import logging
import random
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.core.config import get_settings

logger = logging.getLogger("hfmg.llm.openai")

settings = get_settings()

# Only these are worth retrying. A 400 (bad schema) or 401 (bad key) will fail
# identically on every attempt, and retrying them just burns the caller's
# latency budget while they wait on the phone.
RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


def _default_reasoning_effort(model: str) -> str:
    """Cap hidden reasoning for the original gpt-5 family (gpt-5, -mini, -nano).

    These models default to "medium" effort, and reasoning tokens count against
    max_output_tokens. Observed live with gpt-5-nano: a 2-3 sentence summary
    spent 1984 of 2000 output tokens on reasoning, returned status=incomplete
    with empty text, and took 16-20s. "minimal" returns in ~3s. Later families
    (gpt-5.1+) use different effort values, and non-reasoning models reject the
    parameter, so neither gets a default.
    """
    name = model.lower()
    if name.startswith("gpt-5") and not name.startswith("gpt-5."):
        return "minimal"
    return ""


def _describe(exc: BaseException) -> str:
    """Class name plus the real message, HTTP status and request id when present."""
    parts = [type(exc).__name__]
    status = getattr(exc, "status_code", None)
    if status is not None:
        parts.append(f"status={status}")
    request_id = getattr(exc, "request_id", None)
    if request_id:
        parts.append(f"request_id={request_id}")
    message = str(exc).strip()
    if message:
        parts.append(message[:500])
    return " | ".join(parts)


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    @property
    def is_configured(self) -> bool:
        return bool(settings.openai_api_key)

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "api_key": settings.openai_api_key,
                # Retries are handled here, not in the SDK, so the backoff
                # stays inside the per-call timeout budget.
                "max_retries": 0,
            }
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _request_kwargs(self) -> dict[str, Any]:
        """Optional model parameters, sent only when explicitly configured.

        Reasoning models reject `temperature` outright, so it is omitted unless
        someone sets it for a model that wants it. This is what lets the model
        be swapped via env var without code changes.
        """
        kwargs: dict[str, Any] = {}
        if settings.openai_temperature is not None:
            kwargs["temperature"] = settings.openai_temperature
        effort = settings.openai_reasoning_effort or _default_reasoning_effort(settings.openai_model)
        if effort:
            kwargs["reasoning"] = {"effort": effort}
        return kwargs

    async def _call(
        self,
        *,
        system: str,
        user: str,
        timeout: float,
        max_retries: int,
        extra: dict[str, Any],
        max_output_tokens: int,
    ) -> str | None:
        client = self._get_client()
        attempts = max_retries + 1

        for attempt in range(1, attempts + 1):
            try:
                response = await asyncio.wait_for(
                    client.responses.create(
                        model=settings.openai_model,
                        input=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        max_output_tokens=max_output_tokens,
                        **self._request_kwargs(),
                        **extra,
                    ),
                    timeout=timeout,
                )
                text = response.output_text
                if not text:
                    logger.warning(
                        "OpenAI returned no output text: status=%s incomplete_details=%s usage=%s",
                        getattr(response, "status", None),
                        getattr(response, "incomplete_details", None),
                        getattr(response, "usage", None),
                    )
                return text

            except RETRYABLE as exc:
                if attempt >= attempts:
                    logger.warning(
                        "OpenAI call failed after %s attempt(s): %s",
                        attempt,
                        _describe(exc),
                    )
                    return None
                # Exponential backoff with jitter, to avoid synchronised retries
                # when several calls hit a rate limit at once.
                delay = min(0.25 * (2 ** (attempt - 1)), 2.0) * (0.5 + random.random() / 2)
                logger.info(
                    "OpenAI call attempt %s/%s failed (%s); retrying in %.2fs",
                    attempt,
                    attempts,
                    _describe(exc),
                    delay,
                )
                await asyncio.sleep(delay)

            except asyncio.TimeoutError:
                logger.warning("OpenAI call exceeded %.1fs budget", timeout)
                return None

            except Exception as exc:
                # Non-retryable (bad request, auth, schema rejection).
                logger.exception(
                    "OpenAI call failed with a non-retryable error: %s", _describe(exc)
                )
                return None

        return None

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
        if not self.is_configured:
            logger.warning("OPENAI_API_KEY is not set; cannot call the model")
            return None

        raw = await self._call(
            system=system,
            user=user,
            timeout=timeout if timeout is not None else settings.openai_timeout_seconds,
            max_retries=max_retries if max_retries is not None else settings.openai_max_retries,
            max_output_tokens=settings.openai_max_output_tokens,
            extra={
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                }
            },
        )
        if raw is None:
            return None

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Model returned unparseable JSON for schema %s", schema_name)
            return None

        if not isinstance(parsed, dict):
            logger.warning("Model returned non-object JSON for schema %s", schema_name)
            return None
        return parsed

    async def text(
        self,
        *,
        system: str,
        user: str,
        max_output_tokens: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> str | None:
        if not self.is_configured:
            logger.warning("OPENAI_API_KEY is not set; cannot call the model")
            return None

        raw = await self._call(
            system=system,
            user=user,
            timeout=timeout if timeout is not None else settings.openai_timeout_seconds,
            max_retries=max_retries if max_retries is not None else settings.openai_max_retries,
            max_output_tokens=max_output_tokens or settings.openai_max_output_tokens,
            extra={},
        )
        return raw.strip() if raw else None

"""OpenAI provider built on the Responses API."""

import asyncio
import json
import logging
import random
import time
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.core import trace
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


def _is_reasoning_model(model: str) -> bool:
    """Reasoning families reject `temperature`."""
    name = model.lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


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

    async def warm(self) -> None:
        """Open the pooled HTTPS connection before a caller is waiting on it.

        Measured from a developer machine: the first request of a process
        paid ~0.7 s of connection setup (TLS) on top of the ~0.6 s round trip
        (docs/reviews/VOICE_LATENCY_ACCURACY_REPORT.md). A metadata request
        costs no tokens. Best-effort; never raises.
        """
        if not self.is_configured:
            return
        try:
            await asyncio.wait_for(self._get_client().models.retrieve(settings.voice_nlu_model or settings.openai_model), timeout=5)
        except Exception:
            pass

    def _request_kwargs(self, model: str) -> dict[str, Any]:
        """Optional model parameters, sent only when explicitly configured.

        Reasoning models reject `temperature` outright, so it is omitted unless
        someone sets it for a model that wants it. This is what lets the model
        be swapped via env var without code changes.

        OPENAI_TEMPERATURE / OPENAI_REASONING_EFFORT describe OPENAI_MODEL. A
        per-call override model (e.g. VOICE_NLU_MODEL) gets its own defaults:
        the family's reasoning cap if it is a reasoning model, otherwise
        temperature 0 -- extraction should be deterministic.
        """
        kwargs: dict[str, Any] = {}
        if model == settings.openai_model:
            if settings.openai_temperature is not None:
                kwargs["temperature"] = settings.openai_temperature
            effort = settings.openai_reasoning_effort or _default_reasoning_effort(model)
        else:
            effort = _default_reasoning_effort(model)
            if not _is_reasoning_model(model):
                kwargs["temperature"] = 0
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
        outcome: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> str | None:
        """`outcome`, when given, receives `attempts` and `error` for tracing."""
        client = self._get_client()
        model = model or settings.openai_model
        attempts = max_retries + 1
        outcome = outcome if outcome is not None else {}

        for attempt in range(1, attempts + 1):
            outcome["attempts"] = attempt
            try:
                response = await asyncio.wait_for(
                    client.responses.create(
                        model=model,
                        input=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        max_output_tokens=max_output_tokens,
                        **self._request_kwargs(model),
                        **extra,
                    ),
                    timeout=timeout,
                )
                text = response.output_text
                if not text:
                    outcome["error"] = f"empty output (status={getattr(response, 'status', None)})"
                    logger.warning(
                        "OpenAI returned no output text: status=%s incomplete_details=%s usage=%s",
                        getattr(response, "status", None),
                        getattr(response, "incomplete_details", None),
                        getattr(response, "usage", None),
                    )
                return text

            except RETRYABLE as exc:
                outcome["error"] = _describe(exc)
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
                outcome["error"] = f"timeout after {timeout:.1f}s"
                logger.warning("OpenAI call exceeded %.1fs budget", timeout)
                return None

            except Exception as exc:
                outcome["error"] = _describe(exc)
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
        model: str | None = None,
    ) -> dict[str, Any] | None:
        started = time.perf_counter()
        outcome: dict[str, Any] = {"attempts": 0, "error": None}
        parsed: Any = None
        try:
            if not self.is_configured:
                outcome["error"] = "OPENAI_API_KEY is not set"
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
                outcome=outcome,
                model=model,
            )
            if raw is None:
                return None

            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                outcome["error"] = "unparseable JSON"
                logger.warning("Model returned unparseable JSON for schema %s", schema_name)
                return None

            if not isinstance(parsed, dict):
                outcome["error"] = "non-object JSON"
                logger.warning("Model returned non-object JSON for schema %s", schema_name)
                parsed = None
                return None
            return parsed
        finally:
            trace.record_llm_call(
                kind="structured",
                schema_name=schema_name,
                started=started,
                system=system,
                user=user,
                output=parsed,
                error=outcome["error"] if parsed is None else None,
                attempts=outcome["attempts"],
            )

    async def text(
        self,
        *,
        system: str,
        user: str,
        max_output_tokens: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> str | None:
        started = time.perf_counter()
        outcome: dict[str, Any] = {"attempts": 0, "error": None}
        result: str | None = None
        try:
            if not self.is_configured:
                outcome["error"] = "OPENAI_API_KEY is not set"
                logger.warning("OPENAI_API_KEY is not set; cannot call the model")
                return None

            raw = await self._call(
                system=system,
                user=user,
                timeout=timeout if timeout is not None else settings.openai_timeout_seconds,
                max_retries=max_retries if max_retries is not None else settings.openai_max_retries,
                max_output_tokens=max_output_tokens or settings.openai_max_output_tokens,
                extra={},
                outcome=outcome,
            )
            result = raw.strip() if raw else None
            return result
        finally:
            trace.record_llm_call(
                kind="text",
                schema_name=None,
                started=started,
                system=system,
                user=user,
                output=result,
                error=outcome["error"] if result is None else None,
                attempts=outcome["attempts"],
            )

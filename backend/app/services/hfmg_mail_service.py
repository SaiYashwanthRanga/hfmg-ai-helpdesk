"""Client for the HFMG internal mail API (a Microsoft Graph wrapper).

Base URL comes from ORG_BASE. Every endpoint takes multipart/form-data:
list fields (To, Cc, ToList, CcList) are sent as one part per address, and an
`Attachments` part is always present, empty when there are no files.

Every function returns True on success and False on any failure. None of them
raise: they are called from FastAPI BackgroundTasks, where an exception is only
logged by the ASGI server. Logs carry the action, HTTP status and error class,
never recipients, subjects or bodies.

Retries: only when the connection itself failed (the request never reached the
API). Read timeouts and HTTP error statuses are NOT retried, because the API
may already have handed the message to Microsoft Graph and a retry would send
a duplicate email.

Bodies are HTML (BodyHtml / ReplyBodyHtml / Body). Callers must escape any
untrusted text before passing it in; see HfmgInternalMailProvider.
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Sequence

import httpx

from app.core.config import get_settings

logger = logging.getLogger("hfmg.mail")

settings = get_settings()

SEND_PATH = "/api/Values/mail/send"
REMINDER_PATH = "/api/Values/mail/SentAsRemainder"
REPLY_PATH = "/api/Values/mail/reply"
FORWARD_PATH = "/api/Values/forwardmail"

# (filename, content, content_type)
Attachment = tuple[str, bytes, str]

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


def is_configured() -> bool:
    return bool(settings.org_base.strip())


def _url(path: str) -> str:
    return settings.org_base.strip().rstrip("/") + path


def _as_list(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [item.strip() for item in value if item and item.strip()]


def _quote(value: str) -> str:
    return value.replace("\r", "").replace("\n", "").replace('"', "%22")


def _encode_multipart(
    fields: dict[str, str | list[str] | None], attachments: Sequence[Attachment] | None
) -> tuple[bytes, str]:
    """Build the multipart body by hand; returns (body, content_type).

    Hand-built because httpx drops an empty `filename=""`, which would turn the
    required empty `Attachments` part into a plain form field. ASP.NET binds
    IFormFile lists from file parts, so the empty part must be sent the way a
    browser sends an empty file input: a file part with `filename=""` and no
    content. A list value becomes one part per item.
    """
    boundary = uuid.uuid4().hex
    chunks: list[bytes] = []

    for name, value in fields.items():
        if value in (None, [], ""):
            continue
        for item in value if isinstance(value, list) else [value]:
            chunks.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            )
            chunks.append(item.encode("utf-8"))
            chunks.append(b"\r\n")

    files = list(attachments) if attachments else [("", b"", "application/octet-stream")]
    for filename, content, content_type in files:
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="Attachments"; '
                f'filename="{_quote(filename)}"\r\nContent-Type: {content_type}\r\n\r\n'
            ).encode()
        )
        chunks.append(content)
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


async def _backoff(attempt: int) -> None:
    delay = min(0.25 * (2 ** (attempt - 1)), 2.0) * (0.5 + random.random() / 2)
    await asyncio.sleep(delay)


async def _post(
    action: str,
    path: str,
    fields: dict[str, str | list[str] | None],
    attachments: Sequence[Attachment] | None,
    timeout: float | None,
    max_retries: int | None,
) -> bool:
    if not is_configured():
        logger.warning("HFMG mail API: ORG_BASE is not set; cannot %s", action)
        return False

    request_timeout = timeout if timeout is not None else settings.hfmg_mail_timeout_seconds
    attempts = (max_retries if max_retries is not None else settings.hfmg_mail_max_retries) + 1
    body, content_type = _encode_multipart(fields, attachments)

    for attempt in range(1, attempts + 1):
        try:
            response = await _get_client().post(
                _url(path),
                content=body,
                headers={"Content-Type": content_type},
                timeout=request_timeout,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            if attempt >= attempts:
                logger.error(
                    "HFMG mail API unreachable; %s failed after %s attempt(s): %s",
                    action,
                    attempt,
                    type(exc).__name__,
                )
                return False
            await _backoff(attempt)
            continue
        except httpx.TimeoutException as exc:
            logger.error(
                "HFMG mail API timed out (%.1fs) during %s; not retrying, the message "
                "may already have been sent: %s",
                request_timeout,
                action,
                type(exc).__name__,
            )
            return False
        except Exception:
            logger.exception("HFMG mail API: unexpected error during %s", action)
            return False

        if response.is_success:
            logger.info("HFMG mail API: %s succeeded (HTTP %s)", action, response.status_code)
            return True

        logger.error("HFMG mail API: %s failed with HTTP %s", action, response.status_code)
        return False

    return False


async def send_email(
    *,
    to: str | Sequence[str],
    subject: str,
    body_html: str,
    cc: Sequence[str] | None = None,
    attachments: Sequence[Attachment] | None = None,
    from_email: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> bool:
    """POST /api/Values/mail/send."""
    recipients = _as_list(to)
    if not recipients:
        logger.warning("HFMG mail API: send_email called with no recipients; skipping")
        return False
    # The API rejects a send without FromEmail, and reports it misleadingly as
    # "At least one recipient is required" (HTTP 400), so always supply one.
    sender = from_email or settings.default_from_email or settings.email_from
    if not sender:
        logger.warning("HFMG mail API: no sender (FromEmail, DEFAULT_FROM_EMAIL, EMAIL_FROM); skipping")
        return False
    return await _post(
        "send_email",
        SEND_PATH,
        {
            "To": recipients,
            "Cc": _as_list(cc),
            "Subject": subject,
            "BodyHtml": body_html,
            "FromEmail": sender,
        },
        attachments,
        timeout,
        max_retries,
    )


async def send_reminder(
    *,
    to: str | Sequence[str],
    subject: str,
    body_html: str,
    cc: Sequence[str] | None = None,
    attachments: Sequence[Attachment] | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> bool:
    """POST /api/Values/mail/SentAsRemainder (the API's spelling). Takes no FromEmail."""
    recipients = _as_list(to)
    if not recipients:
        logger.warning("HFMG mail API: send_reminder called with no recipients; skipping")
        return False
    return await _post(
        "send_reminder",
        REMINDER_PATH,
        {"To": recipients, "Cc": _as_list(cc), "Subject": subject, "BodyHtml": body_html},
        attachments,
        timeout,
        max_retries,
    )


async def reply_email(
    *,
    message_item_id: str,
    reply_body_html: str,
    target_user_email: str,
    to: Sequence[str] | None = None,
    cc: Sequence[str] | None = None,
    is_reply_all: bool = False,
    attachments: Sequence[Attachment] | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> bool:
    """POST /api/Values/mail/reply. `message_item_id` is the Graph id of the message."""
    if not message_item_id or not target_user_email:
        logger.warning("HFMG mail API: reply_email needs message_item_id and target_user_email")
        return False
    return await _post(
        "reply_email",
        REPLY_PATH,
        {
            "MessageItemId": message_item_id,
            "ReplyBodyHtml": reply_body_html,
            "To": _as_list(to),
            "Cc": _as_list(cc),
            "TargetUserEmail": target_user_email,
            "IsReplyAll": "true" if is_reply_all else "false",
        },
        attachments,
        timeout,
        max_retries,
    )


async def forward_email(
    *,
    message_item_id: str,
    to_list: str | Sequence[str],
    body: str,
    target_user_email: str,
    subject: str | None = None,
    cc_list: Sequence[str] | None = None,
    attachments: Sequence[Attachment] | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> bool:
    """POST /api/Values/forwardmail. Note the API names the last field `targetUserEmail`."""
    recipients = _as_list(to_list)
    if not message_item_id or not target_user_email or not recipients:
        logger.warning(
            "HFMG mail API: forward_email needs message_item_id, target_user_email and recipients"
        )
        return False
    return await _post(
        "forward_email",
        FORWARD_PATH,
        {
            "Subject": subject,
            "Body": body,
            "MessageItemId": message_item_id,
            "ToList": recipients,
            "CcList": _as_list(cc_list),
            "targetUserEmail": target_user_email,
        },
        attachments,
        timeout,
        max_retries,
    )


async def check_health(timeout: float = 4.0) -> bool:
    """True if the API answers HTTP at all (any status below 500).

    Sends nothing and needs no credentials: the API declares no auth scheme
    and has no dedicated health endpoint, so this proves the host is up and
    the service is listening, not that its mailbox login is still valid.
    """
    if not is_configured():
        logger.warning("HFMG mail API health check: ORG_BASE is not set")
        return False
    try:
        response = await _get_client().get(_url("/"), timeout=timeout)
    except Exception as exc:
        # Health results are cached 30s, so this logs at most about twice a minute.
        logger.warning("HFMG mail API health check failed: %s", type(exc).__name__)
        return False
    if response.status_code >= 500:
        logger.warning("HFMG mail API health check: server error HTTP %s", response.status_code)
        return False
    return True

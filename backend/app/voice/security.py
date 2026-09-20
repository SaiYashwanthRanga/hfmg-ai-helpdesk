"""Twilio webhook authentication.

These endpoints are publicly reachable and create records, so signature
validation is the only thing standing between Twilio and forged tickets.
"""

import logging

from fastapi import HTTPException, Request, status
from twilio.request_validator import RequestValidator

from app.core.config import get_settings

logger = logging.getLogger("hfmg.voice.security")

settings = get_settings()


def _expected_url(request: Request) -> str:
    """The URL Twilio signed, which may differ from the URL we received.

    Behind a tunnel or proxy the app sees an internal host; Twilio signed the
    public one, so the signature only matches if we reconstruct it.
    """
    if settings.twilio_public_base_url:
        base = settings.twilio_public_base_url.rstrip("/")
        return f"{base}{request.url.path}"
    return str(request.url)


async def verify_twilio_signature(request: Request) -> None:
    if not settings.twilio_validate_signature:
        logger.warning("Twilio signature validation is DISABLED -- local testing only")
        return

    if not settings.twilio_auth_token:
        logger.error("TWILIO_AUTH_TOKEN is not set; rejecting webhook")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook not configured")

    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    validator = RequestValidator(settings.twilio_auth_token)
    if not validator.validate(_expected_url(request), params, signature):
        logger.warning("Rejected Twilio webhook with invalid signature")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

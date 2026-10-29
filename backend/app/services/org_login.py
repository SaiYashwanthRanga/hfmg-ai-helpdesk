"""Checks a dashboard sign-in against the HFMG org server's POST /login (AUTH_LOGIN_URL).

It answers 200 with the user's data (including mail and display name), 401
{"message": "Invalid credentials"} for a wrong email or password and 400 for a malformed
request. A login proxy in front of it may also answer 503/504 when the org server is down
or slow. Neither issues a token: the session is ours (app/core/auth.py). The password is
forwarded once and never logged or stored.
"""

import logging
from typing import Any

import httpx

from app.core.auth import SessionUser
from app.core.config import get_settings

logger = logging.getLogger("hfmg.auth")

settings = get_settings()

_EMAIL_KEYS = ("mail", "email", "userPrincipalName")
_NAME_KEYS = ("displayName", "display_name", "name")


class InvalidCredentials(Exception):
    """The org server rejected the email or password."""


class LoginUnavailable(Exception):
    """The sign-in could not be checked; `status_code` is what the dashboard is answered."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_UNREACHABLE = "The sign-in server can't be reached right now. Try again shortly."
_TIMED_OUT = "The sign-in server did not answer in time. Try again."


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.auth_login_timeout_seconds)


def _first_text(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _user_from(data: Any, submitted_email: str) -> SessionUser:
    """Pick the mail and display name out of the org server's answer, wrapped in "user" or not."""
    if isinstance(data, dict) and isinstance(data.get("user"), dict):
        data = data["user"]
    if not isinstance(data, dict):
        data = {}
    email = _first_text(data, _EMAIL_KEYS) or submitted_email
    return SessionUser(email=email, name=_first_text(data, _NAME_KEYS) or email)


async def verify_credentials(email: str, password: str) -> SessionUser:
    try:
        async with _client() as client:
            response = await client.post(settings.auth_login_url, json={"email": email, "password": password})
    except httpx.TimeoutException:
        logger.warning("Org login API timed out for %s", email)
        raise LoginUnavailable(504, _TIMED_OUT) from None
    except httpx.HTTPError as exc:
        logger.warning("Org login API unreachable (%s)", type(exc).__name__)
        raise LoginUnavailable(503, _UNREACHABLE) from None

    if response.status_code in (400, 401, 403, 422):
        logger.info("Sign-in rejected for %s", email)
        raise InvalidCredentials()
    if response.status_code == 504:
        raise LoginUnavailable(504, _TIMED_OUT)
    if response.status_code != 200:
        logger.warning("Org login API answered %s", response.status_code)
        raise LoginUnavailable(503, _UNREACHABLE)
    try:
        data = response.json()
    except ValueError:
        logger.warning("Org login API answered 200 with a body that is not JSON")
        raise LoginUnavailable(503, _UNREACHABLE) from None
    user = _user_from(data, email)
    logger.info("Signed in: %s", user.email)
    return user

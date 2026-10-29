"""Dashboard sessions: a signed cookie issued once the org login API accepts a sign-in.

The cookie holds the user's email, display name and expiry, signed with SESSION_SECRET
(HMAC-SHA256). Nothing is stored server-side, so signing out clears the cookie in that
browser only; a copied cookie stays valid until it expires (SESSION_TTL_HOURS).
"""

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

settings = get_settings()

SESSION_COOKIE = "hfmg_session"
MIN_SECRET_LENGTH = 32


@dataclass(frozen=True)
class SessionUser:
    email: str
    name: str


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _secret() -> bytes | None:
    secret = settings.session_secret.strip()
    return secret.encode() if len(secret) >= MIN_SECRET_LENGTH else None


def login_is_configured() -> bool:
    return bool(settings.auth_login_url.strip()) and _secret() is not None


def _signature(secret: bytes, payload: str) -> str:
    return _b64encode(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())


def session_max_age_seconds() -> int:
    return int(settings.session_ttl_hours * 3600)


def sign_session(user: SessionUser, *, now: float | None = None) -> str:
    secret = _secret()
    if secret is None:
        raise RuntimeError("SESSION_SECRET is not set (or shorter than 32 characters)")
    expires = int((time.time() if now is None else now) + session_max_age_seconds())
    body = json.dumps({"email": user.email, "name": user.name, "exp": expires}, separators=(",", ":"))
    payload = _b64encode(body.encode())
    return f"{payload}.{_signature(secret, payload)}"


def read_session(token: str | None, *, now: float | None = None) -> SessionUser | None:
    """The signed-in user, or None for a missing, tampered, malformed or expired cookie."""
    secret = _secret()
    if secret is None or not token:
        return None
    payload, _, signature = token.partition(".")
    try:
        if not hmac.compare_digest(signature.encode(), _signature(secret, payload).encode()):
            return None
        data = json.loads(_b64decode(payload))
        email, name, expires = str(data["email"]), str(data.get("name") or ""), int(data["exp"])
    except (UnicodeEncodeError, binascii.Error, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not email or expires <= (time.time() if now is None else now):
        return None
    return SessionUser(email=email, name=name)


async def require_login(request: Request) -> SessionUser | None:
    """Route dependency: 401 without a valid session. None when AUTH_REQUIRED=false."""
    if not settings.auth_required:
        return None
    user = read_session(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue")
    return user

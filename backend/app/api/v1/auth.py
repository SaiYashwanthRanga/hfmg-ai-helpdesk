"""Dashboard sign-in, sign-out and "who am I". These routes never require a session themselves."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.core.auth import (
    SESSION_COOKIE,
    SessionUser,
    login_is_configured,
    require_login,
    session_max_age_seconds,
    sign_session,
)
from app.core.config import get_settings
from app.services import org_login

logger = logging.getLogger("hfmg.auth")

settings = get_settings()

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class SessionRead(BaseModel):
    email: str
    name: str
    # False when AUTH_REQUIRED=false: the dashboard shows no sign-in and no "Sign out".
    login_required: bool


@router.post("/login", response_model=SessionRead)
async def login(body: LoginRequest, response: Response) -> SessionRead:
    if not login_is_configured():
        logger.error("Sign-in attempted but AUTH_LOGIN_URL or SESSION_SECRET (32+ characters) is not set")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sign-in is not configured on this server.")
    try:
        user = await org_login.verify_credentials(body.email.strip(), body.password)
    except org_login.InvalidCredentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password") from None
    except org_login.LoginUnavailable as exc:
        raise HTTPException(exc.status_code, detail=exc.detail) from None

    response.set_cookie(
        SESSION_COOKIE,
        sign_session(user),
        max_age=session_max_age_seconds(),
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    return SessionRead(email=user.email, name=user.name, login_required=True)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=settings.session_cookie_secure, samesite="lax")
    return response


@router.get("/me", response_model=SessionRead)
async def me(user: SessionUser | None = Depends(require_login)) -> SessionRead:
    if user is None:
        return SessionRead(email="", name="", login_required=False)
    return SessionRead(email=user.email, name=user.name, login_required=True)

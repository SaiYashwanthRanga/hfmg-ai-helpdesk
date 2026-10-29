"""Dashboard login: the org login API checks the password, the backend issues and enforces the session."""

import httpx
import pytest

from app.core import auth as auth_core
from app.core.config import Settings, get_settings
from app.services import org_login

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

LOGIN_URL = "http://org-login.test/api/login"


@pytest.fixture
def login_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "auth_login_url", LOGIN_URL)
    monkeypatch.setattr(settings, "session_secret", "x" * 40)
    monkeypatch.setattr(settings, "session_ttl_hours", 12.0)


def _org_answers(monkeypatch, handler):
    """Route the backend's call to the org login API to `handler`; returns the requests it received."""
    received: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return handler(request)

    monkeypatch.setattr(org_login, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(record)))
    return received


def _accepts(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"mail": "jordan.sample@example.org", "displayName": "Jordan Sample"})


async def _sign_in(client, monkeypatch):
    _org_answers(monkeypatch, _accepts)
    return await client.post("/api/v1/auth/login", json={"email": "jordan.sample@example.org", "password": "pw"})


async def test_dashboard_routes_need_a_session(client, login_on):
    for path in ("/api/v1/categories", "/api/v1/tickets", "/api/v1/settings/status", "/api/v1/health/dependencies"):
        response = await client.get(path)
        assert response.status_code == 401, path
        assert response.json() == {"detail": "Sign in to continue"}


async def test_sign_in_sets_a_session_that_opens_the_api(client, login_on, monkeypatch):
    response = await _sign_in(client, monkeypatch)

    assert response.status_code == 200
    assert response.json() == {"email": "jordan.sample@example.org", "name": "Jordan Sample", "login_required": True}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=43200" in cookie
    assert (await client.get("/api/v1/categories")).status_code == 200
    me = await client.get("/api/v1/auth/me")
    assert me.json()["name"] == "Jordan Sample"


async def test_the_org_api_receives_the_credentials_as_given(client, login_on, monkeypatch):
    received = _org_answers(monkeypatch, _accepts)
    await client.post("/api/v1/auth/login", json={"email": "  jordan.sample@example.org ", "password": " p w "})

    assert str(received[0].url) == LOGIN_URL
    assert received[0].read() == b'{"email":"jordan.sample@example.org","password":" p w "}'


async def test_wrong_password_is_401_and_sets_no_cookie(client, login_on, monkeypatch):
    _org_answers(monkeypatch, lambda r: httpx.Response(401, json={"detail": "Invalid email or password"}))
    response = await client.post("/api/v1/auth/login", json={"email": "a@example.org", "password": "nope"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    ("org_status", "expected"),
    [(503, 503), (504, 504), (500, 503), (302, 503)],
)
async def test_org_server_trouble_is_reported_not_treated_as_a_bad_password(client, login_on, monkeypatch, org_status, expected):
    _org_answers(monkeypatch, lambda r: httpx.Response(org_status, json={"detail": "x"}))
    response = await client.post("/api/v1/auth/login", json={"email": "a@example.org", "password": "pw"})

    assert response.status_code == expected
    assert "set-cookie" not in response.headers


async def test_unreachable_and_timed_out_org_server(client, login_on, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    _org_answers(monkeypatch, refuse)
    assert (await client.post("/api/v1/auth/login", json={"email": "a@example.org", "password": "pw"})).status_code == 503
    _org_answers(monkeypatch, slow)
    assert (await client.post("/api/v1/auth/login", json={"email": "a@example.org", "password": "pw"})).status_code == 504


async def test_unconfigured_login_stays_closed(client, login_on, monkeypatch):
    monkeypatch.setattr(settings, "session_secret", "too-short")
    received = _org_answers(monkeypatch, _accepts)

    response = await client.post("/api/v1/auth/login", json={"email": "a@example.org", "password": "pw"})

    assert response.status_code == 503
    assert received == []
    assert (await client.get("/api/v1/categories")).status_code == 401


async def test_sign_out_clears_the_cookie(client, login_on, monkeypatch):
    await _sign_in(client, monkeypatch)
    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert 'hfmg_session=""' in response.headers["set-cookie"]
    assert (await client.get("/api/v1/categories")).status_code == 401


async def test_tampered_or_expired_cookie_is_refused(client, login_on):
    user = auth_core.SessionUser(email="a@example.org", name="A")
    token = auth_core.sign_session(user)
    payload, _, signature = token.partition(".")
    forged = auth_core._b64encode(b'{"email":"b@example.org","name":"B","exp":9999999999}')

    assert auth_core.read_session(token) == user
    assert auth_core.read_session(f"{forged}.{signature}") is None
    assert auth_core.read_session(f"{payload}.{signature[:-2]}") is None
    assert auth_core.read_session("garbage") is None
    assert auth_core.read_session(f"{payload}.é") is None
    assert auth_core.read_session(auth_core.sign_session(user, now=0)) is None

    client_cookie = {auth_core.SESSION_COOKIE: f"{forged}.{signature}"}
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=client_cookie) as other:
        assert (await other.get("/api/v1/categories")).status_code == 401


async def test_a_session_signed_with_another_secret_is_refused(login_on, monkeypatch):
    token = auth_core.sign_session(auth_core.SessionUser(email="a@example.org", name="A"))
    monkeypatch.setattr(settings, "session_secret", "y" * 40)

    assert auth_core.read_session(token) is None


async def test_display_name_and_mail_from_the_org_answer():
    assert org_login._user_from({"user": {"mail": "m@example.org", "displayName": "M"}}, "typed@example.org") == (
        auth_core.SessionUser(email="m@example.org", name="M")
    )
    # Fields missing: fall back to what was typed rather than refuse a sign-in the org server accepted.
    assert org_login._user_from(["unexpected"], "typed@example.org") == (
        auth_core.SessionUser(email="typed@example.org", name="typed@example.org")
    )


async def test_health_probes_and_sip_gateway_do_not_use_the_dashboard_session(client, login_on, monkeypatch):
    assert (await client.get("/api/v1/health")).status_code == 200
    monkeypatch.setattr(settings, "voice_sip_gateway_token", "gateway-secret")
    response = await client.post("/api/v1/voice/sip/start", json={"call_id": "probe"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid gateway token"}


async def test_me_without_login_required(client):
    response = await client.get("/api/v1/auth/me")
    assert response.json() == {"email": "", "name": "", "login_required": False}


async def test_login_cannot_be_turned_off_in_production():
    with pytest.raises(ValueError, match="AUTH_REQUIRED=false"):
        Settings(_env_file=None, environment="production", auth_required=False)
    Settings(_env_file=None, environment="production", auth_required=True)

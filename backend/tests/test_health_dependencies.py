"""GET /health/dependencies -- the OpenAI/Twilio/Database/Email status strip.

Follows the same monkeypatch-the-provider pattern as test_summarizer.py's
FakeProvider so these tests never need real network access or credentials.
"""

from datetime import datetime, timezone

import pytest

from app.services import dependency_health

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def _reset_cache():
    """The dependency-health cache is module-level state -- clear it before
    and after every test so results don't leak between tests."""
    dependency_health.reset_cache()
    yield
    dependency_health.reset_cache()


async def test_unconfigured_providers_report_down(client, monkeypatch):
    """No API keys set -> down, without attempting any network call."""
    settings = dependency_health.settings
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "sendgrid_api_key", "")

    response = await client.get("/api/v1/health/dependencies")
    assert response.status_code == 200
    body = response.json()

    assert body["openai"]["status"] == "down"
    assert body["twilio"]["status"] == "down"
    assert body["email"]["status"] == "down"
    assert body["database"]["status"] == "operational"  # real DB, reachable in tests
    for dependency in body.values():
        assert dependency["checked_at"]  # every dependency reports a timestamp


async def test_configured_and_reachable_reports_operational(client, monkeypatch):
    async def fake_check():
        return dependency_health.DependencyCheck(status="operational", checked_at=datetime.now(timezone.utc))

    monkeypatch.setattr(dependency_health, "check_openai", fake_check)
    monkeypatch.setattr(dependency_health, "check_twilio", fake_check)
    monkeypatch.setattr(dependency_health, "check_email", fake_check)

    response = await client.get("/api/v1/health/dependencies")
    body = response.json()
    assert body["openai"]["status"] == "operational"
    assert body["twilio"]["status"] == "operational"
    assert body["email"]["status"] == "operational"


async def test_unreachable_provider_is_caught_and_reported_down(monkeypatch):
    """check_openai catches network errors internally and reports "down"
    rather than letting the exception bubble up into a 500."""
    import httpx

    def raise_on_construct(**_kwargs):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr(dependency_health.settings, "openai_api_key", "sk-test-key-not-real")
    monkeypatch.setattr(httpx, "AsyncClient", raise_on_construct)

    result = await dependency_health.check_openai()
    assert result.status == "down"


async def test_result_is_cached_within_ttl(client, monkeypatch):
    call_count = 0

    async def counting_check():
        nonlocal call_count
        call_count += 1
        return dependency_health.DependencyCheck(status="operational", checked_at=datetime.now(timezone.utc))

    monkeypatch.setattr(dependency_health, "check_openai", counting_check)

    first = await client.get("/api/v1/health/dependencies")
    second = await client.get("/api/v1/health/dependencies")

    assert first.json()["openai"]["status"] == "operational"
    assert second.json()["openai"]["status"] == "operational"
    assert call_count == 1  # second request served from cache, not a fresh check

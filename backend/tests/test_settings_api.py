import pytest

from app.services import dependency_health

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def _reset_cache():
    dependency_health.reset_cache()
    yield
    dependency_health.reset_cache()


async def test_settings_status_masks_configured_secrets(client, monkeypatch):
    settings = dependency_health.settings
    monkeypatch.setattr(settings, "openai_api_key", "sk-abcdefghijklmnop")
    monkeypatch.setattr(settings, "twilio_auth_token", "twilio_secret_token_value")
    monkeypatch.setattr(settings, "sendgrid_api_key", "SG.abcdefghijklmno.pqrstuvwxyz")

    response = await client.get("/api/v1/settings/status")
    assert response.status_code == 200
    body = response.json()

    for provider in ("openai", "twilio", "email"):
        assert body[provider]["configured"] is True
        masked = body[provider]["masked_key"]
        assert masked is not None
        assert "..." in masked
        # The raw secret must never appear anywhere in the response body.
        raw_values = {
            "openai": "sk-abcdefghijklmnop",
            "twilio": "twilio_secret_token_value",
            "email": "SG.abcdefghijklmno.pqrstuvwxyz",
        }
        assert raw_values[provider] not in response.text


async def test_settings_status_reports_not_configured(client, monkeypatch):
    settings = dependency_health.settings
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "sendgrid_api_key", "")

    response = await client.get("/api/v1/settings/status")
    body = response.json()

    for provider in ("openai", "twilio", "email"):
        assert body[provider]["configured"] is False
        assert body[provider]["masked_key"] is None
        assert body[provider]["status"] == "down"


async def test_settings_status_includes_database_and_environment(client):
    response = await client.get("/api/v1/settings/status")
    body = response.json()

    assert body["database"]["configured"] is True
    assert body["database"]["status"] == "operational"
    assert body["database"]["masked_key"] is None  # nothing to mask -- no connection string exposed

    assert "environment" in body["environment"]
    assert isinstance(body["environment"]["enable_ai_summary"], bool)


async def test_settings_status_never_exposes_database_url(client):
    response = await client.get("/api/v1/settings/status")
    assert "hfmg_dev_local" not in response.text  # the DB password in DATABASE_URL

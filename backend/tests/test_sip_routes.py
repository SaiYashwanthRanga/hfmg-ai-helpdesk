"""SIP gateway endpoint tests: bearer auth and the JSON turn contract."""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import VoiceCallSession, VoiceCallState
from app.voice import nlu, scripts

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

START_URL = "/api/v1/voice/sip/start"
TURN_URL = "/api/v1/voice/sip/turn"
STATUS_URL = "/api/v1/voice/sip/status"
TOKEN = "test-gateway-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _gateway_token(monkeypatch):
    monkeypatch.setattr(settings, "voice_sip_gateway_token", TOKEN)


async def test_start_greets_and_creates_session(client, db_session):
    response = await client.post(
        START_URL,
        json={"call_id": "sip-1", "from_number": "+18455550142", "to_number": "+18455559999"},
        headers=AUTH,
    )

    assert response.status_code == 200
    body = response.json()
    assert "Horizon Family Medical Group IT Help Desk" in " ".join(body["lines"])
    assert body["expect_reply"] is True

    session = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == "sip-1"))
    ).scalar_one()
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION
    assert session.collected["phone_number"] == "+18455550142"


async def test_start_is_idempotent(client, db_session):
    payload = {"call_id": "sip-dup", "from_number": "+18455550142"}
    await client.post(START_URL, json=payload, headers=AUTH)
    await client.post(START_URL, json=payload, headers=AUTH)

    sessions = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == "sip-dup"))
    ).scalars().all()
    assert len(sessions) == 1


async def test_turn_advances_conversation(client, monkeypatch):
    await client.post(START_URL, json={"call_id": "sip-turn", "from_number": "+18455550142"}, headers=AUTH)

    async def fake_description(utterance):
        return nlu.TurnResult(
            value="Printer is jammed",
            confidence="high",
            unable_to_determine=False,
            category="Other",
            extras={"short_issue": "printer"},
        )

    monkeypatch.setattr(nlu, "interpret_description", fake_description)

    response = await client.post(
        TURN_URL, json={"call_id": "sip-turn", "utterance": "the printer is jammed"}, headers=AUTH
    )

    assert response.status_code == 200
    body = response.json()
    assert any(option in " ".join(body["lines"]) for option in scripts.DETAILS_ASK_OPTIONS + scripts.NAME_ASK_OPTIONS)
    assert body["expect_reply"] is True


async def test_turn_for_unknown_call_returns_error_line(client):
    response = await client.post(TURN_URL, json={"call_id": "nope", "utterance": "hi"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["expect_reply"] is False


async def test_status_abandons_call_with_nothing_to_salvage(client, db_session):
    await client.post(START_URL, json={"call_id": "sip-drop"}, headers=AUTH)

    response = await client.post(STATUS_URL, json={"call_id": "sip-drop", "reason": "hangup"}, headers=AUTH)

    assert response.status_code == 204
    session = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == "sip-drop"))
    ).scalar_one()
    await db_session.refresh(session)
    assert session.state == VoiceCallState.ABANDONED
    assert session.ticket_id is None


async def test_missing_or_wrong_token_is_rejected(client):
    body = {"call_id": "sip-auth"}
    assert (await client.post(START_URL, json=body)).status_code == 401
    assert (await client.post(START_URL, json=body, headers={"Authorization": "Bearer wrong"})).status_code == 401


async def test_unconfigured_token_disables_endpoints(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_sip_gateway_token", "")
    response = await client.post(START_URL, json={"call_id": "sip-off"}, headers=AUTH)
    assert response.status_code == 403


async def test_retried_start_does_not_reset_a_call_in_progress(client, db_session):
    await client.post(START_URL, json={"call_id": "sip-retry"}, headers=AUTH)
    session = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == "sip-retry"))
    ).scalar_one()
    session.state = VoiceCallState.COLLECT_PHONE
    session.turns = [*session.turns, {"role": "caller", "text": "printer is jammed"}]
    await db_session.commit()

    response = await client.post(START_URL, json={"call_id": "sip-retry"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"lines": [], "expect_reply": True, "ticket_id": None}
    await db_session.refresh(session)
    assert session.state == VoiceCallState.COLLECT_PHONE

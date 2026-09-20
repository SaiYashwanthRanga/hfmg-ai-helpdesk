"""Twilio webhook endpoint tests: signature validation and the HTTP layer."""

import pytest
from sqlalchemy import select
from twilio.request_validator import RequestValidator

from app.core.config import get_settings
from app.db.models import Priority, Ticket, VoiceCallSession, VoiceCallState
from app.voice import nlu

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

VOICE_URL = "/api/v1/webhooks/twilio/voice"
GATHER_URL = "/api/v1/webhooks/twilio/voice/gather"
STATUS_URL = "/api/v1/webhooks/twilio/voice/status"


@pytest.fixture(autouse=True)
def _disable_signature_validation(monkeypatch):
    """Most tests exercise conversation flow, not auth; auth has its own test."""
    monkeypatch.setattr(settings, "twilio_validate_signature", False)


def _async(value):
    async def _coro():
        return value

    return _coro()


async def test_incoming_call_greets_and_creates_session(client, db_session):
    response = await client.post(
        VOICE_URL, data={"CallSid": "CA-web-1", "From": "+18455550142", "To": "+18455559999"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "Thank you for calling Horizon Family Medical Group IT Help Desk" in response.text
    assert "<Gather" in response.text

    session = (
        await db_session.execute(
            select(VoiceCallSession).where(VoiceCallSession.twilio_call_sid == "CA-web-1")
        )
    ).scalar_one()
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION
    assert session.collected["phone_number"] == "+18455550142"


async def test_incoming_call_is_idempotent(client, db_session):
    payload = {"CallSid": "CA-web-dup", "From": "+18455550142", "To": "+18455559999"}
    await client.post(VOICE_URL, data=payload)
    await client.post(VOICE_URL, data=payload)

    sessions = (
        await db_session.execute(
            select(VoiceCallSession).where(VoiceCallSession.twilio_call_sid == "CA-web-dup")
        )
    ).scalars().all()
    assert len(sessions) == 1


async def test_gather_for_unknown_call_does_not_crash(client):
    response = await client.post(GATHER_URL, data={"CallSid": "CA-nonexistent", "SpeechResult": "hi"})
    assert response.status_code == 200
    assert "<Hangup" in response.text


async def test_full_call_creates_phone_ticket(client, db_session, monkeypatch):
    from app.db.models import Category

    db_session.add(Category(name="Other", default_priority=Priority.MEDIUM))
    db_session.add(Category(name="Network", default_priority=Priority.HIGH))
    await db_session.commit()

    monkeypatch.setattr(
        nlu,
        "interpret_description",
        lambda u: _async(
            nlu.TurnResult(
                value="No internet in the back office.",
                confidence="high",
                unable_to_determine=False,
                category="Network",
                priority=Priority.HIGH,
                impact="the back office",
                extras={"short_issue": "the network"},
            )
        ),
    )
    monkeypatch.setattr(
        nlu,
        "interpret_name",
        lambda u: _async(nlu.TurnResult(value="John Smith", unable_to_determine=False)),
    )
    monkeypatch.setattr(
        nlu,
        "interpret_email",
        lambda u: _async(nlu.TurnResult(unable_to_determine=False, extras={"declined": True})),
    )

    await client.post(
        VOICE_URL, data={"CallSid": "CA-web-full", "From": "+18455550142", "To": "+18455559999"}
    )
    await client.post(
        GATHER_URL,
        data={"CallSid": "CA-web-full", "SpeechResult": "no internet back office", "Confidence": "0.91"},
    )
    await client.post(GATHER_URL, data={"CallSid": "CA-web-full", "SpeechResult": "John Smith"})
    final = await client.post(GATHER_URL, data={"CallSid": "CA-web-full", "SpeechResult": "skip"})

    assert "Your ticket number is" in final.text
    assert "H F M G" in final.text

    ticket = (await db_session.execute(select(Ticket))).scalars().one()
    assert ticket.caller_name == "John Smith"
    assert ticket.phone_number == "+18455550142"
    assert ticket.priority == Priority.HIGH


async def test_status_callback_salvages_abandoned_call_with_description(
    client, db_session, monkeypatch
):
    from app.db.models import Category

    db_session.add(Category(name="Other", default_priority=Priority.MEDIUM))
    await db_session.commit()

    monkeypatch.setattr(
        nlu,
        "interpret_description",
        lambda u: _async(
            nlu.TurnResult(
                value="Printer is jammed.",
                confidence="high",
                unable_to_determine=False,
                category="Other",
                priority=Priority.LOW,
                extras={"short_issue": "the printer"},
            )
        ),
    )

    await client.post(
        VOICE_URL, data={"CallSid": "CA-web-drop", "From": "+18455550142", "To": "+18455559999"}
    )
    await client.post(GATHER_URL, data={"CallSid": "CA-web-drop", "SpeechResult": "printer jammed"})
    # Caller hangs up before giving their name.
    response = await client.post(
        STATUS_URL, data={"CallSid": "CA-web-drop", "CallStatus": "completed"}
    )

    assert response.status_code == 204
    ticket = (await db_session.execute(select(Ticket))).scalars().one()
    assert "INCOMPLETE VOICE INTAKE" in ticket.description
    assert ticket.caller_name == "Unknown caller (voice)"


async def test_status_callback_creates_nothing_when_nothing_collected(client, db_session):
    await client.post(
        VOICE_URL, data={"CallSid": "CA-web-empty", "From": "+18455550142", "To": "+18455559999"}
    )
    await client.post(STATUS_URL, data={"CallSid": "CA-web-empty", "CallStatus": "completed"})

    tickets = (await db_session.execute(select(Ticket))).scalars().all()
    assert tickets == []

    session = (
        await db_session.execute(
            select(VoiceCallSession).where(VoiceCallSession.twilio_call_sid == "CA-web-empty")
        )
    ).scalar_one()
    assert session.state == VoiceCallState.ABANDONED


async def test_invalid_signature_is_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", True)
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_public_base_url", "https://example.test")

    response = await client.post(
        VOICE_URL,
        data={"CallSid": "CA-forged", "From": "+15555555555", "To": "+18455559999"},
        headers={"X-Twilio-Signature": "obviously-wrong"},
    )
    assert response.status_code == 403


async def test_valid_signature_is_accepted(client, monkeypatch):
    monkeypatch.setattr(settings, "twilio_validate_signature", True)
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_public_base_url", "https://example.test")

    params = {"CallSid": "CA-signed", "From": "+18455550142", "To": "+18455559999"}
    signature = RequestValidator("test-token").compute_signature(
        f"https://example.test{VOICE_URL}", params
    )

    response = await client.post(VOICE_URL, data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "Thank you for calling" in response.text

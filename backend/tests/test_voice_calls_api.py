import pytest

from app.db.models import EscalationReason, VoiceCallSession, VoiceCallState

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_session(db, **overrides) -> VoiceCallSession:
    defaults = dict(
        twilio_call_sid=f"CA-{overrides.get('twilio_call_sid', 'test')}",
        from_number="+18455550142",
        to_number="+18455559999",
        state=VoiceCallState.COMPLETED,
        collected={"caller_name": "Maria Lopez", "category": "eClinicalWorks", "priority": "HIGH"},
        turns=[{"role": "agent", "text": "Hello", "confidence": None, "at": "2026-01-01T00:00:00Z"}],
    )
    defaults.update(overrides)
    session = VoiceCallSession(**defaults)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def test_list_voice_calls_returns_denormalized_fields(client, db_session):
    await _make_session(db_session, twilio_call_sid="list-1")

    response = await client.get("/api/v1/voice-calls")
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 1
    item = body["items"][0]
    assert item["caller_name"] == "Maria Lopez"
    assert item["category"] == "eClinicalWorks"
    assert item["priority"] == "HIGH"
    assert item["state"] == "COMPLETED"


async def test_list_voice_calls_filters_by_state_and_escalated(client, db_session):
    await _make_session(db_session, twilio_call_sid="active-1", state=VoiceCallState.COLLECT_NAME)
    await _make_session(
        db_session,
        twilio_call_sid="escalated-1",
        state=VoiceCallState.ESCALATED,
        escalated=True,
        escalation_reason=EscalationReason.CALLER_REQUESTED,
    )

    by_state = await client.get("/api/v1/voice-calls", params={"state": "ESCALATED"})
    assert by_state.json()["total"] == 1
    assert by_state.json()["items"][0]["escalation_reason"] == "CALLER_REQUESTED"

    by_escalated = await client.get("/api/v1/voice-calls", params={"escalated": "true"})
    assert by_escalated.json()["total"] == 1

    not_escalated = await client.get("/api/v1/voice-calls", params={"escalated": "false"})
    assert not_escalated.json()["total"] == 1


async def test_voice_call_detail_includes_full_transcript(client, db_session):
    session = await _make_session(
        db_session,
        twilio_call_sid="detail-1",
        turns=[
            {"role": "agent", "text": "How can I help?", "confidence": None, "at": "2026-01-01T00:00:00Z"},
            {"role": "caller", "text": "eCW is down", "confidence": 0.91, "at": "2026-01-01T00:00:05Z"},
        ],
    )

    response = await client.get(f"/api/v1/voice-calls/{session.id}")
    assert response.status_code == 200
    body = response.json()

    assert len(body["turns"]) == 2
    assert body["turns"][1]["text"] == "eCW is down"
    assert body["collected"]["caller_name"] == "Maria Lopez"
    assert body["to_number"] == "+18455559999"


async def test_voice_call_detail_404_for_unknown_id(client):
    import uuid

    response = await client.get(f"/api/v1/voice-calls/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_voice_call_summary_counts_by_state(client, db_session):
    await _make_session(db_session, twilio_call_sid="s1", state=VoiceCallState.COLLECT_NAME)
    await _make_session(db_session, twilio_call_sid="s2", state=VoiceCallState.COMPLETED)
    await _make_session(db_session, twilio_call_sid="s3", state=VoiceCallState.COMPLETED)
    await _make_session(db_session, twilio_call_sid="s4", state=VoiceCallState.ESCALATED, escalated=True)
    await _make_session(db_session, twilio_call_sid="s5", state=VoiceCallState.ABANDONED)

    response = await client.get("/api/v1/voice-calls/summary")
    assert response.status_code == 200
    body = response.json()

    assert body == {"active": 1, "completed": 2, "escalated": 1, "abandoned": 1}

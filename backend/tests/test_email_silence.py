"""Silence at the email question.

Seen on a real call: after the extension, the email question got two empty answers (the gateway heard nothing) and the
call ended as a callback request -- "I'm having trouble understanding" -- although the email is optional and everything
else was collected. The re-ask also repeated "Got it, extension 3 0 0 0. Thanks, ..." from the line before it.

Now: silence at the email question (or its read-back) asks only the question again, once; a second silence leaves the
email off and the call carries on. It never counts toward a callback request.
"""

import pytest

from app.db.models import VoiceCallState
from app.voice import diagnostics, nlu, scripts
from tests.test_multi_issue_calls import AUTH, SIP, _session
from tests.test_multi_issue_calls import call  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _silence(call):  # noqa: F811
    response = await call.client.post(f"{SIP}/turn", json={"call_id": call.call_id, "utterance": ""}, headers=AUTH)
    assert response.status_code == 200
    return response.json()


async def _to_email_question(call, db_session):  # noqa: F811
    await call.start()
    await call.describe("A")
    call._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False))
    await call.turn("Maria Lopez")
    assert (await _session(db_session, call.call_id)).state == VoiceCallState.COLLECT_EMAIL


async def test_one_silence_asks_only_the_email_question_again(call, db_session):  # noqa: F811
    await _to_email_question(call, db_session)

    outcome = await _silence(call)

    session = await _session(db_session, call.call_id)
    assert outcome["expect_reply"] is True
    assert " ".join(outcome["lines"]) == f"{diagnostics.CLARIFY} {scripts.EMAIL_ASK_NO_NAME}"
    assert session.state == VoiceCallState.COLLECT_EMAIL
    assert session.misunderstanding_count == 0


async def test_two_silences_leave_the_email_off_and_the_call_carries_on(call, db_session):  # noqa: F811
    await _to_email_question(call, db_session)

    await _silence(call)
    outcome = await _silence(call)

    session = await _session(db_session, call.call_id)
    assert outcome["lines"][0].startswith(scripts.EMAIL_GIVE_UP)
    assert outcome["expect_reply"] is True
    assert session.state not in (VoiceCallState.ESCALATED, VoiceCallState.COLLECT_EMAIL)
    assert session.escalated is False
    assert session.collected.get("email") is None


async def test_an_address_after_one_silence_is_still_taken(call, db_session):  # noqa: F811
    await _to_email_question(call, db_session)
    await _silence(call)

    call._say(interpret_email=nlu.TurnResult(value="m@hfmg.net", confidence="high", unable_to_determine=False))
    await call.turn("m at hfmg dot net")

    assert (await _session(db_session, call.call_id)).state == VoiceCallState.CONFIRM_EMAIL


async def test_silence_at_the_email_read_back_asks_the_read_back_again(call, db_session):  # noqa: F811
    await _to_email_question(call, db_session)
    call._say(interpret_email=nlu.TurnResult(value="m@hfmg.net", confidence="high", unable_to_determine=False))
    await call.turn("m at hfmg dot net")

    outcome = await _silence(call)

    session = await _session(db_session, call.call_id)
    assert outcome["lines"][0].startswith(diagnostics.CLARIFY)
    assert session.state == VoiceCallState.CONFIRM_EMAIL
    assert session.misunderstanding_count == 0

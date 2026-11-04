"""`short_answer` on the SIP responses: the caller's next answer is a yes or a no.

The gateway then starts listening on about 120 ms of speech instead of 200 ms, so a quick "no" is not missed. It is
set on every /start and /turn response, for the next answer only, and false when no reply is expected.
"""

import pytest

from app.speech import context as speech_context
from app.voice import nlu
from tests.test_multi_issue_calls import AUTH, SIP, _session
from tests.test_multi_issue_calls import call  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize("state, collected", [
    ("CONFIRM_IDENTITY", {}), ("CONFIRM_EMAIL", {}), ("CONFIRM_NAME", {}), ("CONFIRM_CALLBACK_NUMBER", {}),
    ("CONFIRM_SUMMARY", {}), ("CONFIRM_CATEGORY", {}), ("ANYTHING_ELSE", {}), ("COLLECT_EXTENSION", {}),
    ("COLLECT_DETAILS", {"pending_question": "blocked"}), ("COLLECT_DETAILS", {"pending_question": "patient_care"}),
])
async def test_yes_no_questions_are_short_answers(state, collected):
    assert speech_context.expects_yes_no(state, collected)


@pytest.mark.parametrize("state, collected", [
    ("IDENTIFY_CALLER", {}), ("COLLECT_DESCRIPTION", {}), ("COLLECT_DETAILS", {}), ("COLLECT_NAME", {}),
    ("COLLECT_NAME", {"last_name_step": "last"}), ("COLLECT_PHONE", {}), ("COLLECT_EMAIL", {}),
    ("COLLECT_DIAGNOSTICS", {}), ("PATIENT_INTENT", {}), ("GREETING", {}), ("COMPLETED", {}),
    ("CONFIRM_NAME", {"name_spell_step": "first"}),  # spelling the name
    ("CONFIRM_EMAIL", {"email_fix": {"step": "which"}}),  # "which part was wrong?"
    ("CONFIRM_SUMMARY", {"summary_awaiting_change": True}),  # "What should I change?"
    ("COLLECT_EXTENSION", {"extension_number_asked": True}),  # "What's the extension number?"
    ("COLLECT_EXTENSION", {"extension_retried": True}),
])
async def test_open_questions_are_not(state, collected):
    assert not speech_context.expects_yes_no(state, collected)


async def _post(call, path, **body):  # noqa: F811
    response = await call.client.post(f"{SIP}/{path}", json={"call_id": call.call_id, **body}, headers=AUTH)
    assert response.status_code == 200
    return response.json()


async def test_every_response_says_whether_the_next_answer_is_yes_or_no(call, db_session):  # noqa: F811
    started = await _post(call, "start", from_number="+18455550142")
    assert started["short_answer"] is False  # "how can I help?": the problem

    await call.describe("A")
    call._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False))
    await call.turn("Maria Lopez")
    call._say(interpret_email=nlu.TurnResult(value="m@hfmg.net", confidence="high", unable_to_determine=False))
    read_back = await _post(call, "turn", utterance="m at hfmg dot net")
    assert (await _session(db_session, call.call_id)).state.value == "CONFIRM_EMAIL"
    assert read_back["short_answer"] is True  # "Is that correct?"

    filed = await call.answer(True)
    assert filed["ticket_id"] and filed["short_answer"] is True  # "Is there anything else I can help you with?"

    goodbye = await call.answer(False)
    assert goodbye["expect_reply"] is False and goodbye["short_answer"] is False


async def test_the_error_reply_for_an_unknown_call_is_not_a_short_answer(call):  # noqa: F811
    body = await _post(call, "turn", utterance="yes")

    assert body["expect_reply"] is False and body["short_answer"] is False


@pytest.mark.parametrize("said", ["Not really.", "not at all"])
async def test_not_really_is_a_no(said):
    assert nlu.quick_yes_no(said) is False
    assert nlu.quick_yes_no(said, strict=True) is False

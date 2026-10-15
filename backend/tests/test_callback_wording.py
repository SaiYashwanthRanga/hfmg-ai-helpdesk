"""The callback-number question is worded for who is calling.

A patient is not talking to the IT team and has a request, not a ticket. The caller type is the one settled
earlier in the call and is only read here; a call with no (or an unrecognised) type gets the employee wording,
exactly as before. The number read-back and the yes / no handling are the same for both. Real /start and /turn
routes with the language model stubbed, as in the other voice tests.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, VoiceCallSession, VoiceCallState
from app.voice import nlu, orchestrator, scripts
from tests.test_directory_identity import DirCall
from tests.test_multi_issue_calls import _async, _session
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550142"
SPOKEN = "8 4 5, 5 5 5, 0 1 4 2"
OTHER = "+12148859089"
OTHER_SPOKEN = "2 1 4, 8 8 5, 9 0 8 9"

PATIENT_QUESTION = (
    f"I have your callback number as {SPOKEN}. If our support team needs to reach you regarding your request, "
    "they will call this number. Is this the best number to contact you on?"
)
EMPLOYEE_QUESTION = (
    f"I have your callback number as {SPOKEN}. If our IT team needs to reach you regarding this ticket, "
    "they will call this number. Is this the best number to contact you on?"
)
QUESTION = {"patient": PATIENT_QUESTION, "employee": EMPLOYEE_QUESTION}
PATIENT_ASK_OTHER = "What number would you like our support team to call instead?"
EMPLOYEE_ASK_OTHER = "What number would you like our IT team to call instead?"
ASK_OTHER = {"patient": PATIENT_ASK_OTHER, "employee": EMPLOYEE_ASK_OTHER}
CALLER_TYPE = {"patient": "PATIENT_SUPPORT", "employee": "INTERNAL_IT"}
KINDS = pytest.mark.parametrize("kind", ["patient", "employee"])


@pytest.fixture(autouse=True)
async def _flow(monkeypatch, db_session):
    monkeypatch.setattr(settings, "voice_ask_caller_type", True)
    monkeypatch.setattr(settings, "voice_confirm_callback", True)
    for name in ("Appointment Booking", "Other Patient Support", "Other"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):
    def make(call_id, number=CALLER):
        return DirCall(client, monkeypatch, call_id, number)

    return make


async def _to_callback_question(call, kind):
    """Open the call as a patient or an employee and go on until the callback number is read back."""
    await call.start()
    if kind == "patient":
        await call.turn("I'm a patient")
        await call.turn("I need to book an appointment")
        await call.turn("next Tuesday morning")
        call._say(interpret_name=nlu.TurnResult(value="John Smith", confidence="high", unable_to_determine=False))
        return await call.turn("John Smith")
    await call.turn("I'm an employee")
    await call.report()
    await call.name_and_department("Pat Real")
    return await call.turn("skip")  # no email; the callback number is read back next


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


async def _ticket(client, ticket_id) -> dict:
    return (await client.get(f"/api/v1/tickets/{ticket_id}")).json()


# --- 1. the wording --------------------------------------------------------------------------------------------


async def test_a_patient_hears_support_team_and_request(make_call, db_session):
    call = make_call("cbw-patient")

    asked = await _to_callback_question(call, "patient")

    assert _text(asked) == PATIENT_QUESTION
    assert "support team" in _text(asked) and "your request" in _text(asked)
    session = await _session(db_session, "cbw-patient")
    assert session.state == VoiceCallState.CONFIRM_CALLBACK_NUMBER
    assert asked["ticket_id"] is None  # nothing is filed until the number is confirmed


async def test_a_patient_never_hears_it_team_or_ticket_in_the_callback_question(make_call):
    call = make_call("cbw-patient-words")

    asked = await _to_callback_question(call, "patient")

    heard = _text(asked).lower()
    assert "it team" not in heard and "ticket" not in heard


async def test_an_employee_still_hears_it_team_and_this_ticket(make_call, db_session):
    call = make_call("cbw-employee")

    asked = await _to_callback_question(call, "employee")

    assert _text(asked) == EMPLOYEE_QUESTION
    assert "IT team" in _text(asked) and "this ticket" in _text(asked)
    assert "support team" not in _text(asked)
    assert (await _session(db_session, "cbw-employee")).state == VoiceCallState.CONFIRM_CALLBACK_NUMBER


# --- 2. both flows keep the read-back and the confirmation ------------------------------------------------------


@KINDS
async def test_the_caller_type_is_not_changed_by_the_callback_question(kind, make_call, db_session):
    call = make_call(f"cbw-type-{kind}")

    await _to_callback_question(call, kind)

    assert (await _session(db_session, f"cbw-type-{kind}")).collected["caller_type"] == CALLER_TYPE[kind]


@KINDS
async def test_a_yes_confirms_the_number_on_file_and_files_the_call(kind, make_call, client, db_session):
    call = make_call(f"cbw-yes-{kind}")
    asked = await _to_callback_question(call, kind)
    assert f"I have your callback number as {SPOKEN}." in _text(asked)

    filed = await call.answer(True)

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"], ticket["caller_number"]) == (CALLER, CALLER, CALLER)
    session = await _session(db_session, f"cbw-yes-{kind}")
    assert session.state == VoiceCallState.ANYTHING_ELSE
    assert session.collected["caller_type"] == CALLER_TYPE[kind]


@KINDS
async def test_a_no_asks_for_another_number_and_confirms_that_one(kind, make_call, client, db_session):
    call = make_call(f"cbw-no-{kind}")
    await _to_callback_question(call, kind)

    asked_other = await call.answer(False)
    assert _text(asked_other) == ASK_OTHER[kind]
    assert (await _session(db_session, f"cbw-no-{kind}")).state == VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER

    call.phone_says(OTHER)
    confirm_new = await call.turn("two one four, eight eight five, nine oh eight nine")
    assert _text(confirm_new) == f"I have your callback number as {OTHER_SPOKEN}. Is that correct?"
    assert confirm_new["ticket_id"] is None

    filed = await call.answer(True)

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"], ticket["caller_number"]) == (OTHER, OTHER, CALLER)
    assert (await _session(db_session, f"cbw-no-{kind}")).collected["caller_type"] == CALLER_TYPE[kind]


# --- 2b. the question asking for another number ------------------------------------------------------------------


async def test_a_patient_who_rejects_the_number_is_asked_for_another_for_the_support_team(make_call, db_session):
    call = make_call("cbw-other-patient")
    await _to_callback_question(call, "patient")

    asked = await call.answer(False)

    assert _text(asked) == "What number would you like our support team to call instead?"
    assert "IT team" not in _text(asked)
    session = await _session(db_session, "cbw-other-patient")
    assert session.state == VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER
    assert session.collected["caller_type"] == "PATIENT_SUPPORT"  # asking for another number does not reclassify


async def test_an_employee_who_rejects_the_number_still_hears_the_it_team_wording(make_call, db_session):
    call = make_call("cbw-other-employee")
    await _to_callback_question(call, "employee")

    asked = await call.answer(False)

    assert _text(asked) == "What number would you like our IT team to call instead?"
    assert "support team" not in _text(asked)
    session = await _session(db_session, "cbw-other-employee")
    assert session.state == VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER
    assert session.collected["caller_type"] == "INTERNAL_IT"


@KINDS
async def test_turning_down_the_offered_number_asks_again_in_the_same_wording(kind, make_call, db_session):
    call = make_call(f"cbw-again-{kind}")
    await _to_callback_question(call, kind)
    await call.answer(False)
    call.phone_says(OTHER)
    await call.turn("two one four eight eight five nine oh eight nine")

    again = await call.answer(False)  # "no, that's not what I said"

    assert _text(again) == ASK_OTHER[kind]
    session = await _session(db_session, f"cbw-again-{kind}")
    assert session.state == VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER
    assert session.collected["caller_type"] == CALLER_TYPE[kind]


@KINDS
async def test_an_unusable_replacement_gets_the_same_retry_prompts_for_both(kind, make_call, db_session):
    call = make_call(f"cbw-retry-{kind}")
    await _to_callback_question(call, kind)
    await call.answer(False)
    call.phone_says(None)

    retry = await call.turn("one one zero zero six")  # five digits
    last = await call.turn("one zero zero six")  # the second miss ends the asking

    assert "I only caught 5 digits" in _text(retry)
    assert (await _session(db_session, f"cbw-retry-{kind}")).collected["caller_type"] == CALLER_TYPE[kind]
    assert f"we'll use {SPOKEN}" in _text(last)  # the number on file stands, as before
    assert last["ticket_id"] is not None


@KINDS
async def test_declining_to_give_another_number_keeps_the_number_on_file(kind, make_call, client):
    call = make_call(f"cbw-decline-{kind}")
    await _to_callback_question(call, kind)
    await call.answer(False)

    filed = await call.turn("skip")

    assert f"we'll use {SPOKEN}" in _text(filed)
    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"], ticket["caller_number"]) == (CALLER, None, CALLER)


@KINDS
async def test_an_unclear_answer_repeats_the_question_in_the_same_wording(kind, make_call, db_session):
    call = make_call(f"cbw-unclear-{kind}")
    await _to_callback_question(call, kind)
    unclear = nlu.TurnResult(unable_to_determine=True)
    call.monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(unclear))

    repeat = await call.turn("hmm, maybe")

    # Briefly, not the whole number again; worded for neither team, so it fits a patient and an employee alike.
    assert _text(repeat) == scripts.CALLBACK_YES_NO
    assert "IT team" not in scripts.CALLBACK_YES_NO and "support team" not in scripts.CALLBACK_YES_NO
    assert (await _session(db_session, f"cbw-unclear-{kind}")).state == VoiceCallState.CONFIRM_CALLBACK_NUMBER


# --- 3. no type, or one nobody recognises: the employee wording, and the type is left alone --------------------


async def test_a_call_with_no_caller_type_keeps_the_employee_wording_and_is_not_classified(make_call, db_session):
    call = make_call("cbw-missing")
    await _to_callback_question(call, "employee")
    row = await _session(db_session, "cbw-missing")
    row.collected = {key: value for key, value in row.collected.items() if key != "caller_type"}
    await db_session.commit()
    unclear = nlu.TurnResult(unable_to_determine=True)
    call.monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(unclear))

    repeat = await call.turn("hmm, maybe")

    assert _text(repeat) == scripts.CALLBACK_YES_NO
    assert "caller_type" not in (await _session(db_session, "cbw-missing")).collected


def _session_with(**collected) -> VoiceCallSession:
    return VoiceCallSession(
        call_id="cbw-direct", from_number=CALLER, to_number="+1", state=VoiceCallState.COLLECT_EMAIL, turns=[],
        collected={"phone_number": CALLER, **collected},
    )


@pytest.mark.parametrize("collected", [
    {},                                     # never classified
    {"caller_type": None},
    {"caller_type": ""},
    {"caller_type": "INTERNAL_IT"},
    {"caller_type": "PATIENT"},             # not the stored value
    {"caller_type": "patient_support"},     # wrong case
    {"caller_type": "SOMETHING_ELSE"},
])
async def test_only_a_confirmed_patient_gets_the_patient_wording(collected):
    session = _session_with(**collected)

    asked = orchestrator._ask_callback_confirmation(session)

    assert asked.text == EMPLOYEE_QUESTION
    assert session.state == VoiceCallState.CONFIRM_CALLBACK_NUMBER
    assert {k: v for k, v in session.collected.items() if k != "phone_number"} == collected  # nothing was classified


async def test_a_confirmed_patient_gets_the_patient_wording_from_the_same_step():
    session = _session_with(caller_type="PATIENT_SUPPORT")

    asked = orchestrator._ask_callback_confirmation(session)

    assert asked.text == PATIENT_QUESTION
    assert session.collected["caller_type"] == "PATIENT_SUPPORT"


async def test_a_number_the_caller_offered_is_confirmed_with_the_same_neutral_line_for_both_types():
    for caller_type in ("PATIENT_SUPPORT", "INTERNAL_IT"):
        session = _session_with(caller_type=caller_type, callback_candidate=OTHER)

        asked = orchestrator._ask_callback_confirmation(session)

        assert asked.text == f"I have your callback number as {OTHER_SPOKEN}. Is that correct?"


async def test_a_call_with_no_caller_type_is_asked_for_another_number_in_the_employee_wording(make_call, db_session):
    call = make_call("cbw-missing-other")
    await _to_callback_question(call, "employee")
    row = await _session(db_session, "cbw-missing-other")
    row.collected = {key: value for key, value in row.collected.items() if key != "caller_type"}
    await db_session.commit()

    asked = await call.answer(False)

    assert _text(asked) == EMPLOYEE_ASK_OTHER
    session = await _session(db_session, "cbw-missing-other")
    assert session.state == VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER
    assert "caller_type" not in session.collected


@pytest.mark.parametrize("collected", [
    {},                                     # never classified
    {"caller_type": None},
    {"caller_type": ""},
    {"caller_type": "INTERNAL_IT"},
    {"caller_type": "PATIENT"},             # not the stored value
    {"caller_type": "patient_support"},     # wrong case
    {"caller_type": "SOMETHING_ELSE"},
])
async def test_only_a_confirmed_patient_is_asked_for_another_number_for_the_support_team(collected):
    session = _session_with(**collected)

    asked = orchestrator._ask_alternate_callback(session)

    assert asked.text == EMPLOYEE_ASK_OTHER
    assert session.state == VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER
    assert {k: v for k, v in session.collected.items() if k != "phone_number"} == collected  # nothing was classified


async def test_a_confirmed_patient_is_asked_for_another_number_for_the_support_team_from_the_same_step():
    session = _session_with(caller_type="PATIENT_SUPPORT")

    asked = orchestrator._ask_alternate_callback(session)

    assert asked.text == PATIENT_ASK_OTHER
    assert session.collected["caller_type"] == "PATIENT_SUPPORT"

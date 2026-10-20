"""Escalation and leaving lines are worded for who is calling.

A patient hears about the support team and a request; an employee keeps the IT team and ticket wording; a caller
whose type is not known (not asked yet, or a stored value that is not a CallerType) hears neutral escalation lines. Only the wording differs: the
callback request is still created, numbered, flagged and the call still ends the same way. Real /start and /turn
routes where the state can be reached that way; `escalate` and `_caller_leaving` directly for the rest.
"""

import pytest

from app.db.models import CallerType, EscalationReason, Ticket, VoiceCallSession, VoiceCallState
from app.voice import nlu, orchestrator, scripts
from tests.test_callback_wording import _flow, _text, _ticket, _to_callback_question, make_call  # noqa: F401
from tests.test_multi_issue_calls import _async, _session
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

CALLER = "+18455550142"
SPOKEN = "8 4 5, 5 5 5, 0 1 4 2"
GOODBYE = "Thank you for calling Horizon Family Medical Group. Goodbye."

PATIENT_REQUESTED = "Of course. I'll have a member of our support team call you back at {phone}."
EMPLOYEE_REQUESTED = "Of course. I'll have a member of our IT team call you back at {phone}."
PATIENT_PHONE_ASK = "Of course. What's the best number for our support team to call you back on?"
EMPLOYEE_PHONE_ASK = "Of course. What's the best number for our IT team to call you back on?"
PATIENT_NO_NUMBER = "Of course. I'll ask a member of our support team to follow up with you."
EMPLOYEE_NO_NUMBER = "Of course. I'll ask a member of our IT team to follow up with you."
PATIENT_MISUNDERSTOOD = (
    "I'm sorry, I'm having trouble understanding, and I don't want to keep you. "
    "I'll have someone from our support team call you back at {phone} directly."
)
EMPLOYEE_MISUNDERSTOOD = (
    "I'm sorry, I'm having trouble understanding, and I don't want to keep you. "
    "I'll have someone from our IT team call you back at {phone} directly."
)
PATIENT_MISUNDERSTOOD_NO_NUMBER = (
    "I'm sorry, I'm having trouble understanding. I'll log a request for our support team to follow up."
)
EMPLOYEE_MISUNDERSTOOD_NO_NUMBER = (
    "I'm sorry, I'm having trouble understanding. I'll log a request for our IT team to follow up."
)
PATIENT_READ_BACK = "Your reference number is {number}."
EMPLOYEE_READ_BACK = "Your callback request is ticket number {number}. Someone will reach out to you shortly."
PATIENT_LEAVING = "No problem. I've saved what you told me. Your reference number is {number}. Take care."
EMPLOYEE_LEAVING = "No problem. I've saved what you told me. Your ticket number is {number}. Take care."

#: Stored caller types that are not a known CallerType: the caller is unknown, never a guessed patient.
UNKNOWN_TYPES = [{}, {"caller_type": None}, {"caller_type": ""}, {"caller_type": "PATIENT"},
                 {"caller_type": "patient_support"}, {"caller_type": "SOMETHING_ELSE"}]
#: Everything that is not a confirmed patient.
NOT_A_PATIENT = [*UNKNOWN_TYPES, {"caller_type": "INTERNAL_IT"}]


def _assert_patient_words(text: str) -> None:
    lowered = text.lower()
    assert "it team" not in lowered and "ticket" not in lowered, text
    assert "support team" in lowered


async def _number(client, ticket_id) -> str:
    return scripts.spoken_ticket_number((await _ticket(client, ticket_id))["ticket_number"])


async def _patient_at_request_question(call):
    await call.start()
    return await call.turn("I'm a patient")


async def _employee_at_problem_question(call):
    await call.start()
    return await call.turn("I'm an employee")


def _employee_asks_for_a_person(call):
    call._say(interpret_description=nlu.TurnResult(unable_to_determine=True, escalation_requested=True))


# --- 1. the caller asks for a person ------------------------------------------------------------------------


async def test_a_patient_who_asks_for_a_person_hears_the_support_team_and_a_request_number(make_call, client, db_session):
    call = make_call("esc-patient")
    await _patient_at_request_question(call)

    out = await call.turn("Can I just talk to a real person?")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{PATIENT_REQUESTED.format(phone=SPOKEN)} {PATIENT_READ_BACK.format(number=number)} {GOODBYE}"
    _assert_patient_words(_text(out))
    assert out["expect_reply"] is False  # the call still ends here
    session = await _session(db_session, "esc-patient")
    assert session.state == VoiceCallState.ESCALATED and session.escalation_reason == EscalationReason.CALLER_REQUESTED
    assert session.collected["caller_type"] == "PATIENT_SUPPORT"
    ticket = await _ticket(client, out["ticket_id"])
    assert "CALLBACK REQUESTED - caller asked to speak with a person." in ticket["description"]
    assert ticket["phone_number"] == CALLER


async def test_an_employee_who_asks_for_a_person_keeps_the_it_wording(make_call, client, db_session):
    call = make_call("esc-employee")
    await _employee_at_problem_question(call)
    _employee_asks_for_a_person(call)

    out = await call.turn("Can I just talk to a real person?")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{EMPLOYEE_REQUESTED.format(phone=SPOKEN)} {EMPLOYEE_READ_BACK.format(number=number)} {GOODBYE}"
    assert out["expect_reply"] is False
    session = await _session(db_session, "esc-employee")
    assert session.state == VoiceCallState.ESCALATED and session.collected["caller_type"] == "INTERNAL_IT"
    assert "CALLBACK REQUESTED - caller asked to speak with a person." in (await _ticket(client, out["ticket_id"]))["description"]


async def test_a_patient_who_asks_for_a_person_after_the_request_was_filed_gets_that_request_number(make_call, client, db_session):
    call = make_call("esc-patient-filed")
    await _to_callback_question(call, "patient")
    filed = await call.answer(True)
    filed_number = await _number(client, filed["ticket_id"])
    wants = nlu.TurnResult(unable_to_determine=True, escalation_requested=True)
    call.monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(wants))

    out = await call.turn("Can I talk to a real person?")

    assert PATIENT_READ_BACK.format(number=filed_number) in _text(out)  # no second request is created
    _assert_patient_words(_text(out))
    db_session.expire_all()
    assert (await _session(db_session, "esc-patient-filed")).state == VoiceCallState.ESCALATED


# --- 2. no number to call back ---------------------------------------------------------------------------------


async def test_a_patient_without_caller_id_is_asked_for_a_number_for_the_support_team(make_call, client, db_session):
    call = make_call("esc-patient-anon", "anonymous")
    await _patient_at_request_question(call)

    asked = await call.turn("Can I just talk to a real person?")

    assert _text(asked) == PATIENT_PHONE_ASK
    assert asked["ticket_id"] is None and asked["expect_reply"] is True
    assert (await _session(db_session, "esc-patient-anon")).state == VoiceCallState.COLLECT_PHONE

    call.phone_says("+12148859089")
    out = await call.turn("two one four, eight eight five, nine zero eight nine")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == (
        f"{PATIENT_REQUESTED.format(phone='2 1 4, 8 8 5, 9 0 8 9')} {PATIENT_READ_BACK.format(number=number)} {GOODBYE}"
    )
    assert (await _ticket(client, out["ticket_id"]))["phone_number"] == "+12148859089"


async def test_a_patient_with_no_usable_number_hears_the_no_number_line_and_no_callback_promise(make_call, client, db_session):
    call = make_call("esc-patient-nonum", "anonymous")
    await _patient_at_request_question(call)
    await call.turn("Can I just talk to a real person?")
    call.phone_says(None)

    out = await call.turn("mumble")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{PATIENT_NO_NUMBER} {PATIENT_READ_BACK.format(number=number)} {GOODBYE}"
    _assert_patient_words(_text(out))
    assert "call you" not in _text(out) and "reach out" not in _text(out)  # nobody can call: nothing promises it
    session = await _session(db_session, "esc-patient-nonum")
    assert session.state == VoiceCallState.ESCALATED and session.misunderstanding_count == 0


async def test_an_employee_without_caller_id_keeps_the_it_wording_throughout(make_call, client):
    call = make_call("esc-employee-nonum", "anonymous")
    await _employee_at_problem_question(call)
    _employee_asks_for_a_person(call)

    asked = await call.turn("Can I just talk to a real person?")
    assert _text(asked) == EMPLOYEE_PHONE_ASK

    call.phone_says(None)
    out = await call.turn("mumble")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{EMPLOYEE_NO_NUMBER} {EMPLOYEE_READ_BACK.format(number=number)} {GOODBYE}"


# --- 3. the agent could not understand ----------------------------------------------------------------------------


async def test_a_patient_the_agent_cannot_understand_is_handed_to_the_support_team(make_call, client, db_session):
    call = make_call("esc-patient-mis")
    await _patient_at_request_question(call)
    await call.turn("I need to book an appointment")  # now at the name question
    call._say(interpret_name=nlu.TurnResult())

    out = None
    for _ in range(3):
        out = await call.turn("mmm")
        if out["ticket_id"]:
            break

    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{PATIENT_MISUNDERSTOOD.format(phone=SPOKEN)} {PATIENT_READ_BACK.format(number=number)} {GOODBYE}"
    session = await _session(db_session, "esc-patient-mis")
    assert session.escalation_reason == EscalationReason.REPEATED_MISUNDERSTANDING
    assert session.collected["caller_type"] == "PATIENT_SUPPORT"


async def test_an_employee_the_agent_cannot_understand_keeps_the_it_wording(make_call, client, db_session):
    call = make_call("esc-employee-mis")
    await _employee_at_problem_question(call)
    await call.report()  # now at the name question
    call._say(interpret_name=nlu.TurnResult())

    out = None
    for _ in range(3):
        out = await call.turn("mmm")
        if out["ticket_id"]:
            break

    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{EMPLOYEE_MISUNDERSTOOD.format(phone=SPOKEN)} {EMPLOYEE_READ_BACK.format(number=number)} {GOODBYE}"
    assert (await _session(db_session, "esc-employee-mis")).escalation_reason == EscalationReason.REPEATED_MISUNDERSTANDING


def _in_memory_call(*, from_number=CALLER, **collected) -> VoiceCallSession:
    return VoiceCallSession(
        call_id=f"esc-direct-{id(collected)}", from_number=from_number, to_number="+1", state=VoiceCallState.COLLECT_NAME,
        turns=[], misunderstanding_count=0, is_simulated=False, escalated=False,
        collected={"description": "The printer is jammed.", "callback_number_asked": True, **collected},
    )


@pytest.mark.parametrize("reason", [EscalationReason.REPEATED_MISUNDERSTANDING, EscalationReason.SYSTEM_ERROR])
async def test_a_patient_with_no_number_who_could_not_be_understood_is_logged_for_the_support_team(reason, db_session):
    call = _in_memory_call(from_number="anonymous", caller_type="PATIENT_SUPPORT")

    out = await orchestrator.escalate(db_session, call, reason)

    ticket = await db_session.get(Ticket, out.ticket_id)
    number = scripts.spoken_ticket_number(ticket.ticket_number)
    assert out.text == f"{PATIENT_MISUNDERSTOOD_NO_NUMBER} {PATIENT_READ_BACK.format(number=number)} {GOODBYE}"
    assert out.hangup is True


# --- 4. a caller whose type is not known hears neutral lines; a confirmed employee keeps the IT lines ----------------

NEUTRAL_REQUESTED = "Of course. I'll pass your request to our team with your number, {phone}, so they can call you back."
NEUTRAL_PHONE_ASK = "Of course. What's the best number for our team to reach you on?"
NEUTRAL_NO_NUMBER = "Of course. I'll pass your request to our team."
NEUTRAL_MISUNDERSTOOD = (
    "I'm sorry, I'm having trouble understanding, and I don't want to keep you. "
    "I'll pass your request to our team with your number, {phone}, so they can call you back."
)
NEUTRAL_MISUNDERSTOOD_NO_NUMBER = "I'm sorry, I'm having trouble understanding. I'll pass your request to our team."
NEUTRAL_READ_BACK = "Your request number is {number}."


def _assert_neutral(text: str) -> None:
    """Nothing that guesses who is calling, and no arranged call."""
    lowered = text.lower()
    for word in ("it team", "support team", "ticket", "patient", "employee", "will call you", "i'll have"):
        assert word not in lowered, (word, text)
    assert "our team" in lowered


EMPLOYEE_ESCALATIONS = pytest.mark.parametrize("reason, phone, expected", [
    (EscalationReason.CALLER_REQUESTED, True, EMPLOYEE_REQUESTED.format(phone=SPOKEN)),
    (EscalationReason.CALLER_REQUESTED, False, EMPLOYEE_NO_NUMBER),
    (EscalationReason.REPEATED_MISUNDERSTANDING, True, EMPLOYEE_MISUNDERSTOOD.format(phone=SPOKEN)),
    (EscalationReason.REPEATED_MISUNDERSTANDING, False, EMPLOYEE_MISUNDERSTOOD_NO_NUMBER),
])
NEUTRAL_ESCALATIONS = pytest.mark.parametrize("reason, phone, expected", [
    (EscalationReason.CALLER_REQUESTED, True, NEUTRAL_REQUESTED.format(phone=SPOKEN)),
    (EscalationReason.CALLER_REQUESTED, False, NEUTRAL_NO_NUMBER),
    (EscalationReason.REPEATED_MISUNDERSTANDING, True, NEUTRAL_MISUNDERSTOOD.format(phone=SPOKEN)),
    (EscalationReason.REPEATED_MISUNDERSTANDING, False, NEUTRAL_MISUNDERSTOOD_NO_NUMBER),
    (EscalationReason.SYSTEM_ERROR, True, NEUTRAL_MISUNDERSTOOD.format(phone=SPOKEN)),
])


@EMPLOYEE_ESCALATIONS
async def test_a_confirmed_employee_keeps_the_it_escalation_lines(reason, phone, expected, db_session):
    call = _in_memory_call(from_number=CALLER if phone else "anonymous", caller_type="INTERNAL_IT")

    out = await orchestrator.escalate(db_session, call, reason)

    ticket = await db_session.get(Ticket, out.ticket_id)
    assert out.text == f"{expected} {EMPLOYEE_READ_BACK.format(number=scripts.spoken_ticket_number(ticket.ticket_number))} {GOODBYE}"
    assert ticket.caller_type == CallerType.INTERNAL_IT


@pytest.mark.parametrize("collected", UNKNOWN_TYPES)
@NEUTRAL_ESCALATIONS
async def test_a_caller_whose_type_is_not_known_hears_neutral_escalation_lines(collected, reason, phone, expected, db_session):
    call = _in_memory_call(from_number=CALLER if phone else "anonymous", **collected)

    out = await orchestrator.escalate(db_session, call, reason)

    ticket = await db_session.get(Ticket, out.ticket_id)
    number = scripts.spoken_ticket_number(ticket.ticket_number)
    assert out.text == f"{expected} {NEUTRAL_READ_BACK.format(number=number)} {GOODBYE}"
    _assert_neutral(out.text)
    assert out.hangup is True
    assert ticket.caller_type is None  # unknown, not guessed
    assert call.collected.get("caller_type") == collected.get("caller_type")  # never reclassified
    assert ("caller_type" in call.collected) == ("caller_type" in collected)


@pytest.mark.parametrize("collected, expected", [
    *[(c, NEUTRAL_PHONE_ASK) for c in UNKNOWN_TYPES],
    ({"caller_type": "INTERNAL_IT"}, EMPLOYEE_PHONE_ASK),
    ({"caller_type": "PATIENT_SUPPORT"}, PATIENT_PHONE_ASK),
])
async def test_the_number_question_before_escalating_follows_the_known_caller_type(collected, expected, db_session):
    call = _in_memory_call(from_number="anonymous", **collected)
    call.collected.pop("callback_number_asked")

    out = await orchestrator.escalate(db_session, call, EscalationReason.CALLER_REQUESTED)

    assert out.text == expected
    assert call.state == VoiceCallState.COLLECT_PHONE and out.ticket_id is None


async def test_a_caller_who_asks_for_a_person_before_saying_who_they_are_hears_neutral_lines(
    make_call, client, db_session, sent_emails
):
    call = make_call("esc-unknown")
    await call.start()  # "May I know if you are a patient or an employee?"

    out = await call.turn("Can I just talk to a real person?")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{NEUTRAL_REQUESTED.format(phone=SPOKEN)} {NEUTRAL_READ_BACK.format(number=number)} {GOODBYE}"
    _assert_neutral(_text(out))
    assert out["expect_reply"] is False
    session = await _session(db_session, "esc-unknown")
    assert session.state == VoiceCallState.ESCALATED and "caller_type" not in session.collected
    ticket = await _ticket(client, out["ticket_id"])
    assert ticket["caller_type"] is None
    assert "CALLBACK REQUESTED - caller asked to speak with a person." in ticket["description"]
    assert len(sent_emails) == 1


async def test_an_unknown_caller_without_caller_id_is_asked_for_a_number_in_neutral_words(make_call, client, db_session):
    call = make_call("esc-unknown-anon", "anonymous")
    await call.start()

    asked = await call.turn("Can I just talk to a real person?")
    assert _text(asked) == NEUTRAL_PHONE_ASK and asked["ticket_id"] is None

    call.phone_says("+12148859089")
    out = await call.turn("two one four, eight eight five, nine zero eight nine")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == (
        f"{NEUTRAL_REQUESTED.format(phone='2 1 4, 8 8 5, 9 0 8 9')} {NEUTRAL_READ_BACK.format(number=number)} {GOODBYE}"
    )


async def test_an_unknown_caller_with_no_usable_number_is_promised_no_call(make_call, client, db_session):
    call = make_call("esc-unknown-nonum", "anonymous")
    await call.start()
    await call.turn("Can I just talk to a real person?")
    call.phone_says(None)

    out = await call.turn("mumble")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{NEUTRAL_NO_NUMBER} {NEUTRAL_READ_BACK.format(number=number)} {GOODBYE}"
    _assert_neutral(_text(out))
    assert "call you back" not in _text(out)


async def test_a_call_whose_type_was_lost_is_treated_as_unknown(make_call, client, db_session):
    call = make_call("esc-missing")
    await _employee_at_problem_question(call)
    row = await _session(db_session, "esc-missing")
    row.collected = {key: value for key, value in row.collected.items() if key != "caller_type"}
    await db_session.commit()
    _employee_asks_for_a_person(call)

    out = await call.turn("Can I just talk to a real person?")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{NEUTRAL_REQUESTED.format(phone=SPOKEN)} {NEUTRAL_READ_BACK.format(number=number)} {GOODBYE}"
    assert "caller_type" not in (await _session(db_session, "esc-missing")).collected


# --- 4b. a stored caller type that is not a CallerType never crashes ticket creation --------------------------------


@pytest.mark.parametrize("collected, stored", [
    ({}, None),
    ({"caller_type": None}, None),
    ({"caller_type": ""}, None),
    ({"caller_type": "PATIENT"}, None),
    ({"caller_type": "patient_support"}, None),  # wrong case: not silently made a patient
    ({"caller_type": "internal_it"}, None),
    ({"caller_type": "SOMETHING_ELSE"}, None),
    ({"caller_type": ["PATIENT_SUPPORT"]}, None),  # not even a string
    ({"caller_type": "INTERNAL_IT"}, CallerType.INTERNAL_IT),
    ({"caller_type": "PATIENT_SUPPORT"}, CallerType.PATIENT_SUPPORT),
])
async def test_ticket_creation_stores_only_a_real_caller_type(collected, stored, db_session):
    call = _in_memory_call(caller_name="Pat Real", **collected)
    before = dict(call.collected)

    ticket = await orchestrator._create_ticket(db_session, call)

    assert ticket.caller_type == stored
    assert call.collected == before  # the call's own record is not rewritten


async def test_an_invalid_caller_type_gives_one_request_and_one_email_not_a_crash(make_call, client, db_session, sent_emails):
    call = make_call("esc-invalid")
    await _employee_at_problem_question(call)
    row = await _session(db_session, "esc-invalid")
    row.collected = {**row.collected, "caller_type": "PATIENT"}  # e.g. written by older code
    await db_session.commit()
    _employee_asks_for_a_person(call)

    out = await call.turn("Can I just talk to a real person?")

    assert out["ticket_id"] is not None  # before this fix: ValueError, no ticket, no email
    number = await _number(client, out["ticket_id"])
    assert _text(out) == f"{NEUTRAL_REQUESTED.format(phone=SPOKEN)} {NEUTRAL_READ_BACK.format(number=number)} {GOODBYE}"
    ticket = await _ticket(client, out["ticket_id"])
    assert ticket["caller_type"] is None
    assert "Other Patient Support" != ticket["category"]["name"]  # not filed as a patient request
    db_session.expire_all()
    session = await _session(db_session, "esc-invalid")
    assert session.collected["caller_type"] == "PATIENT"  # stored value untouched
    tickets = (await client.get("/api/v1/tickets", params={"q": "CALLBACK REQUESTED"})).json()
    assert tickets["total"] == 1
    assert len(sent_emails) == 1


async def test_an_invalid_caller_type_when_saying_goodbye_saves_one_ticket_with_the_ticket_wording(db_session):
    call = _in_memory_call(caller_name="Pat Real", caller_type="PATIENT")

    out = await orchestrator._caller_leaving(db_session, call)

    ticket = await db_session.get(Ticket, out.ticket_id)
    assert out.text == EMPLOYEE_LEAVING.format(number=scripts.spoken_ticket_number(ticket.ticket_number))
    assert ticket.caller_type is None and call.collected["caller_type"] == "PATIENT"


# --- 5. the caller says goodbye before the end -----------------------------------------------------------------------


async def test_a_patient_who_says_goodbye_after_their_request_is_saved_hears_a_request_number(make_call, client, db_session):
    call = make_call("leave-patient")
    await _to_callback_question(call, "patient")  # name given; the callback number is being confirmed

    out = await call.turn("Okay, thank you, bye.")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == PATIENT_LEAVING.format(number=number)
    assert "ticket" not in _text(out).lower() and "it team" not in _text(out).lower()
    assert out["expect_reply"] is False
    session = await _session(db_session, "leave-patient")
    assert session.state == VoiceCallState.COMPLETED and session.collected["caller_type"] == "PATIENT_SUPPORT"
    ticket = await _ticket(client, out["ticket_id"])
    assert ticket["caller_name"] == "John Smith"


async def test_a_patient_who_leaves_before_giving_a_name_still_gets_a_saved_request(make_call, client, db_session):
    call = make_call("leave-patient-early")
    await _patient_at_request_question(call)
    await call.turn("I need to book an appointment")  # at the name question

    out = await call.turn("Thanks, bye.")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == PATIENT_LEAVING.format(number=number)
    assert "INCOMPLETE VOICE INTAKE" in (await _ticket(client, out["ticket_id"]))["description"]  # flagged as before


async def test_an_employee_who_says_goodbye_after_the_ticket_is_saved_keeps_the_ticket_wording(make_call, client, db_session):
    call = make_call("leave-employee")
    await _to_callback_question(call, "employee")

    out = await call.turn("Okay, thank you, bye.")

    number = await _number(client, out["ticket_id"])
    assert _text(out) == EMPLOYEE_LEAVING.format(number=number)
    assert (await _session(db_session, "leave-employee")).state == VoiceCallState.COMPLETED


@pytest.mark.parametrize("collected", NOT_A_PATIENT)
async def test_only_a_confirmed_patient_hears_the_patient_leaving_line(collected, db_session):
    call = _in_memory_call(caller_name="Pat Real", **collected)

    out = await orchestrator._caller_leaving(db_session, call)

    ticket = await db_session.get(Ticket, out.ticket_id)
    assert out.text == EMPLOYEE_LEAVING.format(number=scripts.spoken_ticket_number(ticket.ticket_number))
    assert call.collected.get("caller_type") == collected.get("caller_type")


async def test_leaving_before_anything_was_saved_is_the_same_short_goodbye_for_both(make_call, db_session):
    for kind, call_id in (("patient", "leave-nothing-p"), ("employee", "leave-nothing-e")):
        call = make_call(call_id)
        await (_patient_at_request_question(call) if kind == "patient" else _employee_at_problem_question(call))

        out = await call.turn("Never mind, bye.")

        assert _text(out) == "No problem. Take care." and out["ticket_id"] is None
        assert (await _session(db_session, call_id)).state == VoiceCallState.ABANDONED

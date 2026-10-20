"""The problem-focused patient flow: the pre-merge checklist, one section per item.

Real /start and /turn routes and the real ticket pipeline; the language model is stubbed only where a step needs it
(the name, a spoken phone number). Synthetic callers and 555 numbers only.
"""

import pytest
from sqlalchemy import select

from app.db.models import Ticket, VoiceCallState
from app.services import ticket_service
from app.voice import caller_routing as routing
from app.voice import nlu, scripts
from tests.test_caller_routing import (  # noqa: F401  (fixtures)
    CALLER, CLAIMS, EMPLOYEE, PATIENT, _ask_who_is_calling, _hang_up, _patient_categories, _patient_request,
    _say_name, _tickets, it_model_off_limits, make_call,
)
from tests.test_directory_identity import _said
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")
NEW_NUMBER, NEW_SPOKEN = "+18455550199", "8 4 5, 5 5 5, 0 1 9 9"
APPOINTMENT_WORDS = ("doctor", "preferred day", "which appointment", "book a new appointment", "reschedule",
                     "cancel", "date and the doctor", "existing appointment")


async def _to_callback(call, wants="I need to change my appointment", name="Maria Lopez"):
    await call.start()
    await call.turn("I'm a patient")
    await call.turn(wants)
    return await _say_name(call, name)


# --- 1. any issue, no appointment questions -------------------------------------------------------------------------


@pytest.mark.parametrize("wants", [
    "I need to change my appointment", "I want to book a visit next week", "I have a question about my bill",
    "I can't log in to the patient portal", "I need a prescription refill", "my test results haven't come through",
])
async def test_any_issue_goes_straight_to_the_name_with_no_appointment_question(make_call, db_session, it_model_off_limits, wants):
    call = make_call("edge-any")
    await call.start()
    await call.turn("I'm a patient")

    out = await call.turn(wants)

    assert _said(out) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"
    assert not any(w in _said(out).lower() for w in APPOINTMENT_WORDS)


# --- 2. the callback number is confirmed, and a wrong one can be replaced ---------------------------------------------


async def test_the_callback_number_is_read_back_and_a_yes_files_it(make_call, client, db_session, sent_emails, it_model_off_limits):
    call = make_call("edge-cb-yes")
    asked = await _to_callback(call)
    assert _said(asked) == scripts.PATIENT_CALLBACK_CONFIRM.format(number="8 4 5, 5 5 5, 0 1 4 2")
    assert asked["ticket_id"] is None  # nothing filed before the number is confirmed

    filed = await call.turn("Yes.")

    assert (await _ticket(client, filed["ticket_id"]))["callback_number"] == CALLER


async def test_a_wrong_callback_number_is_replaced_by_the_one_the_patient_gives(make_call, client, db_session, sent_emails, it_model_off_limits):
    call = make_call("edge-cb-new")
    await _to_callback(call)

    other = await call.turn("No.")
    call.phone_says(NEW_NUMBER)
    read_back = await call.turn("845 555 0199")
    filed = await call.turn("Yes.")

    assert _said(other) == scripts.PATIENT_CALLBACK_ASK_OTHER
    assert _said(read_back) == scripts.CALLBACK_CONFIRM_NEW.format(number=NEW_SPOKEN)
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["callback_number"] == NEW_NUMBER and ticket["priority"] == "MEDIUM"
    assert "I've saved your request" in _said(filed)


async def test_okay_is_not_a_confirmation_of_the_number(make_call, db_session, it_model_off_limits):
    call = make_call("edge-cb-okay")
    await _to_callback(call)

    out = await call.turn("Okay.")

    assert _said(out) == scripts.CALLBACK_YES_NO and out["ticket_id"] is None


# --- 3. a failed request never sounds like a saved one ----------------------------------------------------------------


async def test_a_failed_request_is_never_reported_as_saved(make_call, client, db_session, sent_emails, it_model_off_limits, monkeypatch):
    """The request cannot be created: the turn is rolled back, so "I've saved your request" is never sent. The fallback
    files a callback request on a fresh transaction, and only that request's number is read out."""
    call = make_call("edge-fail-once")
    await _to_callback(call)
    real_create, calls = ticket_service.create_ticket, []

    async def fail_first(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database unavailable")
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(ticket_service, "create_ticket", fail_first)

    out = await call.turn("Yes.")

    assert "I've saved your request" not in _said(out)
    tickets = await _tickets(db_session)
    assert len(tickets) == 1 and out["ticket_id"] == str(tickets[0].id)  # the number read out is a real request
    assert scripts.spoken_ticket_number(tickets[0].ticket_number) in _said(out)
    assert tickets[0].caller_type.value == "PATIENT_SUPPORT" and tickets[0].priority.value == "MEDIUM"


async def test_when_nothing_can_be_saved_no_reference_number_is_given(make_call, client, db_session, sent_emails, it_model_off_limits, monkeypatch):
    call = make_call("edge-fail-always")
    await _to_callback(call)

    async def always_fail(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(ticket_service, "create_ticket", always_fail)

    out = await call.turn("Yes.")

    assert out["ticket_id"] is None and "reference number" not in _said(out).lower()
    assert "saved" not in _said(out).lower()
    assert await _tickets(db_session) == []


# --- 4. a hang-up after the problem still saves the request -------------------------------------------------------


async def test_a_hang_up_after_the_problem_saves_a_medium_patient_request(make_call, client, db_session, it_model_off_limits):
    call = make_call("edge-hangup")
    await call.start()
    await call.turn("I'm a patient")
    await call.turn("I have a question about my bill")

    await _hang_up(client, "edge-hangup")

    tickets = await _tickets(db_session)
    assert len(tickets) == 1
    assert (tickets[0].caller_type.value, tickets[0].priority.value) == ("PATIENT_SUPPORT", "MEDIUM")
    assert "question about my bill" in tickets[0].description and tickets[0].phone_number == CALLER


async def test_a_goodbye_after_the_problem_saves_the_request_and_reads_its_real_number(make_call, client, db_session, it_model_off_limits):
    call = make_call("edge-bye")
    await call.start()
    await call.turn("I'm a patient")
    await call.turn("I need a prescription refill")

    out = await call.turn("Thanks, bye.")

    ticket = await _ticket(client, out["ticket_id"])
    assert scripts.spoken_ticket_number(ticket["ticket_number"]) in _said(out) and ticket["priority"] == "MEDIUM"


async def test_a_hang_up_before_any_problem_saves_nothing(make_call, client, db_session):
    call = make_call("edge-hangup-early")
    await call.start()
    await call.turn("I'm a patient")

    await _hang_up(client, "edge-hangup-early")

    assert await _tickets(db_session) == []


# --- 5. a second request: same caller, new issue, new number --------------------------------------------------------


async def test_a_second_request_reuses_the_name_and_number_but_is_its_own_request(
    make_call, client, db_session, sent_emails, it_model_off_limits
):
    call = make_call("edge-second")
    first = await _patient_request(call, wants="I need to change my appointment", name="Maria Lopez")

    await call.turn("Yes.")  # anything else
    second = await call.turn("I also have a question about my bill")

    assert second["ticket_id"] and second["ticket_id"] != first["ticket_id"]
    one, two = await _ticket(client, first["ticket_id"]), await _ticket(client, second["ticket_id"])
    assert one["ticket_number"] != two["ticket_number"]
    assert scripts.spoken_ticket_number(two["ticket_number"]) in _said(second)  # its own number, not the first one
    assert scripts.spoken_ticket_number(one["ticket_number"]) not in _said(second)
    issue_one, issue_two = (t["description"].split("--- Call transcript ---")[0] for t in (one, two))
    assert "change my appointment" in issue_one and "question about my bill" in issue_two
    assert "change my appointment" not in issue_two  # the first issue never leaks into the second (the transcript keeps the whole call)
    assert (two["caller_name"], two["callback_number"]) == ("Maria Lopez", CALLER)
    assert scripts.PATIENT_NAME_ASK not in _said(second)


# --- 6. "actually, I'm an employee" at every patient step -------------------------------------------------------------


async def _at(call, stage):
    """Bring a patient call to `stage` and return nothing; the next turn is said there."""
    await call.start()
    await call.turn("I'm a patient")
    if stage == "problem":
        return
    await call.turn("I need to change my appointment")
    if stage == "name":
        return
    await _say_name(call, "Maria Lopez")
    if stage == "callback":
        return
    await call.turn("No.")  # a different number, then
    if stage == "other number":
        return
    raise AssertionError(stage)


@pytest.mark.parametrize("stage", ["problem", "name", "callback", "other number"])
async def test_an_employee_correction_is_recognized_at_every_patient_step(make_call, client, db_session, sent_emails, stage):
    call = make_call(f"edge-switch-{stage.replace(' ', '-')}")
    await _at(call, stage)

    moved = await call.turn("Actually, I'm an employee.")

    assert _said(moved) == f"{scripts.TYPE_CORRECTED} {scripts.EMPLOYEE_ASK}"
    session = await _session(db_session, call.call_id)
    assert session.collected["caller_type"] == EMPLOYEE and session.state == VoiceCallState.COLLECT_DESCRIPTION
    assert session.collected.get("patient_intent") is None and session.collected.get("description") is None
    assert await _tickets(db_session) == []  # the patient request in progress is dropped, never filed


async def test_an_employee_correction_at_a_second_request_moves_only_the_new_issue(make_call, client, db_session, sent_emails):
    call = make_call("edge-switch-second")
    first = await _patient_request(call)
    await call.turn("Yes.")  # anything else

    moved = await call.turn("Actually, I'm an employee.")

    assert _said(moved) == f"{scripts.TYPE_CORRECTED} {scripts.EMPLOYEE_ASK}"
    assert (await _session(db_session, "edge-switch-second")).collected["caller_type"] == EMPLOYEE
    assert (await _ticket(client, first["ticket_id"]))["caller_type"] == "PATIENT_SUPPORT"  # unchanged once filed


async def test_mentioning_an_employee_is_not_a_correction(make_call, db_session, it_model_off_limits):
    call = make_call("edge-mention")
    await call.start()
    await call.turn("I'm a patient")

    await call.turn("an employee at the front desk was rude to me")

    session = await _session(db_session, "edge-mention")
    assert session.collected["caller_type"] == PATIENT and "rude" in session.collected["description"]


# --- 8. Medium priority and the right patient category, every time ----------------------------------------------------


@pytest.mark.parametrize("wants, category", [
    ("I need to change my appointment", "Appointment Booking"),
    ("I need a prescription refill", "Prescription Refill Portal"),
    ("something else entirely, I'll explain to the nurse", "Other Patient Support"),
])
async def test_every_patient_request_is_medium_in_its_patient_category(make_call, client, db_session, sent_emails, it_model_off_limits, wants, category):
    call = make_call("edge-category")

    filed = await _patient_request(call, wants=wants)

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["priority"], ticket["category"]["name"], ticket["caller_type"]) == ("MEDIUM", category, "PATIENT_SUPPORT")


async def test_a_patient_who_asks_for_a_person_still_gets_a_medium_patient_request(make_call, client, db_session, sent_emails, it_model_off_limits):
    call = make_call("edge-person")
    await call.start()
    await call.turn("I'm a patient")
    await call.turn("I need a prescription refill")

    out = await call.turn("Can I talk to a real person?")

    ticket = await _ticket(client, out["ticket_id"])
    assert (ticket["priority"], ticket["category"]["name"]) == ("MEDIUM", "Prescription Refill Portal")
    assert "CALLBACK REQUESTED" in ticket["description"]


async def test_a_patient_category_that_is_missing_falls_back_to_other_patient_support_never_to_it(
    make_call, client, db_session, sent_emails, it_model_off_limits
):
    """"Test results" belong under Medical Records Portal; this database does not have it, as a server missing a
    category would not. The request still lands in a patient category, never the employee IT "Other"."""
    assert routing.patient_category(routing.OTHER, "my test results haven't come through") == "Medical Records Portal"
    call = make_call("edge-category-missing")

    filed = await _patient_request(call, wants="my test results haven't come through")

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["category"]["name"], ticket["priority"]) == ("Other Patient Support", "MEDIUM")

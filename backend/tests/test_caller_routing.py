"""The call opens by asking whether the caller is a patient or an employee.

Real /start and /turn routes, real routing rules; only the language model is stubbed, and only where the
employee flow needs it. External systems are not involved: there is no appointment system behind this line,
so these tests check that nothing is ever claimed to be booked, changed, cancelled or found.
"""

import asyncio
import re

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket, VoiceCallSession, VoiceCallState
from app.speech import context as speech_context
from app.voice import caller_routing as routing
from app.voice import nlu, orchestrator, scripts
from tests.test_directory_identity import AUTH, SIP, DirCall, _said
from tests.test_multi_issue_calls import _async, _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550142"
PATIENT, EMPLOYEE = routing.PATIENT, routing.EMPLOYEE
#: Anything that says an appointment was made, changed, cancelled, confirmed or found.
CLAIMS = re.compile(
    r"\b(?:has been|have been|is now|was|been) (?:booked|scheduled|rescheduled|cancel+ed|confirmed|moved)\b"
    r"|\bi(?:'ve| have) (?:booked|scheduled|rescheduled|cancel+ed|confirmed|moved|found|checked)\b"
    r"|\bavailab\w+|\byour appointment is\b",
    re.IGNORECASE,
)


@pytest.fixture(autouse=True)
def _ask_who_is_calling(monkeypatch):
    monkeypatch.setattr(settings, "voice_ask_caller_type", True)


@pytest.fixture(autouse=True)
async def _patient_categories(db_session):
    for name in ("Appointment Booking", "Other Patient Support", "Prescription Refill Portal", "Other"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):
    def make(call_id, number=CALLER):
        return DirCall(client, monkeypatch, call_id, number)

    return make


@pytest.fixture
def it_model_off_limits(monkeypatch):
    """A patient's turns must never reach the employee IT language model."""
    async def forbidden(*args, **kwargs):
        raise AssertionError("a patient turn reached the employee IT language model")

    monkeypatch.setattr(nlu, "interpret_description", forbidden)


async def _say_name(call, name):
    call._say(interpret_name=nlu.TurnResult(value=name, confidence="high", unable_to_determine=False))
    return await call.turn(name)


async def _patient_request(call, *, wants="I need to book an appointment", details=None, name="John Smith"):
    """Start, say patient, say what is needed, give a name (no other questions are asked)."""
    await call.start()
    await call.turn("I'm a patient")
    await call.turn(wants)
    if details is not None:
        await call.turn(details)
    await _say_name(call, name)
    return await call.turn("yes")  # patients always confirm the callback number first; this files the request


async def _employee_ticket(call, name="Pat Real"):
    await call.start()
    await call.turn("I'm an employee")
    await call.report()
    await call.name_and_department(name)
    return await call.skip_email()


async def _tickets(db_session) -> list[Ticket]:
    db_session.expire_all()
    return list((await db_session.execute(select(Ticket))).scalars().all())


async def _hang_up(client, call_id):
    return await client.post(f"{SIP}/status", json={"call_id": call_id, "reason": "hangup"}, headers=AUTH)


# --- the rules: who is calling ------------------------------------------------------------------------------


@pytest.mark.parametrize("utterance, expected", [
    ("I'm a patient.", PATIENT), ("patient", PATIENT), ("I am calling as a patient", PATIENT), ("I'm a new patient", PATIENT),
    ("I'm calling about an appointment.", PATIENT), ("I need to see the doctor", PATIENT), ("I need a prescription refill", PATIENT),
    ("I'm not an employee", PATIENT), ("I'm a patient calling with a question", PATIENT),
    ("I'm an employee.", EMPLOYEE), ("Employee.", EMPLOYEE), ("I work at the clinic.", EMPLOYEE), ("staff", EMPLOYEE),
    ("I'm with the clinic", EMPLOYEE), ("I'm calling from the front desk", EMPLOYEE), ("I'm not a patient", EMPLOYEE),
    ("my printer is jammed", EMPLOYEE), ("my Outlook isn't working", EMPLOYEE),
    ("I'm an employee and a patient can't check in", EMPLOYEE),  # who they are beats what they mention
    ("I'm a patient and I work here", None),  # both: ask
    ("a patient can't check in because the computer is down", None),  # about a patient, from the IT side: ask
    ("hello", None), ("umm", None), ("yes", None), ("", None),
])
async def test_natural_answers_are_classified_and_unclear_ones_are_not_guessed(utterance, expected):
    assert routing.identify_caller(utterance) == expected


@pytest.mark.parametrize("utterance, expected", [
    ("I'm an employee.", False), ("I work at the clinic.", False), ("I'm Dr. Patel's nurse", False),
    ("I'm an employee, my printer is jammed", True), ("employee, my Outlook keeps crashing", True),
    ("I work here and the internet is down", True),
])
async def test_an_employee_who_already_states_the_it_problem_is_not_asked_for_it_again(utterance, expected):
    assert routing.states_it_problem(utterance) is expected


@pytest.mark.parametrize("utterance, expected", [
    ("I want to book an appointment", routing.BOOK), ("I need to see the doctor", routing.BOOK),
    ("can I get an appointment for next week", routing.BOOK), ("I'd like to schedule a visit", routing.BOOK),
    ("I'd like to reschedule", routing.RESCHEDULE), ("can I move my appointment to Friday", routing.RESCHEDULE),
    ("change my appointment", routing.RESCHEDULE), ("cancel it and book another day", routing.RESCHEDULE),
    ("I need to cancel and book a new one", routing.RESCHEDULE),
    ("I need to cancel my appointment", routing.CANCEL), ("cancelling my visit", routing.CANCEL),
    ("what time is my appointment", routing.CHECK), ("I want to check when my appointment is", routing.CHECK),
    ("do I have an appointment tomorrow", routing.CHECK),
    ("I can't book an appointment on the website", routing.TROUBLE), ("the website won't let me schedule", routing.TROUBLE),
    ("I need a prescription refill", routing.OTHER), ("I have a question about my bill", routing.OTHER),
    ("I have trouble with the patient portal login", routing.OTHER),
    ("an existing one", None), ("appointment", None), ("hello", None), ("", None),
])
async def test_what_a_patient_wants_is_recognized_and_a_vague_request_is_not_guessed(utterance, expected):
    assert routing.patient_intent(utterance) == expected


@pytest.mark.parametrize("intent, utterance, category", [
    (routing.BOOK, "book", "Appointment Booking"), (routing.CANCEL, "cancel", "Appointment Booking"),
    (routing.TROUBLE, "can't book", "Appointment Booking"),
    (routing.OTHER, "I need a prescription refill", "Prescription Refill Portal"),
    (routing.OTHER, "something else entirely", "Other Patient Support"),
])
async def test_a_patient_request_lands_in_a_patient_category_that_already_exists(intent, utterance, category):
    assert routing.patient_category(intent, utterance) == category


# --- 1. the greeting ---------------------------------------------------------------------------------------------


async def test_a_new_call_asks_whether_the_caller_is_a_patient_or_an_employee(make_call, db_session):
    call = make_call("route-greeting")

    started = await call.start()

    assert started["lines"] == ["Thank you for calling Horizon Family Medical Group. Are you a patient or an employee?"]
    assert started["expect_reply"] is True
    assert (await _session(db_session, "route-greeting")).state == VoiceCallState.IDENTIFY_CALLER


async def test_a_retried_start_repeats_the_question_and_does_not_restart_the_call(make_call, db_session):
    call = make_call("route-retry")
    await call.start()

    again = await call.start()
    await call.turn("I'm a patient")
    late = await call.start()  # a late duplicate, after the caller has already answered

    assert again["lines"] == [scripts.GREETING_IDENTIFY]
    assert late["lines"] == []
    session = await _session(db_session, "route-retry")
    assert session.state == VoiceCallState.PATIENT_INTENT and session.collected["caller_type"] == PATIENT


async def test_with_the_step_switched_off_the_call_opens_as_before(make_call, db_session, monkeypatch):
    monkeypatch.setattr(settings, "voice_ask_caller_type", False)
    call = make_call("route-off")

    started = await call.start()

    assert started["lines"] == [scripts.GREETING]
    assert (await _session(db_session, "route-off")).state == VoiceCallState.COLLECT_DESCRIPTION


async def test_the_call_simulator_keeps_testing_the_employee_intake(db_session):
    session = VoiceCallSession(call_id="sim-like", from_number="", to_number="", state=VoiceCallState.GREETING, collected={}, turns=[])

    outcome = await orchestrator.start_call(session, identify_caller=False)

    assert session.state == VoiceCallState.COLLECT_DESCRIPTION and outcome.lines == [scripts.GREETING]


# --- 2. patient -------------------------------------------------------------------------------------------------------


async def test_a_patient_is_taken_into_the_patient_flow_not_the_it_flow(make_call, db_session, it_model_off_limits):
    call = make_call("route-patient")
    await call.start()

    asked = await call.turn("I'm a patient")

    assert asked["lines"] == [scripts.PATIENT_INTENT_ASK]
    session = await _session(db_session, "route-patient")
    assert session.state == VoiceCallState.PATIENT_INTENT and session.collected["caller_type"] == PATIENT
    assert session.misunderstanding_count == 0


async def test_a_patient_who_says_what_they_need_in_the_same_breath_is_not_asked_again(make_call, db_session, it_model_off_limits):
    call = make_call("route-patient-direct")
    await call.start()

    asked = await call.turn("I need to cancel my appointment")

    session = await _session(db_session, "route-patient-direct")
    assert session.collected["patient_intent"] == routing.CANCEL
    assert _said(asked) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"  # straight to the name


async def test_the_patient_flow_asks_only_for_the_problem_name_and_number_and_claims_nothing(
    make_call, client, db_session, sent_emails, it_model_off_limits
):
    call = make_call("route-patient-honest")
    await call.start()
    spoken = [(await call.turn("I'm a patient"))["lines"], (await call.turn("I need to change my appointment"))["lines"]]
    call._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False))
    spoken.append((await call.turn("Maria Lopez"))["lines"])
    filed = await call.turn("yes")  # the callback number, which files the request
    spoken.append(filed["lines"])

    assert spoken[0] == [scripts.PATIENT_INTENT_ASK]
    assert " ".join(spoken[1]) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"
    assert " ".join(spoken[2]).startswith("I have your callback number as")
    assert "best number to reach you" in " ".join(spoken[2])
    heard = " ".join(line for lines in spoken for line in lines)
    for never in ("doctor", "preferred day", "which appointment", "book a new appointment", "reschedule", "cancel",
                  "department", "extension", "email", "stopping you", "let me make sure"):
        assert never not in heard.lower(), never
    assert not CLAIMS.search(heard)
    ticket = await _ticket(client, filed["ticket_id"])
    assert f"Your reference number is {scripts.spoken_ticket_number(ticket['ticket_number'])}" in " ".join(spoken[3])
    assert "I've saved your request" in " ".join(spoken[3]) and "support team will follow up" in " ".join(spoken[3])


async def test_a_patient_request_becomes_a_patient_support_request_with_no_it_content(
    make_call, client, db_session, sent_emails, it_model_off_limits
):
    call = make_call("route-patient-ticket")

    filed = await _patient_request(call)

    assert filed["ticket_id"]
    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["caller_type"], ticket["caller_name"], ticket["priority"]) == ("PATIENT_SUPPORT", "John Smith", "MEDIUM")
    assert ticket["category"]["name"] == "Appointment Booking" and ticket["phone_number"] == CALLER
    assert 'Patient request: book an appointment. Caller said: "I need to book an appointment"' in ticket["description"]
    for it_only in ("--- Intake details ---", "Department:", "Work blocked:", "Started:", "Priority:", "Details:"):
        assert it_only not in ticket["description"]
    assert len(await _tickets(db_session)) == 1


@pytest.mark.parametrize("wants, intent, category", [
    ("I need to book an appointment", routing.BOOK, "Appointment Booking"),
    ("I want to reschedule my appointment", routing.RESCHEDULE, "Appointment Booking"),
    ("I need to cancel my appointment", routing.CANCEL, "Appointment Booking"),
    ("what time is my appointment", routing.CHECK, "Appointment Booking"),
    ("I can't book an appointment on the website", routing.TROUBLE, "Appointment Booking"),
])
async def test_no_appointment_request_gets_an_appointment_question(
    make_call, client, db_session, sent_emails, it_model_off_limits, wants, intent, category
):
    call = make_call(f"route-intent-{intent}")
    await call.start()
    await call.turn("I'm a patient")

    asked = await call.turn(wants)

    assert _said(asked) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"
    await _say_name(call, "Ann Patient")
    filed = await call.turn("yes")
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["category"]["name"] == category and f"Patient request: {routing.INTENT_LABELS[intent]}." in ticket["description"]


async def test_a_request_that_is_not_about_an_appointment_still_reaches_the_team_in_the_right_category(
    make_call, client, db_session, sent_emails, it_model_off_limits
):
    call = make_call("route-patient-other")

    filed = await _patient_request(call, wants="I need a prescription refill")

    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["category"]["name"] == "Prescription Refill Portal" and ticket["caller_type"] == "PATIENT_SUPPORT"


async def test_any_problem_is_taken_in_the_callers_own_words_never_asked_to_pick_a_kind_of_appointment(
    make_call, client, db_session, sent_emails, it_model_off_limits
):
    call = make_call("route-patient-vague")
    await call.start()
    await call.turn("I'm a patient")

    asked_name = await call.turn("it's complicated, I'll explain to the nurse")

    assert _said(asked_name) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"
    session = await _session(db_session, "route-patient-vague")
    assert session.collected["patient_intent"] == routing.OTHER and "complicated" in session.collected["description"]
    await _say_name(call, "Ann Patient")
    filed = await call.turn("yes")
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["category"]["name"] == "Other Patient Support"  # nothing was guessed


async def test_only_a_non_answer_is_asked_about_again(make_call, db_session, it_model_off_limits):
    call = make_call("route-patient-hello")
    await call.start()
    await call.turn("I'm a patient")

    again = await call.turn("hello?")
    asked_name = await call.turn("I have a question about my bill")

    assert _said(again) == scripts.PATIENT_INTENT_CLARIFY
    assert _said(asked_name) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"


async def test_a_second_request_from_a_patient_starts_at_what_do_you_need(make_call, client, db_session, sent_emails, it_model_off_limits):
    call = make_call("route-patient-two")
    first = await _patient_request(call)

    asked = await call.turn("yes")  # "anything else?"
    second = await call.turn("I need to cancel my appointment")

    assert _said(asked) == scripts.PATIENT_INTENT_ASK_AGAIN and first["ticket_id"]
    session = await _session(db_session, "route-patient-two")
    assert session.collected["patient_intent"] == routing.CANCEL
    # The name and the confirmed callback number are already known: the second request is saved straight away.
    assert scripts.PATIENT_NAME_ASK not in _said(second) and "callback number" not in _said(second).lower()
    assert second["ticket_id"] and "I've saved your request" in _said(second)
    assert len(await _tickets(db_session)) == 2 and all(t.caller_type.value == "PATIENT_SUPPORT" for t in await _tickets(db_session))


# --- 3. employee ---------------------------------------------------------------------------------------------------------


async def test_an_employee_enters_the_existing_it_flow_unchanged(make_call, client, db_session, sent_emails):
    call = make_call("route-employee")
    await call.start()

    asked = await call.turn("I'm an employee")

    assert asked["lines"] == [scripts.EMPLOYEE_ASK]
    session = await _session(db_session, "route-employee")
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION and session.collected["caller_type"] == EMPLOYEE
    after_issue = await call.report()
    assert any(option in _said(after_issue) for option in scripts.NAME_ASK_OPTIONS)  # the usual next question


async def test_an_employee_ticket_is_created_exactly_as_before(make_call, client, db_session, sent_emails):
    call = make_call("route-employee-ticket")

    filed = await _employee_ticket(call)

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["caller_type"], ticket["caller_name"], ticket["source"]) == ("INTERNAL_IT", "Pat Real", "PHONE")
    assert "--- Intake details ---" in ticket["description"] and "Department: Billing" in ticket["description"]
    assert "Patient request" not in ticket["description"]
    assert len(sent_emails) == 1  # the same helpdesk notification


async def test_an_employee_who_states_the_problem_with_the_answer_is_not_asked_for_it_again(make_call, db_session, sent_emails):
    call = make_call("route-employee-direct")
    await call.start()
    call._say(interpret_description=nlu.TurnResult(
        value="My printer is jammed.", confidence="high", unable_to_determine=False, category="Printer",
        priority=Priority.MEDIUM, extras={"short_issue": "your printer", "started": "today", "work_blocked": False},
    ))

    asked = await call.turn("I'm an employee and my printer is jammed")

    assert scripts.EMPLOYEE_ASK not in _said(asked)
    session = await _session(db_session, "route-employee-direct")
    assert session.collected["description"] == "My printer is jammed." and session.collected["caller_type"] == EMPLOYEE


# --- 4. unclear answers --------------------------------------------------------------------------------------------------------


@pytest.fixture
def llm_unsure(monkeypatch):
    monkeypatch.setattr(nlu, "classify_caller_type", lambda d: _async(nlu.CallerClassification("INTERNAL_IT", 0.5)))


async def test_an_answer_with_cues_for_both_sides_is_asked_about_not_guessed(make_call, db_session, llm_unsure):
    call = make_call("route-both")
    await call.start()

    clarify = await call.turn("I'm a patient and I work here")

    assert clarify["lines"] == [scripts.IDENTIFY_CLARIFY]
    assert (await _session(db_session, "route-both")).state == VoiceCallState.IDENTIFY_CALLER
    patient = await call.turn("patient")
    assert scripts.PATIENT_INTENT_ASK in _said(patient)


async def test_a_hello_is_answered_with_the_question_again_and_is_not_counted_as_unclear(make_call, db_session, llm_unsure):
    call = make_call("route-hello")
    await call.start()

    again = await call.turn("Hello?")
    assert again["lines"] == [scripts.IDENTIFY_AFTER_HELLO]
    still = await call.turn("hi there")

    assert still["lines"] == [scripts.IDENTIFY_AFTER_HELLO]
    assert (await _session(db_session, "route-hello")).collected.get("identify_attempts", 0) == 0


async def test_two_unclear_answers_fall_back_to_describing_the_problem_never_a_dead_end(make_call, client, db_session, llm_unsure, sent_emails):
    call = make_call("route-fallback")
    await call.start()

    first = await call.turn("hmm let me think about it")
    second = await call.turn("I am not sure what to say")

    assert first["lines"] == [scripts.IDENTIFY_CLARIFY] and second["lines"] == [scripts.IDENTIFY_FALLBACK]
    session = await _session(db_session, "route-fallback")
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION and session.misunderstanding_count == 0
    after_issue = await call.report()  # the older flow takes over: the type is worked out from the problem
    assert any(option in _said(after_issue) for option in scripts.NAME_ASK_OPTIONS)


async def test_an_answer_the_rules_cannot_read_may_be_settled_by_the_language_model_when_it_is_sure(make_call, db_session, monkeypatch):
    monkeypatch.setattr(nlu, "classify_caller_type", lambda d: _async(nlu.CallerClassification("INTERNAL_IT", 0.93)))
    call = make_call("route-llm")
    await call.start()

    asked = await call.turn("I'm Dr. Patel's nurse")

    assert scripts.EMPLOYEE_ASK in _said(asked)
    assert (await _session(db_session, "route-llm")).collected["caller_type"] == EMPLOYEE


async def test_the_language_model_is_never_asked_when_the_cues_conflict(make_call, db_session, monkeypatch):
    async def forbidden(description):
        raise AssertionError("cues for both sides must be asked about, not sent to the model")

    monkeypatch.setattr(nlu, "classify_caller_type", forbidden)
    call = make_call("route-no-llm")
    await call.start()

    assert (await call.turn("I'm a patient and I work here"))["lines"] == [scripts.IDENTIFY_CLARIFY]


# --- 5. the type is kept, and a correction is safe -------------------------------------------------------------------------------


async def test_the_callers_type_is_kept_and_the_question_is_never_asked_again(make_call, client, db_session, sent_emails, it_model_off_limits):
    call = make_call("route-persist")
    await call.start()
    spoken = []
    for utterance in ("I'm a patient", "I need to cancel my appointment", "the fifteenth"):
        spoken.append((await call.turn(utterance))["lines"])
        assert (await _session(db_session, "route-persist")).collected["caller_type"] == PATIENT
    call._say(interpret_name=nlu.TurnResult(value="Ann Patient", confidence="high", unable_to_determine=False))
    spoken.append((await call.turn("Ann Patient"))["lines"])
    spoken.append((await call.turn("yes"))["lines"])

    heard = " ".join(line for lines in spoken for line in lines)
    for repeated in (scripts.GREETING_IDENTIFY, scripts.IDENTIFY_CLARIFY, scripts.IDENTIFY_AFTER_HELLO):
        assert repeated not in heard


async def test_a_patient_who_says_they_are_an_employee_is_moved_to_the_it_flow_and_the_patient_request_is_dropped(
    make_call, client, db_session, sent_emails
):
    call = make_call("route-correct-1")
    await call.start()
    await call.turn("I'm a patient")
    await call.turn("I need to cancel my appointment")

    moved = await call.turn("Actually, I'm an employee")

    assert scripts.TYPE_CORRECTED in _said(moved) and scripts.EMPLOYEE_ASK in _said(moved)
    session = await _session(db_session, "route-correct-1")
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION and session.collected["caller_type"] == EMPLOYEE
    assert session.collected.get("patient_intent") is None and session.collected.get("description") is None
    filed_after = await call.report()
    assert any(option in _said(filed_after) for option in scripts.NAME_ASK_OPTIONS)


async def test_an_employee_who_says_they_are_a_patient_at_the_first_question_is_moved_to_the_patient_flow(
    make_call, db_session, it_model_off_limits
):
    call = make_call("route-correct-2")
    await call.start()
    await call.turn("I'm an employee")

    moved = await call.turn("Sorry, I'm a patient")

    assert scripts.PATIENT_INTENT_ASK in _said(moved)
    assert (await _session(db_session, "route-correct-2")).collected["caller_type"] == PATIENT


async def test_a_sentence_that_only_mentions_a_patient_never_changes_who_is_calling(make_call, db_session, sent_emails):
    call = make_call("route-nochange")
    await call.start()
    await call.turn("I'm an employee")
    call._say(interpret_description=nlu.TurnResult(
        value="A patient cannot check in.", confidence="high", unable_to_determine=False, category="Other",
        priority=Priority.MEDIUM, extras={"short_issue": "check-in", "started": "today", "work_blocked": True},
    ))

    await call.turn("a patient cannot check in because the computer froze")

    session = await _session(db_session, "route-nochange")
    assert session.collected["caller_type"] == EMPLOYEE and session.collected["description"] == "A patient cannot check in."


async def test_at_a_second_request_an_employee_is_moved_and_the_filed_patient_request_stays(
    make_call, client, db_session, sent_emails, it_model_off_limits
):
    call = make_call("route-late-switch")
    first = await _patient_request(call)

    await call.turn("yes")  # anything else
    moved = await call.turn("Actually I'm an employee")  # the next issue is an employee's

    assert _said(moved) == f"{scripts.TYPE_CORRECTED} {scripts.EMPLOYEE_ASK}"
    session = await _session(db_session, "route-late-switch")
    assert session.collected["caller_type"] == EMPLOYEE and session.state == VoiceCallState.COLLECT_DESCRIPTION
    assert len(session.collected["ticket_ids"]) == 1  # nothing new filed by the switch itself
    assert (await _ticket(client, first["ticket_id"]))["caller_type"] == "PATIENT_SUPPORT"  # never changed after filing


# --- 6. simultaneous calls, retries ---------------------------------------------------------------------------------------------------------


async def test_simultaneous_calls_keep_their_own_caller_type_and_details(make_call, client, db_session, sent_emails, monkeypatch):
    async def name_from_utterance(utterance, **kwargs):
        return nlu.TurnResult(value=utterance.split(",")[0], confidence="high", unable_to_determine=False, extras={"department": "Billing"})

    monkeypatch.setattr(nlu, "interpret_name", name_from_utterance)
    monkeypatch.setattr(nlu, "interpret_description", lambda u: _async(nlu.TurnResult(
        value="My printer is jammed.", confidence="high", unable_to_determine=False, category="Printer",
        priority=Priority.MEDIUM, extras={"short_issue": "your printer", "started": "today", "work_blocked": False},
    )))
    numbers = [f"+1845555040{n}" for n in range(6)]
    calls = [make_call(f"route-par-{n}", numbers[n]) for n in range(6)]
    kinds = ["I'm a patient", "I'm an employee"] * 3

    await asyncio.gather(*(c.start() for c in calls))
    await asyncio.gather(*(c.turn(k) for c, k in zip(calls, kinds)))
    sessions = [await _session(db_session, f"route-par-{n}") for n in range(6)]
    assert [s.collected["caller_type"] for s in sessions] == [PATIENT, EMPLOYEE] * 3
    assert [s.state.value for s in sessions] == ["PATIENT_INTENT", "COLLECT_DESCRIPTION"] * 3

    patients, employees = calls[0::2], calls[1::2]
    await asyncio.gather(*(c.turn("I need to book an appointment") for c in patients), *(c.turn("my printer is jammed") for c in employees))
    await asyncio.gather(*(c.turn(f"Pat {i}") for i, c in enumerate(patients)), *(c.turn(f"Emp {i}, Billing") for i, c in enumerate(employees)))
    answers = await asyncio.gather(*(c.turn("yes") for c in patients), *(c.turn("skip") for c in employees))
    filed = answers[:3]  # the callback number: files each patient request

    tickets = {n: await _ticket(client, (await _session(db_session, f"route-par-{n}")).ticket_id) for n in range(6)}
    for n in (0, 2, 4):
        assert (tickets[n]["caller_type"], tickets[n]["category"]["name"], tickets[n]["caller_name"]) == ("PATIENT_SUPPORT", "Appointment Booking", f"Pat {n // 2}")
        assert tickets[n]["phone_number"] == numbers[n] and "Intake details" not in tickets[n]["description"]
    for n in (1, 3, 5):
        assert tickets[n]["caller_type"] == "INTERNAL_IT" and "Patient request" not in tickets[n]["description"]
        assert tickets[n]["caller_name"] == f"Emp {n // 2}" and tickets[n]["phone_number"] == numbers[n]
    assert len(await _tickets(db_session)) == 6 and all(f["ticket_id"] for f in filed)


async def test_a_retried_final_turn_does_not_make_a_second_patient_request(make_call, client, db_session, sent_emails, it_model_off_limits):
    call = make_call("route-dup-patient")
    await call.start()
    await call.turn("I'm a patient")
    await call.turn("I need to cancel my appointment")
    await call.turn("the fifteenth")
    await _say_name(call, "Ann Patient")  # the callback number is asked next; the answer files the request
    body = {"call_id": "route-dup-patient", "utterance": "yes", "turn_id": "turn-final"}

    first, second = await asyncio.gather(
        client.post(f"{SIP}/turn", json=body, headers=AUTH), client.post(f"{SIP}/turn", json=body, headers=AUTH)
    )
    third = await client.post(f"{SIP}/turn", json=body, headers=AUTH)

    assert first.json()["ticket_id"] and second.json() == first.json() == third.json()
    assert len(await _tickets(db_session)) == 1


async def test_a_retried_final_turn_does_not_make_a_second_employee_ticket(make_call, client, db_session, sent_emails):
    call = make_call("route-dup-employee")
    await call.start()
    await call.turn("I'm an employee")
    await call.report()
    await call.name_and_department("Pat Real")
    body = {"call_id": "route-dup-employee", "utterance": "skip", "turn_id": "turn-final"}

    first = await client.post(f"{SIP}/turn", json=body, headers=AUTH)
    second = await client.post(f"{SIP}/turn", json=body, headers=AUTH)

    assert first.json()["ticket_id"] and second.json() == first.json()
    assert len(await _tickets(db_session)) == 1 and len(sent_emails) == 1


# --- 7. interruptions, silence, hang-ups ------------------------------------------------------------------------------------------------------------


async def test_silence_at_the_first_question_is_asked_again_and_is_not_a_failure(make_call, db_session, llm_unsure):
    call = make_call("route-silence")
    await call.start()

    first = await call.turn("")
    second = await call.turn("")

    assert first["lines"] == [scripts.IDENTIFY_CLARIFY] and second["lines"] == [scripts.IDENTIFY_FALLBACK]
    assert (await _session(db_session, "route-silence")).misunderstanding_count == 0


async def test_silence_during_the_patient_flow_never_stalls_or_escalates(make_call, client, db_session, sent_emails, it_model_off_limits):
    call = make_call("route-silence-patient")
    await call.start()
    await call.turn("I'm a patient")

    await call.turn("")  # the first silence: ask what they need, more simply
    kept = await call.turn("")  # still silent: the request is kept as "other", for the team

    session = await _session(db_session, "route-silence-patient")
    assert session.collected["patient_intent"] == routing.OTHER and session.state == VoiceCallState.COLLECT_NAME
    assert scripts.PATIENT_NAME_ASK in _said(kept) and session.misunderstanding_count == 0


async def test_a_hang_up_before_anyone_answered_the_question_makes_no_ticket(make_call, client, db_session):
    call = make_call("route-hangup-1")
    await call.start()

    response = await _hang_up(client, "route-hangup-1")
    await _hang_up(client, "route-hangup-1")

    assert response.status_code == 204
    assert (await _session(db_session, "route-hangup-1")).state == VoiceCallState.ABANDONED
    assert await _tickets(db_session) == []


async def test_a_hang_up_during_a_patient_request_leaves_a_patient_request_never_an_it_ticket(
    make_call, client, db_session, sent_emails, it_model_off_limits
):
    call = make_call("route-hangup-2")
    await call.start()
    await call.turn("I'm a patient")
    await call.turn("I need to cancel my appointment")

    await _hang_up(client, "route-hangup-2")
    await _hang_up(client, "route-hangup-2")

    tickets = await _tickets(db_session)
    assert len(tickets) == 1 and tickets[0].caller_type.value == "PATIENT_SUPPORT"
    assert "INCOMPLETE VOICE INTAKE" in tickets[0].description and "Intake details" not in tickets[0].description
    assert (await _session(db_session, "route-hangup-2")).state == VoiceCallState.ABANDONED


async def test_a_goodbye_at_the_first_question_ends_the_call_politely_with_no_ticket(make_call, db_session):
    call = make_call("route-bye")
    await call.start()

    ended = await call.turn("goodbye")

    assert ended["expect_reply"] is False and ended["ticket_id"] is None
    assert (await _session(db_session, "route-bye")).state == VoiceCallState.ABANDONED


async def test_asking_for_a_person_at_the_first_question_gets_a_callback_as_everywhere_else(make_call, client, db_session, sent_emails):
    call = make_call("route-human")
    await call.start()

    handed_off = await call.turn("I want to talk to a human")

    assert handed_off["expect_reply"] is False and handed_off["ticket_id"]
    assert (await _session(db_session, "route-human")).state == VoiceCallState.ESCALATED


# --- 8. contracts ------------------------------------------------------------------------------------------------------------------------------------


async def test_the_gateway_contract_is_unchanged_by_the_new_step(make_call, client, db_session):
    schemas = __import__("app.main", fromlist=["app"]).app.openapi()["components"]["schemas"]
    assert set(schemas["StartCallRequest"]["properties"]) == {"call_id", "from_number", "to_number"}
    assert set(schemas["TurnRequest"]["properties"]) == {"call_id", "utterance", "confidence", "turn_id"}
    started = await make_call("route-contract").start()
    assert set(started) == {"lines", "expect_reply", "ticket_id", "stt_prompt"}
    plain = await client.post(f"{SIP}/turn", json={"call_id": "route-contract", "utterance": "I'm a patient"}, headers=AUTH)
    assert plain.status_code == 200 and set(plain.json()) == {"lines", "expect_reply", "ticket_id", "stt_prompt"}


@pytest.mark.parametrize("state", ["IDENTIFY_CALLER", "PATIENT_INTENT", "PATIENT_DETAILS"])
async def test_the_new_states_have_speech_hints_for_the_recognizer(state):
    assert speech_context.stt_prompt(state)
    assert speech_context.recognition_mode(state, {}) == state

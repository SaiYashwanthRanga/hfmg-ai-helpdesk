"""Quick intake (VOICE_QUICK_INTAKE, CALL_FLOW.md 2.2a).

Asked for after a real call took 19 turns to file one ticket: staff have no time for the agent's questions. An employee
is asked for the problem and when it started, then the name and email as before, and hears a read-back they can correct
before the ticket is filed. Nothing else is asked; what they said is still read for the priority and IT's next step.

Every longer-flow setting is on here, as in production, so a question that leaks through fails these tests.
"""

from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.db.models import Priority, VoiceCallState
from app.voice import caller_routing, nlu, orchestrator, scripts
from tests.test_directory_identity import _said
from tests.test_employee_identity_first import DANA, PROBLEM, UNKNOWN, make_call, sheet  # noqa: F401
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

NOT_ASKED = (
    scripts.EXTENSION_ASK, "department", "get your work done", "stopping you", "anyone else", "laptop, your phone",
    "error message", "best number",
)


@pytest.fixture(autouse=True)
def _quick_intake_as_in_production(monkeypatch):
    for name in ("voice_quick_intake", "voice_confirm_summary", "voice_confirm_callback", "voice_confirm_name",
                 "voice_diagnostic_questions", "voice_ask_extension"):
        monkeypatch.setattr(settings, name, True)


def _problem_heard(call, description=PROBLEM, *, category="Microsoft 365", short_issue="your Outlook", **extras):
    call._say(interpret_description=nlu.TurnResult(
        value=description, confidence="high", unable_to_determine=False, category=category, priority=Priority.MEDIUM,
        extras={"short_issue": short_issue, **extras},
    ))


def _start_heard(call, started="this morning"):
    call._say(interpret_details=nlu.TurnResult(value=started, unable_to_determine=False, extras={"started": started}))


async def _agent_lines(db_session, call):
    session = await _session(db_session, call.call_id)
    return " ".join(turn["text"] for turn in session.turns if turn["role"] != "caller")


async def _to_the_read_back(call, description=PROBLEM, **kwargs):
    """An unknown number: the problem, when it started, the last name and the email on file."""
    await call.start()
    assert scripts.EMPLOYEE_PROBLEM_ASK_PLAIN in _said(await call.turn("I'm an employee."))
    _problem_heard(call, description, **kwargs)
    out = await call.turn(description)
    assert _said(out).endswith(scripts.DETAILS_ASK_STARTED)
    _start_heard(call)
    assert scripts.LAST_NAME_ASK in _said(await call.turn("This morning."))
    call._say(interpret_name=nlu.TurnResult(value="Sample", confidence="high", unable_to_determine=False))
    await call.turn("Sample")
    assert scripts.spoken_email("dana.sample@hfmg.net") in _said(await call.turn("Yes."))
    return await call.turn("Yes.")


async def test_an_unknown_number_gives_the_problem_then_the_name_and_email_and_nothing_else(
    make_call, client, db_session, sent_emails,  # noqa: F811
):
    call = make_call("quick-unknown", UNKNOWN)

    read_back = await _to_the_read_back(call)

    said = _said(read_back)
    assert scripts.SUMMARY_PROBLEM.format(problem=PROBLEM) in said
    assert "It started this morning." in said
    assert scripts.spoken_phone_number(UNKNOWN) in said
    assert (await _session(db_session, call.call_id)).state == VoiceCallState.CONFIRM_SUMMARY

    filed = await call.turn("Yes.")

    assert filed["ticket_id"] is not None
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["caller_name"] == "Dana Sample" and ticket["email"] == "dana.sample@hfmg.net"
    assert "Suggested next step" in ticket["description"] and "Not established" not in ticket["description"]
    session = await _session(db_session, call.call_id)
    assert session.collected["callback_confirmed"] is True
    assert session.collected["callback_number"] == session.collected["phone_number"]
    lines = (await _agent_lines(db_session, call)).lower()
    assert not [question for question in NOT_ASKED if question.lower() in lines]


async def test_a_known_number_confirms_the_name_and_email_then_gives_the_problem(make_call, client, db_session):
    call = make_call("quick-known", DANA)
    await call.start()
    await call.turn("I'm an employee.")
    await call.turn("Yes.")
    assert _said(await call.turn("Yes.")) == scripts.EMPLOYEE_PROBLEM_ASK.format(first_name="Dana")
    _problem_heard(call)
    assert _said(await call.turn(PROBLEM)).endswith(scripts.DETAILS_ASK_STARTED)
    _start_heard(call)

    read_back = await call.turn("This morning.")

    assert scripts.SUMMARY_PROBLEM.format(problem=PROBLEM) in _said(read_back)
    filed = await call.turn("Yes.")
    assert (await _ticket(client, filed["ticket_id"]))["caller_name"] == "Dana Sample"
    lines = (await _agent_lines(db_session, call)).lower()
    assert not [question for question in NOT_ASKED if question.lower() in lines]


async def test_a_start_time_already_said_is_not_asked_for(make_call):
    call = make_call("quick-volunteered", UNKNOWN)
    await call.start()
    await call.turn("I'm an employee.")
    _problem_heard(call, started="today")

    out = await call.turn(PROBLEM)

    assert scripts.LAST_NAME_ASK in _said(out) and scripts.DETAILS_ASK_STARTED not in _said(out)


async def _name_said_with_the_problem(call, name):
    await call.start()
    await call.turn("I'm an employee.")
    _problem_heard(call, started="today", caller_name=name, name_confidence="high")
    return await call.turn(f"This is {name}. {PROBLEM}")


async def test_a_name_said_with_the_problem_is_found_and_confirmed_with_the_caller(make_call, client):
    call = make_call("quick-name-said", UNKNOWN)

    out = await _name_said_with_the_problem(call, "Dana Sample")

    assert scripts.IDENTITY_CONFIRM_BY_LAST_NAME.format(name="Dana Sample") in _said(out)
    assert scripts.EMAIL_DIRECTORY_CONFIRM.format(email=scripts.spoken_email("dana.sample@hfmg.net")) in _said(
        await call.turn("Yes.")
    )
    read_back = await call.turn("Yes.")
    assert scripts.SUMMARY_PROBLEM.format(problem=PROBLEM) in _said(read_back)
    filed = await call.turn("Yes.")
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["caller_name"] == "Dana Sample" and ticket["email"] == "dana.sample@hfmg.net"
    assert "Identity: directory entry confirmed by the caller; not authenticated" in ticket["description"]


async def test_no_to_the_person_found_takes_the_name_and_lets_the_email_be_skipped(make_call, client, db_session):
    call = make_call("quick-not-them", UNKNOWN)
    await _name_said_with_the_problem(call, "Dana Sample")

    asked = await call.turn("No.")

    assert scripts.NAME_ONLY_ASK in _said(asked) and "department" not in _said(asked).lower()
    assert (await _session(db_session, call.call_id)).collected.get("identity_confirmed") in (None, False)


async def test_a_name_the_directory_does_not_have_is_kept_and_the_email_can_be_skipped(make_call, client, db_session):
    call = make_call("quick-not-found", UNKNOWN)

    out = await _name_said_with_the_problem(call, "Alex Zzyzx")

    assert scripts.LAST_NAME_ASK not in _said(out) and scripts.spelled_name("Alex Zzyzx") in _said(out)  # read back
    asked_email = await call.turn("Yes.")
    assert "skip" in _said(asked_email).lower() and scripts.spoken_email("dana.sample@hfmg.net") not in _said(asked_email)
    assert "Here's the problem as I heard it" in _said(await call.turn("Skip."))
    filed = await call.turn("Yes.")
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["caller_name"] == "Alex Zzyzx" and not ticket["email"]
    assert "not found in the directory" in ticket["description"]


async def test_the_last_name_not_found_asks_only_for_the_name_then_the_email_can_be_skipped(make_call, client):
    call = make_call("quick-last-not-found", UNKNOWN)
    await call.start()
    await call.turn("I'm an employee.")
    _problem_heard(call, started="today")
    await call.turn(PROBLEM)
    call._say(interpret_name=nlu.TurnResult(value="Zzyzx", confidence="high", unable_to_determine=False))

    asked = await call.turn("Zzyzx")

    assert scripts.NAME_ONLY_ASK in _said(asked)
    call._say(interpret_name=nlu.TurnResult(value="Alex Zzyzx", confidence="high", unable_to_determine=False))
    assert scripts.spelled_name("Alex Zzyzx") in _said(await call.turn("Alex Zzyzx"))  # read back, then the email
    assert "skip" in _said(await call.turn("Yes.")).lower()
    await call.turn("Skip.")
    filed = await call.turn("Yes.")
    assert (await _ticket(client, filed["ticket_id"]))["caller_name"] == "Alex Zzyzx"


async def test_a_name_several_people_share_goes_to_the_last_name_question(make_call):
    call = make_call("quick-several", UNKNOWN)

    out = await _name_said_with_the_problem(call, "Pat")

    assert scripts.LAST_NAME_ASK in _said(out)


async def test_a_known_number_is_still_confirmed_when_a_name_comes_with_the_problem(make_call, db_session):
    call = make_call("quick-known-name", DANA)
    await call.start()
    words = f"I'm an employee, this is Alex Other. {PROBLEM}"
    _problem_heard(call, started="today", caller_name="Alex Other", name_confidence="high")

    out = await call.turn(words)

    assert scripts.IDENTITY_CONFIRM.format(name="Dana Sample") in _said(out)
    assert (await _session(db_session, call.call_id)).collected.get("caller_name") is None


async def test_a_correction_at_the_read_back_changes_what_is_filed_and_what_it_is_taken_for(make_call, client):
    call = make_call("quick-corrected", UNKNOWN)
    await _to_the_read_back(call)
    corrected = "The front desk printer is jammed."
    call._say(interpret_summary_correction=nlu.TurnResult(unable_to_determine=False, extras={"changes": {
        "description": corrected, "short_issue": "the front desk printer", "category": "Printer",
    }}))

    again = await call.turn("No, it's the front desk printer, it's jammed.")

    assert scripts.SUMMARY_PROBLEM.format(problem=corrected) in _said(again)
    filed = await call.turn("Yes.")
    description = (await _ticket(client, filed["ticket_id"]))["description"]
    assert corrected in description and "Outlook / email" not in description


async def test_a_number_corrected_at_the_read_back_is_the_one_used(make_call, db_session):
    call = make_call("quick-number", UNKNOWN)
    await _to_the_read_back(call)
    mobile = "+18455550477"
    call._say(interpret_summary_correction=nlu.TurnResult(unable_to_determine=False, extras={"changes": {
        "phone_number": mobile,
    }}))

    again = await call.turn("Call my mobile instead, 845 555 0477.")

    assert scripts.spoken_phone_number(mobile) in _said(again)
    assert (await _session(db_session, call.call_id)).state == VoiceCallState.CONFIRM_SUMMARY
    await call.turn("Yes.")
    session = await _session(db_session, call.call_id)
    assert session.collected["callback_number"] == mobile


async def test_something_urgent_is_still_noticed_from_the_words_and_still_read_back(make_call, db_session):
    call = make_call("quick-urgent", UNKNOWN)
    await call.start()
    await call.turn("I'm an employee.")
    words = "I clicked a link in a phishing email and my account was hacked."
    _problem_heard(call, words, category="Other", short_issue="your account")

    out = await call.turn(words)

    assert scripts.DETAILS_ASK_STARTED not in _said(out) and scripts.LAST_NAME_ASK in _said(out)
    call._say(interpret_name=nlu.TurnResult(value="Sample", confidence="high", unable_to_determine=False))
    await call.turn("Sample")
    await call.turn("Yes.")
    read_back = await call.turn("Yes.")

    session = await _session(db_session, call.call_id)
    assert session.state == VoiceCallState.CONFIRM_SUMMARY and read_back["ticket_id"] is None
    assert session.collected["priority"] != Priority.MEDIUM.value


@pytest.mark.parametrize("caller_type", ["PATIENT_SUPPORT", caller_routing.PROVIDER])
async def test_a_patient_or_a_provider_still_confirms_the_callback_number(caller_type):
    session = SimpleNamespace(collected={"caller_type": caller_type, "phone_number": "+18455550499"})

    assert orchestrator._quick_intake(session) is False
    assert orchestrator._callback_number_pending(session) is True


async def test_the_read_back_shortens_a_long_problem_and_never_fails_on_one():
    first = "Outlook will not open on the desktop."
    assert orchestrator._problem_as_heard(f"{first} {'More detail follows here. ' * 20}") == first
    assert orchestrator._problem_as_heard("x" * 400) is None
    assert orchestrator._problem_as_heard("." * 400) is None
    assert orchestrator._problem_as_heard(None) is None
    assert orchestrator._problem_as_heard("Printer jammed") == "Printer jammed."

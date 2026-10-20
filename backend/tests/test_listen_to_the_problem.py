"""The agent lets the caller finish describing the problem.

A short pause mid-sentence ends the caller's turn at the SIP gateway ("My appointment was moved and..."). Instead of
taking half a problem and moving on, the agent says "Go on, I'm listening." and joins what follows -- for patients and
employees alike, at most twice, and silence after "go on" takes what was said. Real /start and /turn routes; the
language model is stubbed only for the employee description and the name. Synthetic callers only.
"""

import pytest

from app.db.models import VoiceCallState
from app.voice import nlu, scripts
from tests.test_caller_routing import (  # noqa: F401  (fixtures)
    _ask_who_is_calling, _patient_categories, _say_name, _tickets, it_model_off_limits, make_call,
)
from tests.test_directory_identity import _said
from tests.test_multi_issue_calls import _session
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")


# --- the rule ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("said", [
    "My appointment is on Tuesday and", "I cannot log in because", "I need to...", "I'm having a problem with my",
    "The portal shows an error,", "Outlook keeps crashing when",
])
async def test_a_sentence_cut_off_mid_way_sounds_unfinished(said):
    assert nlu.sounds_unfinished(said)


@pytest.mark.parametrize("said", [
    "I need to change my appointment.", "Outlook keeps crashing", "My printer is jammed", "Yes.", "Hello?", "",
    "I can't log in to the patient portal", "um", "Yesterday it was working fine, but today I can't turn it on.",
])
async def test_a_finished_sentence_does_not(said):
    assert not nlu.sounds_unfinished(said)


# --- patients ---------------------------------------------------------------------------------------------------------


async def _patient_problem(call):
    await call.start()
    await call.turn("I'm a patient")


async def test_a_patient_who_pauses_mid_sentence_is_asked_to_go_on_and_the_whole_problem_is_kept(
    make_call, db_session, sent_emails, it_model_off_limits
):
    call = make_call("listen-patient")
    await _patient_problem(call)

    first = await call.turn("I got a letter about my bill and")
    second = await call.turn("the amount is twice what my insurance said I owe")

    assert _said(first) == scripts.PROBLEM_GO_ON and first["expect_reply"]
    assert _said(second) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"
    await _say_name(call, "Maria Lopez")
    filed = await call.turn("Yes.")
    assert filed["ticket_id"]
    (ticket,) = await _tickets(db_session)
    assert "I got a letter about my bill and the amount is twice what my insurance said I owe" in ticket.description


async def test_silence_after_go_on_takes_what_the_patient_said(make_call, db_session, it_model_off_limits):
    call = make_call("listen-patient-silent")
    await _patient_problem(call)
    await call.turn("I need help with my prescription because")

    out = await call.turn("")

    assert _said(out) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"
    session = await _session(db_session, "listen-patient-silent")
    assert "I need help with my prescription because" in session.collected["description"]
    assert not session.collected.get("problem_partial")


async def test_that_is_all_after_go_on_is_not_added_to_the_problem(make_call, db_session, it_model_off_limits):
    call = make_call("listen-patient-done")
    await _patient_problem(call)
    await call.turn("My test results are not showing in the portal and")

    out = await call.turn("That's it.")

    assert _said(out) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"
    description = (await _session(db_session, "listen-patient-done")).collected["description"]
    assert "showing in the portal and" in description and "That's it" not in description


async def test_go_on_is_said_at_most_twice(make_call, db_session, it_model_off_limits):
    call = make_call("listen-patient-bound")
    await _patient_problem(call)

    one = await call.turn("I need to")
    two = await call.turn("ask a question about my")
    three = await call.turn("account and")

    assert _said(one) == _said(two) == scripts.PROBLEM_GO_ON
    assert _said(three) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"
    description = (await _session(db_session, "listen-patient-bound")).collected["description"]
    assert "I need to ask a question about my account and" in description


async def test_a_finished_problem_is_never_interrupted(make_call, db_session, it_model_off_limits):
    call = make_call("listen-patient-whole")
    await _patient_problem(call)

    out = await call.turn("I can't log in to the patient portal")

    assert _said(out) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"


async def test_a_very_long_answer_cut_by_the_gateway_limit_is_continued(make_call, db_session, it_model_off_limits):
    call = make_call("listen-patient-long")
    await _patient_problem(call)
    long_answer = " ".join(["I called last week about my bill and nobody called me back"] * 5)

    out = await call.turn(long_answer)
    rest = await call.turn("so I would like someone to explain the charges.")

    assert _said(out) == scripts.PROBLEM_GO_ON
    assert _said(rest) == f"{scripts.PATIENT_PROBLEM_NOTED} {scripts.PATIENT_NAME_ASK}"
    assert "explain the charges" in (await _session(db_session, "listen-patient-long")).collected["description"]


async def test_a_patient_asking_for_a_person_mid_sentence_is_not_told_to_go_on(make_call, db_session, it_model_off_limits):
    call = make_call("listen-patient-human")
    await _patient_problem(call)

    out = await call.turn("I want to speak to a real person because")

    assert _said(out) != scripts.PROBLEM_GO_ON


# --- employees --------------------------------------------------------------------------------------------------------


async def _employee_problem(call):
    await call.start()
    await call.turn("I'm an employee.")
    call.classify_as("INTERNAL_IT", 0.95)


async def test_an_employee_who_pauses_mid_sentence_is_asked_to_go_on_and_the_whole_problem_is_kept(
    make_call, db_session
):
    call = make_call("listen-employee")
    await _employee_problem(call)

    first = await call.turn("Outlook keeps crashing when")
    whole = "Outlook keeps crashing when I open an attachment from a patient referral"
    call._say(interpret_description=nlu.TurnResult(
        value=whole, confidence="high", unable_to_determine=False, category="Other",
        extras={"short_issue": "Outlook crashing", "started": "this morning", "work_blocked": False},
    ))
    second = await call.turn("I open an attachment from a patient referral")

    assert _said(first) == scripts.PROBLEM_GO_ON and first["expect_reply"]
    assert _said(second) != scripts.PROBLEM_GO_ON
    session = await _session(db_session, "listen-employee")
    assert session.state != VoiceCallState.COLLECT_DESCRIPTION
    assert session.collected["problem_words"] == whole
    assert not session.collected.get("problem_partial")


async def test_silence_after_go_on_takes_what_the_employee_said(make_call, db_session):
    call = make_call("listen-employee-silent")
    await _employee_problem(call)
    await call.turn("I cannot print to the front desk printer because")
    call._say(interpret_description=nlu.TurnResult(
        value="Cannot print to the front desk printer", confidence="high", unable_to_determine=False,
        category="Other", extras={"short_issue": "the front desk printer", "started": "today", "work_blocked": True},
    ))

    out = await call.turn("")

    assert _said(out) != scripts.PROBLEM_GO_ON
    session = await _session(db_session, "listen-employee-silent")
    assert session.collected["problem_words"] == "I cannot print to the front desk printer because"
    assert session.collected.get("description")

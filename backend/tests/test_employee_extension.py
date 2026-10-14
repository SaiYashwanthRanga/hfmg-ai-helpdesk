"""Employee calls: identity from the directory, the office extension asked once, and no model calls for
answers code can read (the extension, a one-word department).

Real /start and /turn routes; the language model is stubbed where the flow needs it and made to fail where
a turn must not use it. Directory entries and numbers are synthetic.
"""

import asyncio

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket, VoiceCallState
from app.main import app
from app.services.user_directory import UserDirectoryService
from app.voice import nlu, orchestrator, scripts
from tests.test_directory_identity import AUTH, SIP, DirCall
from tests.test_multi_issue_calls import _async, _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
DANA = "+18455550301"     # in the directory, extension 3001
NORA = "+18455550302"     # in the directory, no extension
STRANGER = "+18455550399"  # not in the directory
ASK = "Do you have an office extension number?"


@pytest.fixture(autouse=True)
async def _employee_flow(monkeypatch, db_session, tmp_path):
    monkeypatch.setattr(settings, "voice_ask_caller_type", True)
    monkeypatch.setattr(settings, "voice_ask_extension", True)
    for name in ("Microsoft 365", "Other", "Appointment Booking", "Other Patient Support"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, [
        row("Dana Sample", phone="(845) 555-0301", ext="3001", email="dana.sample@example.org"),
        row("Nora Noext", phone="(845) 555-0302", ext="", email="nora.noext@example.org"),
        row("Lee Other", phone="(845) 555-0303", ext="4004", email="lee.other@example.org"),
    ]))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):
    def make(call_id, number=STRANGER):
        return DirCall(client, monkeypatch, call_id, number)

    return make


@pytest.fixture
def no_model(monkeypatch):
    """Every model-backed interpreter raises: a turn that needs one fails the test."""
    def install():
        async def forbidden(*args, **kwargs):
            raise AssertionError("this turn must not call the language model")

        for name in ("interpret_name", "interpret_email", "interpret_phone", "interpret_yes_no", "interpret_details",
                     "interpret_description", "classify_caller_type"):
            monkeypatch.setattr(nlu, name, forbidden)

    return install


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


async def _to_extension_question(call):
    """An employee the directory does not know, up to the extension question (caller ID gives the number)."""
    await call.start()
    await call.turn("I'm an employee")
    await call.report()
    return await call.name_and_department("Pat Real")


async def _file(call):
    """Answer what is left (email: skip) and return the filing turn."""
    out = await call.turn("skip")
    while not out["ticket_id"] and out["expect_reply"]:
        out = await call.turn("skip")
    return out


async def _intake(client, ticket_id) -> str:
    return (await _ticket(client, ticket_id))["description"]


# --- 1. asked once, only when needed -------------------------------------------------------------------------------


async def test_an_employee_without_a_known_extension_is_asked_once(make_call, db_session):
    call = make_call("ext-ask")

    asked = await _to_extension_question(call)

    assert _text(asked) == ASK
    assert (await _session(db_session, "ext-ask")).state == VoiceCallState.COLLECT_EXTENSION


async def test_a_verified_directory_extension_is_reused_and_never_asked_for(make_call, client, db_session):
    call = make_call("ext-verified", DANA)
    await call.start()
    await call.turn("I'm an employee")
    await call.report()
    await call.turn("yes")  # "Am I speaking with Dana Sample?"
    heard = [_text(await call.turn("Billing"))]
    out = await call.turn("yes")  # the directory email on file
    heard.append(_text(out))
    while not out["ticket_id"]:
        out = await call.turn("yes")
        heard.append(_text(out))

    assert all("extension" not in line.lower() for line in heard)
    session = await _session(db_session, "ext-verified")
    assert (session.collected["extension"], session.collected["extension_source"]) == ("3001", "directory")
    assert "Extension: 3001\n" in await _intake(client, out["ticket_id"])


async def test_a_confirmed_entry_without_an_extension_is_asked(make_call):
    call = make_call("ext-noext", NORA)
    await call.start()
    await call.turn("I'm an employee")
    await call.report()
    await call.turn("yes")  # "Am I speaking with Nora Noext?"

    asked = await call.turn("Billing")

    assert _text(asked) == ASK


async def test_patients_are_never_asked_for_an_extension(make_call):
    call = make_call("ext-patient")
    await call.start()
    heard = []
    for said in ("I'm a patient", "I need to book an appointment", "next Tuesday morning"):
        heard.append(_text(await call.turn(said)))
    call._say(interpret_name=nlu.TurnResult(value="John Smith", confidence="high", unable_to_determine=False))
    heard.append(_text(await call.turn("John Smith")))
    heard.append(_text(await call.turn("yes")))

    assert all("extension" not in line.lower() for line in heard)


async def test_the_setting_switches_the_question_off(make_call, monkeypatch):
    monkeypatch.setattr(settings, "voice_ask_extension", False)
    call = make_call("ext-off")

    after_name = await _to_extension_question(call)

    assert "extension" not in _text(after_name).lower()


async def test_a_second_issue_on_the_same_call_does_not_ask_again(make_call, monkeypatch):
    call = make_call("ext-second")
    await _to_extension_question(call)
    await call.turn("1 0 0 4")
    await _file(call)
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=True, unable_to_determine=False))
    await call.turn("yes")  # anything else?

    after = await call.report("The printer is jammed too.")

    assert ASK not in _text(after)


# --- 2. reading the answer, in code ------------------------------------------------------------------------------


@pytest.mark.parametrize("said, extension", [
    ("1 0 0 4", "1004"), ("1004", "1004"), ("It's extension 2001.", "2001"), ("two zero zero one", "2001"),
    ("Oh, it's one zero zero four", "1004"), ("Yes, I have one, it's 2 0 0 1", "2001"),
    ("double one five", "115"), ("x4410", "4410"),
])
async def test_an_extension_is_read_from_the_answer_without_the_model(said, extension, make_call, db_session, no_model):
    call = make_call(f"ext-read-{extension}-{len(said)}")
    await _to_extension_question(call)
    no_model()

    out = await call.turn(said)

    assert _text(out).startswith(scripts.EXTENSION_NOTED.format(extension=" ".join(extension)))
    session = await _session(db_session, call.call_id)
    assert (session.collected["extension"], session.collected["extension_source"]) == (extension, "caller")
    assert session.collected["phone_number"] == STRANGER  # never touched
    assert session.state == VoiceCallState.COLLECT_EMAIL


@pytest.mark.parametrize("said", ["I don't have one.", "No.", "Nope, no extension.", "I don't know it", "skip"])
async def test_no_extension_is_accepted_without_the_model(said, make_call, client, db_session, no_model):
    call = make_call(f"ext-none-{len(said)}")
    await _to_extension_question(call)
    no_model()

    out = await call.turn(said)

    assert _text(out).startswith(scripts.EXTENSION_NONE)
    session = await _session(db_session, call.call_id)
    assert session.collected["extension"] is None and session.collected["extension_source"] == "none"
    assert session.misunderstanding_count == 0


async def test_a_bare_yes_asks_for_the_number_once(make_call, db_session, no_model):
    call = make_call("ext-yes")
    await _to_extension_question(call)
    no_model()

    what = await call.turn("Yes, I do.")
    out = await call.turn("2 0 0 1")

    assert _text(what) == scripts.EXTENSION_WHAT
    assert (await _session(db_session, "ext-yes")).collected["extension"] == "2001"
    assert _text(out).startswith("Got it, extension 2 0 0 1.")


async def test_a_phone_number_is_never_taken_as_the_extension_or_the_callback_number(make_call, client, db_session, no_model):
    call = make_call("ext-phone")
    await _to_extension_question(call)
    no_model()

    clarify = await call.turn("8 4 5 5 5 5 0 1 9 9")
    assert _text(clarify) == scripts.EXTENSION_NOT_A_PHONE
    out = await call.turn("8 4 5 5 5 5 0 1 9 9")  # said again: the call moves on without one

    session = await _session(db_session, "ext-phone")
    assert session.collected["extension"] is None and session.collected["extension_source"] == "unclear"
    assert session.collected["phone_number"] == STRANGER
    assert "extension" not in _text(out).lower()
    filed = await _file(call)
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["phone_number"] == STRANGER and "8455550199" not in ticket["phone_number"]
    assert "Extension: not captured" in ticket["description"]


async def test_silence_is_asked_about_once_then_the_call_moves_on(make_call, db_session):
    call = make_call("ext-silence")
    await _to_extension_question(call)

    again = await call.turn("")
    moved_on = await call.turn("")

    assert _text(again) == scripts.EXTENSION_RETRY
    session = await _session(db_session, "ext-silence")
    assert session.collected["extension_source"] == "unclear" and session.misunderstanding_count == 0
    assert session.state == VoiceCallState.COLLECT_EMAIL and moved_on["expect_reply"] is True


# --- 3. what goes on the ticket, and the directory -----------------------------------------------------------------


async def test_the_ticket_says_where_the_extension_came_from(make_call, client):
    call = make_call("ext-ticket")
    await _to_extension_question(call)
    await call.turn("1 0 0 4")

    filed = await _file(call)

    assert "Extension: 1004 (given by the caller; not found in the directory)" in await _intake(client, filed["ticket_id"])


async def test_another_employees_extension_is_noted_never_named_and_never_changes_who_is_calling(make_call, client, db_session):
    call = make_call("ext-other")
    await _to_extension_question(call)  # caller said they are Pat Real

    out = await call.turn("4 0 0 4")  # Lee Other's extension in the directory

    assert "Lee" not in _text(out)
    session = await _session(db_session, "ext-other")
    assert session.collected["caller_name"] == "Pat Real" and not session.collected.get("identity_confirmed")
    filed = await _file(call)
    description = await _intake(client, filed["ticket_id"])
    assert "Extension: 4004 (given by the caller; the directory lists this extension for a different entry)" in description
    assert "Lee" not in description


async def test_an_extension_matching_the_callers_own_entry_is_noted_as_such(make_call, client):
    call = make_call("ext-same")
    await call.start()
    await call.turn("I'm an employee")
    await call.report()
    await call.name_and_department("Lee Other")  # unknown caller ID: no identity proposal, the name is asked
    await call.turn("4 0 0 4")

    filed = await _file(call)

    assert "Extension: 4004 (given by the caller; matches the directory entry for this caller's name)" in (
        await _intake(client, filed["ticket_id"])
    )


async def test_saying_no_extension_is_on_the_ticket(make_call, client):
    call = make_call("ext-none-ticket")
    await _to_extension_question(call)
    await call.turn("I don't have one")

    filed = await _file(call)

    assert "Extension: none (the caller has no office extension)" in await _intake(client, filed["ticket_id"])


# --- 4. interruptions --------------------------------------------------------------------------------------------


async def test_asking_for_a_person_at_the_extension_question_escalates(make_call, db_session):
    call = make_call("ext-human")
    await _to_extension_question(call)

    out = await call.turn("Can I just talk to a real person?")

    assert out["expect_reply"] is False and out["ticket_id"]
    assert (await _session(db_session, "ext-human")).state == VoiceCallState.ESCALATED


async def test_saying_goodbye_at_the_extension_question_saves_what_was_collected(make_call, client, db_session):
    call = make_call("ext-bye")
    await _to_extension_question(call)

    out = await call.turn("Okay, thanks, bye.")

    assert out["ticket_id"] and out["expect_reply"] is False
    assert "Your ticket number is" in _text(out)
    assert (await _session(db_session, "ext-bye")).state == VoiceCallState.COMPLETED


# --- 5. a one-word department needs no model call ----------------------------------------------------------------


@pytest.mark.parametrize("said, department", [("Billing.", "Billing"), ("the IT department", "IT"), ("HR", "Human Resources")])
async def test_a_one_word_department_is_matched_in_code(said, department, make_call, db_session, no_model):
    call = make_call(f"dept-{department}", DANA)
    await call.start()
    await call.turn("I'm an employee")
    await call.report()
    await call.turn("yes")  # identity confirmed: only the department is asked
    no_model()

    await call.turn(said)

    session = await _session(db_session, call.call_id)
    assert (session.collected["department"], session.collected["department_verified"]) == (department, True)


async def test_a_department_answer_code_cannot_read_still_goes_to_the_model(make_call, db_session):
    call = make_call("dept-model", DANA)
    await call.start()
    await call.turn("I'm an employee")
    await call.report()
    await call.turn("yes")
    calls = []

    async def interpret_name(utterance, **kwargs):
        calls.append(utterance)
        return nlu.TurnResult(value=None, unable_to_determine=False, extras={"department": "Radiology"})

    call.monkeypatch.setattr(nlu, "interpret_name", interpret_name)

    await call.turn("Oh I work over in the imaging place by the lab, you know")

    assert calls == ["Oh I work over in the imaging place by the lab, you know"]
    assert (await _session(db_session, "dept-model")).collected["department"] == "Radiology"


async def test_a_failed_model_call_for_the_department_does_not_stall_the_call(make_call, db_session):
    call = make_call("dept-fail", DANA)
    await call.start()
    await call.turn("I'm an employee")
    await call.report()
    await call.turn("yes")
    call._say(interpret_name=nlu.TurnResult())  # the model failed or timed out

    out = await call.turn("Mm, the place upstairs")

    assert out["expect_reply"] is True and out["ticket_id"] is None
    session = await _session(db_session, "dept-fail")
    assert session.state != VoiceCallState.COLLECT_NAME  # the department is asked once, then the call moves on


# --- 6. retries, duplicates and concurrency ------------------------------------------------------------------------


async def test_a_retried_extension_turn_is_replayed_not_run_twice(make_call, client, db_session):
    call = make_call("ext-retry")
    await _to_extension_question(call)
    body = {"call_id": "ext-retry", "utterance": "1 0 0 4", "turn_id": "turn-ext-1"}

    first = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()
    again = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()

    assert again["lines"] == first["lines"]
    session = await _session(db_session, "ext-retry")
    assert session.collected["extension"] == "1004" and session.state == VoiceCallState.COLLECT_EMAIL


async def test_filing_twice_never_makes_a_second_ticket(make_call, client, db_session):
    call = make_call("ext-dupe")
    await _to_extension_question(call)
    await call.turn("1 0 0 4")
    body = {"call_id": "ext-dupe", "utterance": "skip", "turn_id": "turn-file-1"}

    first = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()
    second = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()

    assert first["ticket_id"] and second["ticket_id"] == first["ticket_id"]
    db_session.expire_all()
    count = (await db_session.execute(select(func.count()).select_from(Ticket))).scalar_one()
    assert count == 1


async def test_simultaneous_calls_keep_their_own_extensions(make_call, db_session):
    first, second = make_call("ext-c1"), make_call("ext-c2")
    await asyncio.gather(_to_extension_question(first), _to_extension_question(second))

    await asyncio.gather(first.turn("1 0 0 4"), second.turn("I don't have one"))

    one, two = await _session(db_session, "ext-c1"), await _session(db_session, "ext-c2")
    assert (one.collected["extension"], two.collected["extension"]) == ("1004", None)
    assert two.collected["extension_source"] == "none"


# --- 7. success is announced only for a ticket that exists -----------------------------------------------------------


async def test_a_ticket_that_cannot_be_saved_is_never_announced(make_call, client, db_session, monkeypatch):
    call = make_call("ext-fail")
    await _to_extension_question(call)
    await call.turn("1 0 0 4")

    async def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(orchestrator, "_create_ticket", broken)
    out = await call.turn("skip")

    assert out["ticket_id"] is None
    assert "ticket number" not in _text(out).lower()
    assert _text(out) == scripts.SYSTEM_ERROR
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar_one() == 0


async def test_an_extension_turn_holds_no_model_request_in_the_fast_path(make_call, no_model):
    """The extension answer and the reply after it are produced with no model call at all."""
    call = make_call("ext-fast")
    await _to_extension_question(call)
    no_model()

    out = await call.turn("1 0 0 4")

    assert out["expect_reply"] is True and "email" in _text(out).lower()

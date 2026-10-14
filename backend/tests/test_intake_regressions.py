"""Regressions from a real employee call, and late additions.

The call: unclear "patient or employee?" answers; a server running out of space where the caller can no longer
install software but nothing is failing; the server named inside non-English speech; the free-space answer and
the extension answer both came back from the recognizer as "Hello, this is the HFMG IT help desk. How can I
assist you today?" (a prompt hallucination during a silence, not the caller); the caller remembered another
server near the end. Real /start and /turn routes; the language model is stubbed where the flow needs it.
"""

import pytest
from sqlalchemy import func, select

from app.ai import summarizer
from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket, VoiceCallState
from app.voice import diagnostics as dx
from app.voice import nlu, orchestrator, scripts
from tests.test_directory_identity import AUTH, SIP, DirCall
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550399"
HALLUCINATION = "Hello, this is the HFMG IT help desk. How can I assist you today?"
PROBLEM = "The file server is running out of disk space and I can't install any new software."
_REAL_INTERPRET_EMAIL = nlu.interpret_email


@pytest.fixture(autouse=True)
async def _employee_intake(monkeypatch, db_session):
    monkeypatch.setattr(settings, "voice_ask_caller_type", True)
    monkeypatch.setattr(settings, "voice_diagnostic_questions", True)
    monkeypatch.setattr(settings, "voice_ask_extension", True)
    for name in ("Other", "Microsoft 365", "Appointment Booking", "Other Patient Support"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):
    def make(call_id, number=CALLER):
        return DirCall(client, monkeypatch, call_id, number)

    return make


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


def _describe_stub(call, problem=PROBLEM, *, work_blocked=False):
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value=problem, confidence="high", unable_to_determine=False, category="Other", priority=Priority.MEDIUM,
        extras={"short_issue": "the file server", "started": "this morning", "work_blocked": work_blocked,
                "affected_scope": "one_person"},
    ))


async def _server_call_to_paperwork(call, *, server_answer="It's FS01.", free_space="Less than 3 GB."):
    """Employee, the server problem, and its questions answered: the call is at the name question."""
    await call.start()
    await call.turn("I'm an employee")
    _describe_stub(call)
    out = await call.turn(PROBLEM)
    answers = {"Which server": server_answer, "Which drive": "The E drive.",
               "application or service": "No, the existing applications still run, I just can't install new software.",
               "free space": free_space, "space": "About 50 gigabytes."}
    for _ in range(8):
        if (await _session_state(call)) != VoiceCallState.COLLECT_DIAGNOSTICS:
            return out
        said = next((a for k, a in answers.items() if k in _text(out)), "I don't know")
        out = await call.turn(said)
    return out


async def _session_state(call):
    from app.db.base import async_session_factory

    async with async_session_factory() as db:
        return (await _session(db, call.call_id)).state


# --- 1. the call, as it happened ------------------------------------------------------------------------------


async def test_the_reported_call_is_taken_down_correctly(make_call, client, db_session, sent_emails):
    call = make_call("reg-call")
    await call.start()

    # "Patient or employee?" -- unclear twice, then answered. It never loops.
    first = await call.turn("Hello?")
    call.classify_as("INTERNAL_IT", 0.40)  # what a real model says to an answer with no cue: unsure
    second = await call.turn("Uh, hmm, I'm not sure what you mean")
    routed = await call.turn("I'm an employee")
    assert scripts.IDENTIFY_AFTER_HELLO == _text(first) and scripts.IDENTIFY_CLARIFY == _text(second)
    assert "IT problem" in _text(routed)

    _describe_stub(call)
    asked_server = await call.turn(PROBLEM)
    assert "Which server" in _text(asked_server)
    asked_drive = await call.turn("server ka naam FS01 hai")  # the name inside Hindi
    asked_impact = await call.turn("The E drive.")
    assert "application or service" in _text(asked_impact)  # not assumed: "can't install" is not "failing"
    asked_space = await call.turn("No, the existing applications still run, I just can't install new software.")
    assert "free space" in _text(asked_space)

    # The recognizer hears silence and returns its prompt as a "greeting": not an answer.
    clarified = await call.turn(HALLUCINATION)
    assert _text(clarified) == f"{dx.CLARIFY} Do you know how much free space is left?"
    asked_name = await call.turn("Less than 3 GB.")
    assert "Who am I speaking with" in _text(asked_name) or "name and department" in _text(asked_name)

    asked_extension = await call.name_and_department("Pat Real", "Billing")
    assert _text(asked_extension) == scripts.EXTENSION_ASK
    again = await call.turn(HALLUCINATION)  # the same hallucination at the extension question
    assert _text(again) == scripts.EXTENSION_RETRY
    asked_email = await call.turn("No, I don't have one.")
    assert "email" in _text(asked_email).lower()

    # Near the end: "I have another server."
    kind = await call.turn("Oh wait, I have another server too.")
    assert _text(kind) == "Is another server affected as well as FS01, or did I get FS01 wrong?"
    name = await call.turn("It's another one, also affected.")
    assert _text(name) == scripts.LATE_SERVER_NAME
    back = await call.turn("FS02")
    assert _text(back).startswith(scripts.LATE_SERVER_ADDED) and "email" in _text(back).lower()  # same question again

    call.monkeypatch.setattr(nlu, "interpret_email", _REAL_INTERPRET_EMAIL)
    filed = await call.turn("skip")
    assert "Your ticket number is" in _text(filed)

    ticket = await _ticket(client, filed["ticket_id"])
    intake = ticket["description"].split("--- Call transcript ---")[0]
    for expected in ('Server: FS01 (caller said: "server ka naam FS01 hai")', "Additional servers: FS02",
                     "Drive or folder: The E drive.", "Free space now: Less than 3 GB.",
                     'Application failing: no (caller: "No, the existing applications still run',
                     "Department: Billing", "Extension: none (the caller has no office extension)",
                     "Suggested next step: Check free space on FS01 and FS02 (E drive)"):
        assert expected in intake, expected
    assert "help desk" not in intake.lower() and "Escalation" not in intake
    assert ticket["priority"] != "URGENT"
    transcript = ticket["description"].split("--- Call transcript ---")[1]
    assert transcript.count(orchestrator.NOT_CALLER_SPEECH) == 2  # kept, marked, never used
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar_one() == 1
    assert len(sent_emails) == 1


# --- 2. the recognizer's own words are not the caller's ------------------------------------------------------------


@pytest.mark.parametrize("utterance, ignored", [
    (HALLUCINATION, True),
    ("Hello, this is the Horizon IT help desk.", True),
    ("How can I help you today?", True),
    ("Less than 3 GB.", False),
    ("Hello?", False),  # a caller's hello is theirs: handled as a greeting, not dropped
    ("Is this the help desk?", False),
    ("The part before the at sign.", False),
    ("F S 0 1", False),
    ("No, I don't have one.", False),
])
async def test_only_the_agents_or_recognizers_own_words_are_dropped(utterance, ignored):
    from app.db.models import VoiceCallSession

    session = VoiceCallSession(call_id="x", from_number="+1", to_number="+1", state=VoiceCallState.COLLECT_DIAGNOSTICS,
                               collected={}, turns=[{"role": "agent", "text": "Was it the part before the at sign that was wrong, the part after it, or both?"}])
    assert orchestrator._not_caller_speech(session, utterance) is ignored


async def test_the_agents_own_line_coming_back_is_treated_as_silence(make_call, db_session):
    call = make_call("reg-echo")
    out = await _server_call_to_paperwork(call)
    out = await call.name_and_department("Pat Real", "Billing")
    assert _text(out) == scripts.EXTENSION_ASK

    echoed = await call.turn("Do you have an office extension number?")  # the agent's audio leaking back

    assert _text(echoed) == scripts.EXTENSION_RETRY
    session = await _session(db_session, "reg-echo")
    assert session.collected.get("extension_source") is None
    last_caller = [t["text"] for t in session.turns if t["role"] == "caller"][-1]
    assert last_caller.startswith(orchestrator.NOT_CALLER_SPEECH)  # kept on the transcript, marked as not the caller


@pytest.mark.parametrize("problem, failing", [
    ("The file server is full and the app is failing to install new software.", None),
    ("The server is out of space and I can't install anything new.", None),
    ("The file server is full and the billing app is failing to save.", "yes"),
    ("The server ran out of space and the application is failing.", "yes"),
])
async def test_not_being_able_to_install_software_is_not_a_failing_application(problem, failing):
    state = dx.begin("Other", problem, None)

    assert state["values"].get("app_impact") == failing
    assert (dx.urgent_reason(state) == "an application is already failing") is (failing == "yes")


async def test_the_recognizer_prompt_no_longer_contains_a_sentence_to_echo():
    from app.speech import context as speech_context

    for state in ("COLLECT_DIAGNOSTICS", "COLLECT_EXTENSION", "IDENTIFY_CALLER", "SPELL_NAME", "COLLECT_EMAIL"):
        prompt = speech_context.transcription_prompt(state)
        assert "help desk" not in prompt.lower() and "phone call" not in prompt.lower()


# --- 3. every question gives up gracefully -------------------------------------------------------------------------


async def _keeps_asking(call, said, *, limit=4):
    """How many turns of `said` the call stays in the state it was in."""
    start = await _session_state(call)
    for n in range(1, limit + 2):
        out = await call.turn(said)
        if out["expect_reply"] is False or (await _session_state(call)) != start:
            return n
    return limit + 1


@pytest.mark.parametrize("said", ["Hello?", HALLUCINATION, "um"])
async def test_the_patient_or_employee_question_is_bounded(said, make_call):
    call = make_call(f"reg-id-{len(said)}")
    await call.start()

    assert await _keeps_asking(call, said) <= 4


async def test_a_diagnostic_question_is_asked_at_most_twice(make_call):
    call = make_call("reg-dx-bound")
    await call.start()
    await call.turn("I'm an employee")
    _describe_stub(call)
    asked = await call.turn(PROBLEM)
    assert "Which server" in _text(asked)

    again = await call.turn("um")
    moved_on = await call.turn("um")

    assert _text(again) == f"{dx.CLARIFY} Which server is it? The server name, if you know it."
    assert "Which drive" in _text(moved_on)  # the next question, not the same one a third time


async def test_every_diagnostic_question_ends_even_if_nothing_is_ever_answered(make_call):
    call = make_call("reg-dx-silent")
    await call.start()
    await call.turn("I'm an employee")
    _describe_stub(call)
    await call.turn(PROBLEM)

    turns = 0
    while (await _session_state(call)) == VoiceCallState.COLLECT_DIAGNOSTICS and turns < 20:
        await call.turn("")
        turns += 1

    assert turns <= 2 * dx.MAX_QUESTIONS  # each question: once, then once more, then moved on


async def test_the_extension_question_is_bounded(make_call):
    call = make_call("reg-ext-bound")
    await _server_call_to_paperwork(call)
    await call.name_and_department("Pat Real", "Billing")

    assert await _keeps_asking(call, "um") <= 2


# --- 4. late additions: never a restart, nothing lost ---------------------------------------------------------------


async def _details(db_session, call_id):
    collected = (await _session(db_session, call_id)).collected
    return collected, collected["diagnostics"]["values"]


async def test_another_server_named_at_the_extension_question_is_added_and_the_question_resumes(make_call, db_session):
    call = make_call("late-ext")
    await _server_call_to_paperwork(call)
    asked = await call.name_and_department("Pat Real", "Billing")
    assert _text(asked) == scripts.EXTENSION_ASK

    kind = await call.turn("Also there's another server, FS02.")
    back = await call.turn("Both are affected.")

    assert _text(kind) == "Is FS02 another server that's affected as well, or should it replace FS01?"
    assert _text(back) == f"{scripts.LATE_SERVER_ADDED} {scripts.EXTENSION_ASK}"
    collected, values = await _details(db_session, "late-ext")
    assert (await _session(db_session, "late-ext")).state == VoiceCallState.COLLECT_EXTENSION
    assert (values["server"], values["additional_servers"]) == ("FS01", "FS02")
    assert (collected["caller_name"], collected["department"]) == ("Pat Real", "Billing")
    assert values["free_space"] == "Less than 3 GB." and values["drive"] == "The E drive."


async def test_a_correction_at_the_final_read_back_replaces_the_name_and_keeps_the_old_one(make_call, client, db_session, monkeypatch, sent_emails):
    monkeypatch.setattr(settings, "voice_confirm_summary", True)
    call = make_call("late-summary")
    await _server_call_to_paperwork(call)
    await call.name_and_department("Pat Real", "Billing")
    await call.turn("No, I don't have one.")  # extension
    call.monkeypatch.setattr(nlu, "interpret_email", _REAL_INTERPRET_EMAIL)
    read_back = await call.turn("skip")  # email -> the summary is read back
    assert (await _session(db_session, "late-summary")).state == VoiceCallState.CONFIRM_SUMMARY

    kind = await call.turn("Sorry, it's a different server, FS03.")
    back = await call.turn("Replace it, FS01 was wrong.")

    assert "should it replace FS01" in _text(kind)
    assert _text(back) == f"{scripts.LATE_SERVER_CORRECTED} {scripts.SUMMARY_REST_CORRECT}"  # not a restart, nor the whole read-back
    assert read_back["lines"]
    _, values = await _details(db_session, "late-summary")
    assert values["server"] == "FS03" and "FS01" in values["server_corrected_from"]
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=True, unable_to_determine=False))
    filed = await call.turn("Yes, that's right.")
    ticket = await _ticket(client, filed["ticket_id"])
    assert "Server: FS03" in ticket["description"] and "Server (corrected): the caller corrected it from" in ticket["description"]
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar_one() == 1
    assert len(sent_emails) == 1


async def test_another_server_right_after_identity_is_added_without_asking_identity_again(make_call, db_session):
    call = make_call("late-identity")
    await _server_call_to_paperwork(call)
    await call.name_and_department("Pat Real", "Billing")

    kind = await call.turn("I have another server.")
    name = await call.turn("Yes, another one.")
    back = await call.turn("It's FS-BACKUP-02.")

    assert scripts.LATE_SERVER_NAME == _text(name)
    assert "Who am I speaking with" not in _text(back) and "patient or an employee" not in _text(back)
    assert _text(back).endswith(scripts.EXTENSION_ASK)
    collected, values = await _details(db_session, "late-identity")
    assert values["additional_servers"] == "FS-BACKUP-02" and collected["caller_name"] == "Pat Real"
    assert "another server" in _text(kind)


async def test_an_unclear_answer_about_another_server_adds_it_and_never_overwrites(make_call, db_session):
    call = make_call("late-unclear")
    await _server_call_to_paperwork(call)
    await call.name_and_department("Pat Real", "Billing")

    await call.turn("There's another server, FS02.")
    await call.turn("Hmm, I don't know.")

    _, values = await _details(db_session, "late-unclear")
    assert values["server"] == "FS01" and values["additional_servers"] == "FS02"


async def test_a_replayed_late_answer_is_not_added_twice(make_call, client, db_session):
    call = make_call("late-replay")
    await _server_call_to_paperwork(call)
    await call.name_and_department("Pat Real", "Billing")
    await call.turn("There's another server, FS02.")
    body = {"call_id": "late-replay", "utterance": "Both are affected.", "turn_id": "late-1"}

    first = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()
    again = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()

    assert again["lines"] == first["lines"]
    _, values = await _details(db_session, "late-replay")
    assert values["additional_servers"] == "FS02"


async def test_a_patient_mentioning_another_server_is_not_asked_about_servers(make_call, db_session):
    call = make_call("late-patient")
    await call.start()
    await call.turn("I'm a patient")
    out = await call.turn("I need to book an appointment, my other server account is fine")

    assert "server" not in _text(out).lower()
    assert "late_server" not in (await _session(db_session, "late-patient")).collected


# --- 5. names in other languages, and what is not a name ------------------------------------------------------------


@pytest.mark.parametrize("answer, server", [
    ("server ka naam FS01 hai", "FS01"),
    ("सर्वर का नाम FS01 है", "FS01"),
    ("సర్వర్ పేరు HFMG-SQL02", "HFMG-SQL02"),
    ("F S 0 1", "FS01"),
])
async def test_a_server_name_is_taken_out_of_any_language_and_kept_as_said(answer, server, make_call, client, db_session):
    call = make_call(f"host-{abs(hash(answer)) % 10000}")
    await _server_call_to_paperwork(call, server_answer=answer)

    _, values = await _details(db_session, call.call_id)
    assert values["server"] == server and values["server_said"] == answer


async def test_without_a_recognisable_name_the_words_are_kept_and_nothing_is_invented(make_call, db_session):
    call = make_call("host-none")
    await _server_call_to_paperwork(call, server_answer="the big file server in the back")

    _, values = await _details(db_session, "host-none")
    assert values["server"] == "the big file server in the back"


# --- 6. the department, against the configured list --------------------------------------------------------------


async def test_the_department_line_says_verified_unverified_unchecked_or_missing(monkeypatch):
    from app.voice.departments import DEFAULT_DEPARTMENTS

    monkeypatch.setattr(settings, "voice_departments", ",".join(DEFAULT_DEPARTMENTS))
    assert "Department: Billing\n" in orchestrator._intake_details({"department": "Billing", "department_verified": True}) + "\n"
    assert "Department: Space Lab (unverified: not on HFMG's department list)" in orchestrator._intake_details(
        {"department": "Space Lab", "department_verified": False})
    assert "Department: not given" in orchestrator._intake_details({"caller_type": "INTERNAL_IT"})
    assert "Department" not in orchestrator._intake_details({"caller_type": "PATIENT_SUPPORT"})
    monkeypatch.setattr(settings, "voice_departments", "")  # the server has no list configured
    assert "Department: Billing (not checked: no HFMG department list is configured)" in orchestrator._intake_details(
        {"department": "Billing", "department_verified": True})


# --- 7. the summary agrees with what was said ---------------------------------------------------------------------


async def test_the_one_summary_is_written_from_the_final_corrected_details(make_call, client, db_session, monkeypatch, sent_emails):
    prompts = []

    class Provider:
        is_configured = True

        async def structured(self, **kwargs):
            prompts.append(kwargs["user"])
            return {"summary": "File servers FS01 and FS02 are low on space (under 3 GB free); software can't be installed."}

    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: Provider())
    call = make_call("reg-summary")
    await _server_call_to_paperwork(call)
    await call.name_and_department("Pat Real", "Billing")
    await call.turn("There's another server, FS02.")
    await call.turn("Both.")
    await call.turn("No, I don't have one.")
    call.monkeypatch.setattr(nlu, "interpret_email", _REAL_INTERPRET_EMAIL)

    filed = await call.turn("skip")

    assert len(prompts) == 1
    for fact in ("Additional servers: FS02", "Free space now: Less than 3 GB.", "Application failing: no"):
        assert fact in prompts[0], fact
    assert "help desk" not in prompts[0].lower()
    assert (await _ticket(client, filed["ticket_id"]))["priority"] != "URGENT"
    assert len(sent_emails) == 1

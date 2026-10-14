"""Employee calls: issue-specific diagnostic questions before the paperwork. An intake agent, not a troubleshooter.

The question engine is tested directly (choosing a question set, reading answers, choosing the next question,
the ticket section) and through the real /start and /turn routes, with the language model stubbed for the
description and made to fail during the diagnostic questions, which must not use it.
"""

import asyncio
import re

import pytest
from sqlalchemy import func, select

from app.ai import summarizer
from app.core.config import get_settings
from app.db.models import Category, EscalationReason, Priority, Ticket, VoiceCallState
from app.voice import diagnostics as dx
from app.voice import nlu
from tests.test_directory_identity import AUTH, SIP, DirCall
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550399"
#: Anything that asks the caller to do something to fix the problem: an instruction ("please restart", "can you
#: close", "try reconnecting", a sentence that starts with the action) or asking whether a step worked.
_ACTIONS = (r"restart|reboot|reconnect|disconnect|turn|unplug|plug|close|reopen|re-open|sign in|log in|log out|clear|"
            r"reinstall|update|click|press|go to|open|hold|check")
SELF_HELP = re.compile(
    rf"(?:^|[.?!,]\s+|\bplease\s+|\b(?:can|could|would) you\s+|\btry\s+(?:to\s+)?|\band\s+(?:then\s+)?)(?:{_ACTIONS})\w*\b"
    r"|\bdid (?:that|it) (?:fix|help|work)\b",
    re.IGNORECASE,
)


# --- 1. the engine ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("category, description, playbook", [
    ("Microsoft 365", "My Outlook keeps crashing.", "outlook"),
    ("Microsoft 365", "I'm not getting any email since this morning.", "outlook"),
    ("Other", "The file server is running out of disk space.", "server_storage"),
    ("Network", "I can't connect to the VPN from home.", "network"),
    ("Other", "The wifi keeps dropping.", "network"),
    ("Other", "My laptop won't turn on.", "device"),
    ("Password", "I'm locked out of my account.", "account"),
    ("eClinicalWorks", "I can't log in to eClinicalWorks.", "account"),
    ("Printer", "The front desk printer is jammed.", "printer"),
    ("Other", "My keyboard stopped working.", "printer"),
    ("Microsoft 365", "Teams keeps showing me as away.", "other"),
    ("Other", "Something is wrong with the scheduling board.", "other"),
])
async def test_the_question_set_follows_the_problem(category, description, playbook):
    assert dx.choose(category, description).name == playbook


async def test_no_question_asks_the_caller_to_try_anything():
    for playbook in dx.PLAYBOOKS.values():
        for question in playbook.questions:
            assert not SELF_HELP.search(question.prompt), (playbook.name, question.prompt)
    assert not SELF_HELP.search(dx.URGENT_NOTICE)


async def test_what_the_description_already_says_is_not_asked_again():
    state = dx.begin("Microsoft 365", "Outlook crashes on my laptop every time I open it.", None)

    assert state["values"]["device"] == "computer" and state["values"]["symptom"] == "crash"
    assert dx.next_prompt(state)[0] == "error"


async def test_the_next_question_depends_on_the_answers():
    sending = dx.begin("Microsoft 365", "My emails are stuck in the outbox.", "one_person")
    crashing = dx.begin("Microsoft 365", "Outlook crashes on my laptop.", "one_person")
    one_system = dx.begin("Network", "I can't reach one site from the office.", "one_person")
    everything = dx.begin("Network", "No internet at all in the office.", "one_person")

    for state in (sending, crashing):
        state["values"]["device"], state["values"]["error"] = "computer", "none"
    assert dx.next_prompt(sending) is None  # already about sending/receiving: not asked again
    assert dx.next_prompt(crashing)[0] == "mail_flow"
    assert dx.next_prompt(one_system)[0] == "resource"
    assert dx.next_prompt(everything)[0] == "error"


async def test_questions_are_capped():
    state = dx.begin("Other", "My laptop is very slow.", None)
    asked = []
    while (found := dx.next_prompt(state)) is not None:
        state["pending"] = found[0]
        state, *_ = dx.record(state, "I don't know")
        asked.append(found[0])
    assert 0 < len(asked) <= dx.MAX_QUESTIONS


@pytest.mark.parametrize("question_kind, said, expected", [
    ("yes_no", "Yes, it does.", "yes"), ("yes_no", "No.", "no"), ("yes_no", "I'm not sure.", None),
    ("text", "No, there's no error.", "none"), ("text", "It says cannot start Microsoft Outlook.", "It says cannot start Microsoft Outlook."),
])
async def test_answers_are_read_in_code_and_kept_in_the_callers_words(question_kind, said, expected):
    assert dx._parse(dx.Question("q", "Q", "?", question_kind), said) == expected


async def test_a_password_said_on_the_call_never_reaches_the_ticket():
    assert dx.clean("It says wrong password, my password is Winter2026!") == "It says wrong password, my password [removed]"
    assert "Winter2026" not in dx.clean("the code is 123456 and password: Winter2026")


@pytest.mark.parametrize("description, reason, priority", [
    ("I clicked a link in a phishing email and Outlook is acting weird.", "it may be a security incident", Priority.URGENT),
    ("All my files are gone from the shared drive.", "data may have been lost", Priority.HIGH),
    ("The server is out of space and the billing app is failing.", "an application is already failing", Priority.HIGH),
    ("Nobody can print, the whole office is affected.", "it's affecting everyone there", Priority.URGENT),
])
async def test_urgent_signs_are_flagged_and_set_a_priority_floor(description, reason, priority):
    state = dx.begin("Other", description, None)
    assert dx.urgent_reason(state) == reason
    assert dx.priority_floor(state) == (priority, reason)


async def test_an_urgent_problem_gets_only_the_essential_questions():
    server = dx.begin("Other", "The file server is out of disk space and the billing app is failing.", None)
    asked = []
    while (found := dx.next_prompt(server)) is not None:
        server["pending"] = found[0]
        server, *_ = dx.record(server, "E")
        asked.append(found[0])
    assert asked == ["server", "drive"]


async def test_the_ticket_section_names_what_is_missing_and_never_fills_it_in():
    state = dx.begin("Other", "The file server is out of disk space.", None)
    state["pending"] = "server"
    state, *_ = dx.record(state, "It's FS01.")

    lines = dict(dx.ticket_lines(state))
    assert lines["Issue type"] == "Server / storage" and lines["Server"] == "FS01 (caller said: \"It's FS01.\")"
    assert "drive or folder" in lines["Not established"] and "Free space now" not in lines
    assert lines["Suggested next step"].startswith("Check free space on FS01 (drive not given)")


# --- 2. through the call ---------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _diagnostics_on(monkeypatch, db_session):
    monkeypatch.setattr(settings, "voice_ask_caller_type", True)
    monkeypatch.setattr(settings, "voice_diagnostic_questions", True)
    for name in ("Microsoft 365", "Network", "Printer", "Password", "Other", "Appointment Booking", "Other Patient Support"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):
    def make(call_id, number=CALLER):
        return DirCall(client, monkeypatch, call_id, number)

    return make


@pytest.fixture
def no_model(monkeypatch):
    def install():
        async def forbidden(*args, **kwargs):
            raise AssertionError("diagnostic questions must not call the language model")

        for name in ("interpret_name", "interpret_email", "interpret_phone", "interpret_yes_no", "interpret_details",
                     "interpret_description", "classify_caller_type", "interpret_clarification"):
            monkeypatch.setattr(nlu, name, forbidden)

    return install


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


async def _describe(call, problem, category, *, short="your computer", work_blocked=False, department=None, caller_name=None):
    """Open as an employee and describe the problem (when it started and the work impact volunteered)."""
    await call.start()
    await call.turn("I'm an employee")
    call.classify_as("INTERNAL_IT", 0.95)
    extras = {"short_issue": short, "started": "this morning", "work_blocked": work_blocked, "affected_scope": "one_person"}
    if department:
        extras["department"] = department
    if caller_name:
        extras["caller_name"] = caller_name
    call._say(interpret_description=nlu.TurnResult(
        value=problem, confidence="high", unable_to_determine=False, category=category, priority=Priority.MEDIUM,
        extras=extras,
    ))
    return await call.turn(problem)


_REAL_INTERPRET_EMAIL = nlu.interpret_email


async def _finish(call):
    """Name and department, skip the email: the ticket is filed normally (not as a callback after a failure)."""
    call.monkeypatch.setattr(nlu, "interpret_email", _REAL_INTERPRET_EMAIL)
    out = await call.name_and_department("Pat Real")
    while not out["ticket_id"] and out["expect_reply"]:
        out = await call.turn("skip")
    assert "Your ticket number is" in _text(out), _text(out)
    return out


async def test_an_outlook_problem_is_asked_about_and_never_troubleshot(make_call, client, db_session, no_model):
    call = make_call("dx-outlook")
    first = await _describe(call, "Outlook crashes on my laptop every time I open it.", "Microsoft 365", short="your Outlook")
    no_model()

    heard = [_text(first)]
    for said in ("It says Microsoft Outlook has stopped working.", "Yes, email works on my phone.", "No, just me."):
        heard.append(_text(await call.turn(said)))

    assert "error message" in heard[0].lower()  # device and symptom came from the description
    assert "send and receive email" in heard[1]
    assert "anyone else" in heard[2]  # who is affected is asked, never assumed from the model's reading
    assert all(not SELF_HELP.search(h) for h in heard[:3])
    assert (await _session(db_session, "dx-outlook")).state == VoiceCallState.COLLECT_NAME  # paperwork only now
    filed = await _finish(call)
    description = (await _ticket(client, filed["ticket_id"]))["description"]
    for expected in ("Issue type: Outlook / email", "Device: computer (from the problem description)",
                     "Symptom: crash (from the problem description)",
                     "Error message: It says Microsoft Outlook has stopped working.",
                     "Can still send and receive email: yes", "Started: this morning",
                     "Suggested next step: Check the Outlook app on the caller's computer"):
        assert expected in description, expected
    assert "Step tried" not in description


async def test_a_full_server_disk_with_a_failing_application_is_flagged_with_what_it_needs(make_call, client, db_session, no_model):
    call = make_call("dx-server")
    first = await _describe(call, "The file server is out of disk space and the billing app is failing to save.", "Other",
                            short="the file server", work_blocked=True)
    no_model()

    drive = await call.turn("It's FS01.")
    paperwork = await call.turn("The E drive.")

    assert "Which server" in _text(first) and "Which drive" in _text(drive)
    assert _text(paperwork).startswith(dx.URGENT_NOTICE)
    session = await _session(db_session, "dx-server")
    assert session.collected["priority"] in ("HIGH", "URGENT")
    assert "department" not in session.collected or session.collected["department"] is None  # not "Billing"
    assert "which department" in _text(paperwork).lower()
    filed = await _finish(call)
    description = (await _ticket(client, filed["ticket_id"]))["description"]
    for expected in ("Issue type: Server / storage", "Server: FS01 (caller said: \"It's FS01.\")", "Drive or folder: The E drive.",
                     "Application failing: yes (from the problem description)",
                     "Escalation: flagged urgent: an application is already failing",
                     "Not established: free space now, space needed",
                     "Suggested next step: Check free space on FS01 (E drive)"):
        assert expected in description, expected


async def test_storage_details_are_collected_when_nothing_is_failing_yet(make_call, client, db_session):
    call = make_call("dx-space")
    await _describe(call, "The file server is running low on disk space.", "Other", short="the file server")
    for said in ("FS02", "The S drive", "No, everything still works.", "About 5 gigabytes."):
        await call.turn(said)

    session = await _session(db_session, "dx-space")
    values = session.collected["diagnostics"]["values"]
    assert (values["server"], values["drive"], values["app_impact"], values["free_space"]) == (
        "FS02", "The S drive", "no", "About 5 gigabytes.")
    assert session.state == VoiceCallState.COLLECT_NAME  # the cap of four questions: space needed is not asked


async def test_a_possible_security_incident_is_flagged_without_any_instructions(make_call, client, db_session):
    call = make_call("dx-security")

    first = await _describe(call, "I clicked a link in a phishing email and now my account is acting strange.", "Other")

    heard = _text(first)
    assert dx.URGENT_NOTICE in heard and not SELF_HELP.search(heard.replace("Who am I speaking with", ""))
    session = await _session(db_session, "dx-security")
    assert session.state == VoiceCallState.COLLECT_NAME
    assert (session.collected["priority"], session.collected["priority_reason"]) == ("URGENT", "it may be a security incident")
    filed = await _finish(call)
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["priority"] == "URGENT"
    assert "Escalation: flagged urgent: it may be a security incident" in ticket["description"]


async def test_remote_and_office_network_problems_get_different_questions(make_call, db_session):
    remote, office = make_call("dx-vpn"), make_call("dx-lan")

    vpn_first = await _describe(remote, "I can't connect to the VPN from home.", "Network", short="the VPN")
    lan_first = await _describe(office, "From the office I can't reach one site.", "Network", short="the network")

    assert "error message" in _text(vpn_first).lower()
    assert "Which site or system" in _text(lan_first)


async def test_an_account_problem_never_asks_for_a_password_and_strips_one_that_is_said(make_call, client, db_session):
    call = make_call("dx-account")
    first = await _describe(call, "I'm locked out of my account.", "Password", short="your account")
    heard = [_text(first)]
    heard.append(_text(await call.turn("Windows login, my password is Winter2026")))
    heard.append(_text(await call.turn("It says the account is locked.")))
    heard.append(_text(await call.turn("Yes, it worked yesterday.")))

    assert "Please don't tell me your password." in heard[0]
    filed = await _finish(call)
    intake = (await _ticket(client, filed["ticket_id"]))["description"].split("--- Call transcript ---")[0]
    assert "Winter2026" not in intake and "password [removed]" in intake


async def test_an_unknown_kind_of_problem_is_asked_about_not_guessed(make_call):
    call = make_call("dx-other")

    first = await _describe(call, "Something's off with the scheduling board.", "Other", short="the scheduling board")

    assert _text(first).endswith("What exactly happens when you try?")


# --- 3. the department is never guessed from the problem ----------------------------------------------------------


async def test_a_department_word_in_an_application_name_is_not_the_callers_department(make_call, db_session):
    call = make_call("dept-app")
    await _describe(call, "The billing app keeps crashing on my computer.", "Other", short="the billing app", department="Billing")

    session = await _session(db_session, "dept-app")
    assert session.collected.get("department") is None


async def test_a_department_the_caller_states_is_kept(make_call, db_session):
    call = make_call("dept-said")
    await _describe(call, "Hi, I'm in billing and Outlook keeps crashing.", "Microsoft 365", short="your Outlook",
                    department="Billing")

    assert (await _session(db_session, "dept-said")).collected["department"] == "Billing"


@pytest.mark.parametrize("said, department, kept", [
    ("The billing application keeps freezing.", "Billing", False),
    ("The HR portal won't load.", "HR", False),
    ("I work in Billing and Outlook keeps crashing.", "Billing", True),
    ("I'm from the front desk and the printer is jammed.", "Front desk", True),
    ("I'm in billing, and the billing application crashed.", "Billing", True),
    ("I'm on the billing application all day and it froze.", "Billing", False),
    ("The front desk computer won't turn on.", "Front desk", False),
    ("The radiology printer is jammed.", "Radiology", False),
    ("Our scheduling system is down.", "Scheduling", False),
    ("The HR portal won't load.", "Human Resources", False),
    ("This is James from the front desk, my printer is jammed.", "Front desk", True),
    ("Billing department here, the internet is down.", "Billing", True),
    ("I work in radiology and the radiology printer is jammed.", "Radiology", True),
])
async def test_only_a_stated_department_is_taken_from_the_description(said, department, kept):
    from app.voice.orchestrator import _department_stated

    assert _department_stated(said, department) is kept


async def _to_department_question(call):
    """An employee who gave their name with the problem: after the issue questions, only the department is asked."""
    out = await _describe(call, "This is Pat Real, my laptop won't turn on.", "Other", short="your laptop",
                          caller_name="Pat Real")
    for _ in range(5):  # answer the issue questions, however many are left after the description
        if "which department" in _text(out).lower():
            return
        out = await call.turn("No.")
    raise AssertionError(f"the department question never came: {_text(out)}")


@pytest.mark.parametrize("answer", ["The billing application.", "The HR portal"])
async def test_a_system_name_given_for_the_department_is_not_recorded_as_one(answer, make_call, db_session):
    call = make_call(f"dept-answer-{len(answer)}")
    await _to_department_question(call)
    model_reading = "Billing" if "billing" in answer.lower() else "Human Resources"
    call._say(interpret_name=nlu.TurnResult(value=None, unable_to_determine=False, extras={"department": model_reading}))

    await call.turn(answer)

    assert (await _session(db_session, call.call_id)).collected.get("department") is None


async def test_a_department_answer_naming_the_department_is_recorded(make_call, db_session, no_model):
    call = make_call("dept-answer-ok")
    await _to_department_question(call)
    no_model()

    await call.turn("Billing.")

    session = await _session(db_session, "dept-answer-ok")
    assert (session.collected["department"], session.collected["department_verified"]) == ("Billing", True)


async def test_a_call_already_past_the_problem_is_not_sent_back_to_the_questions(make_call, db_session, monkeypatch):
    """A call in progress when this code is deployed: it carries on with its paperwork."""
    monkeypatch.setattr(settings, "voice_diagnostic_questions", False)
    call = make_call("dx-inflight")
    asked_name = await _describe(call, "Outlook crashes on my laptop.", "Microsoft 365", short="your Outlook")
    assert (await _session(db_session, "dx-inflight")).state == VoiceCallState.COLLECT_NAME, _text(asked_name)

    monkeypatch.setattr(settings, "voice_diagnostic_questions", True)  # the new code is live mid-call
    asked_email = await call.name_and_department("Pat Real")

    assert "email" in _text(asked_email).lower() and "error message" not in _text(asked_email).lower()
    assert "diagnostics" not in (await _session(db_session, "dx-inflight")).collected
    call.monkeypatch.setattr(nlu, "interpret_email", _REAL_INTERPRET_EMAIL)
    assert "Your ticket number is" in _text(await call.turn("skip"))


# --- 4. interruptions, failures, retries ------------------------------------------------------------------------


async def test_silence_is_asked_about_once_then_moves_on_without_counting_a_misunderstanding(make_call, db_session):
    call = make_call("dx-silence")
    await _describe(call, "Outlook crashes on my laptop.", "Microsoft 365", short="your Outlook")

    again = await call.turn("")
    moved_on = await call.turn("")

    assert _text(again) == f"{dx.CLARIFY} Do you see an error message? If so, what does it say?"
    session = await _session(db_session, "dx-silence")
    assert session.misunderstanding_count == 0
    assert "error" in session.collected["diagnostics"]["asked"] and "error" not in session.collected["diagnostics"]["values"]
    assert "error message" not in _text(moved_on).lower()


async def test_asking_for_a_person_mid_questions_escalates_with_what_was_found(make_call, client, db_session):
    call = make_call("dx-human")
    await _describe(call, "Outlook crashes on my laptop.", "Microsoft 365", short="your Outlook")
    await call.turn("It says Outlook stopped working.")

    out = await call.turn("Can I just talk to a real person?")

    assert out["expect_reply"] is False
    session = await _session(db_session, "dx-human")
    assert session.state == VoiceCallState.ESCALATED and session.escalation_reason == EscalationReason.CALLER_REQUESTED
    assert "Error message: It says Outlook stopped working." in (await _ticket(client, out["ticket_id"]))["description"]


async def test_saying_goodbye_mid_questions_saves_a_ticket_with_the_answers_so_far(make_call, client):
    call = make_call("dx-bye")
    await _describe(call, "Outlook crashes on my laptop.", "Microsoft 365", short="your Outlook")
    await call.turn("It says Outlook stopped working.")

    out = await call.turn("Actually I have to go, bye.")

    assert out["ticket_id"] and out["expect_reply"] is False
    assert "Error message: It says Outlook stopped working." in (await _ticket(client, out["ticket_id"]))["description"]


async def test_a_failed_model_call_for_the_details_does_not_stop_the_questions(make_call, db_session):
    call = make_call("dx-modelfail")
    await call.start()
    await call.turn("I'm an employee")
    call._say(interpret_description=nlu.TurnResult(
        value="Outlook crashes on my laptop.", confidence="high", unable_to_determine=False, category="Microsoft 365",
        priority=Priority.MEDIUM, extras={"short_issue": "your Outlook"},
    ))
    await call.turn("Outlook crashes on my laptop.")  # asks when it started
    call._say(interpret_details=nlu.TurnResult())  # the model failed / timed out

    out = await call.turn("Since this morning")

    assert "error message" in _text(out).lower()
    assert (await _session(db_session, "dx-modelfail")).state == VoiceCallState.COLLECT_DIAGNOSTICS


async def test_a_retried_turn_is_replayed_and_the_answer_recorded_once(make_call, client, db_session):
    call = make_call("dx-replay")
    await _describe(call, "Outlook crashes on my laptop.", "Microsoft 365", short="your Outlook")
    body = {"call_id": "dx-replay", "utterance": "It says Outlook stopped working.", "turn_id": "dx-1"}

    first = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()
    again = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()

    assert again["lines"] == first["lines"]
    assert (await _session(db_session, "dx-replay")).collected["diagnostics"]["asked"] == ["error"]


async def test_filing_twice_makes_one_ticket_and_one_email(make_call, client, db_session, sent_emails):
    call = make_call("dx-dupe")
    await _describe(call, "My laptop won't turn on.", "Other", short="your laptop")
    for said in ("A laptop.", "No."):
        await call.turn(said)
    await call.name_and_department("Pat Real")
    body = {"call_id": "dx-dupe", "utterance": "skip", "turn_id": "dx-file-1"}

    first = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()
    second = (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()

    assert first["ticket_id"] and second["ticket_id"] == first["ticket_id"]
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar_one() == 1
    assert len(sent_emails) == 1


async def test_simultaneous_calls_keep_their_own_answers(make_call, db_session):
    first, second = make_call("dx-c1"), make_call("dx-c2")
    # Described one after the other: the harness stubs the model process-wide. The diagnostic turns below use
    # no model, and those are what run at the same time.
    await _describe(first, "Outlook crashes on my laptop.", "Microsoft 365", short="your Outlook")
    await _describe(second, "I can't connect to the VPN from home.", "Network", short="the VPN")

    await asyncio.gather(first.turn("It says Outlook stopped working."), second.turn("It says connection failed."))

    one, two = await _session(db_session, "dx-c1"), await _session(db_session, "dx-c2")
    assert one.collected["diagnostics"]["values"]["error"] == "It says Outlook stopped working."
    assert two.collected["diagnostics"]["values"]["error"] == "It says connection failed."


async def test_the_summary_is_written_once_and_includes_the_answers(make_call, client, monkeypatch, sent_emails):
    prompts = []

    class Provider:
        is_configured = True

        async def structured(self, **kwargs):
            prompts.append(kwargs["user"])
            return {"summary": "Outlook crashes on the caller's laptop; email still works on the phone."}

    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: Provider())
    call = make_call("dx-summary")
    await _describe(call, "Outlook crashes on my laptop.", "Microsoft 365", short="your Outlook")
    for said in ("It says Outlook stopped working.", "Yes, on my phone it works.", "No, just me."):
        await call.turn(said)

    filed = await _finish(call)

    assert len(prompts) == 1
    assert "Error message: It says Outlook stopped working." in prompts[0]
    assert (await _ticket(client, filed["ticket_id"]))["ai_summary"].startswith("Outlook crashes")
    assert len(sent_emails) == 1 and "email still works on the phone" in sent_emails[0]["body_html"]


# --- 5. what stays the same --------------------------------------------------------------------------------------


async def test_patients_are_never_asked_the_it_questions(make_call, db_session):
    call = make_call("dx-patient")
    await call.start()
    await call.turn("I'm a patient")
    await call.turn("I need to book an appointment")
    await call.turn("next Tuesday")
    call._say(interpret_name=nlu.TurnResult(value="John Smith", confidence="high", unable_to_determine=False))
    await call.turn("John Smith")

    session = await _session(db_session, "dx-patient")
    assert "diagnostics" not in session.collected and session.state != VoiceCallState.COLLECT_DIAGNOSTICS


async def test_the_setting_switches_the_questions_off(make_call, db_session, monkeypatch):
    monkeypatch.setattr(settings, "voice_diagnostic_questions", False)
    call = make_call("dx-off")

    first = await _describe(call, "Outlook crashes on my laptop.", "Microsoft 365", short="your Outlook")

    assert (await _session(db_session, "dx-off")).state == VoiceCallState.COLLECT_NAME
    assert "error message" not in _text(first).lower()


async def test_a_second_issue_gets_its_own_questions(make_call, db_session):
    call = make_call("dx-second")
    await _describe(call, "My laptop won't turn on.", "Other", short="your laptop")
    for said in ("A laptop.", "No."):
        await call.turn(said)
    await _finish(call)
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=True, unable_to_determine=False))
    await call.turn("yes")  # anything else?
    call._say(interpret_description=nlu.TurnResult(
        value="The printer is jammed.", confidence="high", unable_to_determine=False, category="Printer",
        priority=Priority.MEDIUM, extras={"short_issue": "the printer", "started": "today", "work_blocked": False},
    ))

    out = await call.turn("The printer is jammed.")

    assert "Which printer" in _text(out)
    assert (await _session(db_session, "dx-second")).collected["diagnostics"]["playbook"] == "printer"

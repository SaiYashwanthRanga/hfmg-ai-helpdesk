"""Provider calls (CALL_FLOW.md 2.5): a doctor, nurse practitioner or physician assistant reporting a problem.

The provider flow is the employee IT flow -- listening, no question asked twice, corrections, name, callback, email,
read-back -- with provider questions for the clinical systems, "can you see patients?" deciding the priority, and
every ticket filed under the Providers category and sent to the same IT inbox. Off by default (VOICE_PROVIDER_FLOW):
then nothing about a call changes. Real /start and /turn routes; the language model is stubbed for the description,
the details and the name only. Synthetic callers only; no patient is ever named.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import CallerType, Category, Priority, VoiceCallSession, VoiceCallState
from app.voice import caller_routing as routing
from app.voice import diagnostics as dx
from app.voice import nlu, orchestrator, scripts
from app.voice import priority as priority_rules
from tests.test_directory_identity import DirCall
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550177"
PROVIDER, EMPLOYEE, PATIENT = routing.PROVIDER, routing.EMPLOYEE, routing.PATIENT


@pytest.fixture(autouse=True)
async def _production_flow(monkeypatch, db_session):
    for name, value in (("voice_ask_caller_type", True), ("voice_provider_flow", True),
                        ("voice_diagnostic_questions", True), ("voice_ask_extension", True),
                        ("voice_confirm_summary", True)):
        monkeypatch.setattr(settings, name, value)
    for name in ("Providers", "eClinicalWorks", "Microsoft 365", "Network", "Printer", "Password", "Other",
                 "Other Patient Support", "Appointment Booking"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):  # noqa: F811
    def make(call_id):
        return DirCall(client, monkeypatch, call_id, CALLER)

    return make


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


def _description(call, said, category="eClinicalWorks", *, short="eClinicalWorks", started="this morning",
                 blocked=None, scope=None, priority=Priority.MEDIUM):
    call._say(interpret_description=nlu.TurnResult(
        value=said, confidence="high", unable_to_determine=False, category=category, priority=priority,
        extras={"short_issue": short, "started": started, "work_blocked": blocked, "affected_scope": scope},
    ))


def _details(call, *, blocked, started=None):
    call._say(interpret_details=nlu.TurnResult(
        value="details", confidence="high", unable_to_determine=False,
        extras={"work_blocked": blocked, "started": started},
    ))


async def _as_provider(call, said="This is Dr. Rivera, I'm a provider."):
    await call.start()
    return await call.turn(said)


async def _file(call, out):
    """Name, no extension, skip the email, yes to the read-back -- whichever of these are still asked."""
    call._say(interpret_name=nlu.TurnResult(value="Ana Rivera", confidence="high", unable_to_determine=False,
                                            extras={"department": "Family Medicine", "name_confidence": "high"}))
    out = await call.turn("Ana Rivera, Family Medicine")
    for said in ("No, I don't have one.", "skip", "Yes.", "Yes."):
        if out["ticket_id"] or not out["expect_reply"]:
            break
        out = await call.turn(said)
    return out


def _intake(ticket) -> str:
    return ticket["description"].split("--- Call transcript ---")[0]


# --- who is a provider ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("utterance, expected", [
    ("I'm a provider.", PROVIDER), ("Provider.", PROVIDER), ("This is Dr. Rivera.", PROVIDER),
    ("this is doctor Patel", PROVIDER), ("It's Dr. Shah", PROVIDER), ("I'm a nurse practitioner", PROVIDER),
    ("I'm an NP at Newburgh", PROVIDER), ("I'm a PA", PROVIDER), ("I'm a physician assistant", PROVIDER),
    ("I'm one of the providers here", PROVIDER), ("I'm the doctor on call", PROVIDER),
    ("I'm a doctor, I work at the clinic", PROVIDER),  # a provider also works here: one answer, not two
    ("Provider, I can't send prescriptions", PROVIDER),  # a provider's work is prescriptions: no patient topic overrules it
    ("This is Dr. Rivera, eCW is frozen", PROVIDER),
    # Someone working for, or talking about, a provider is not one.
    ("I'm Dr. Patel's nurse", None), ("I'm doctor Patel's assistant", None), ("I'm a doctor's assistant", None),
    ("I work for Dr. Patel", EMPLOYEE), ("this is the doctor's office", None), ("I'm calling about Dr. Patel", None),
    ("the provider can't log in", None), ("my provider wants me to get labs", None),
    ("As the doctor said, I need a refill", PATIENT), ("I'm a patient, I need to see the doctor", PATIENT),
    ("I'm not a provider, I'm an employee", EMPLOYEE),
    ("I'm a patient", PATIENT), ("I'm an employee", EMPLOYEE), ("hello", None),
])
async def test_providers_are_recognised_by_what_they_say_they_are(utterance, expected):
    assert routing.identify_caller(utterance, providers=True) == expected


@pytest.mark.parametrize("utterance", [
    "I'm a provider.", "Provider.", "This is Dr. Rivera.", "I'm a nurse practitioner", "I'm a doctor, I work at the clinic",
    "Provider, I can't send prescriptions", "This is Dr. Rivera, eCW is frozen", "I'm a patient", "I'm an employee",
    "I'm Dr. Patel's nurse", "my printer is jammed", "hello",
])
async def test_with_providers_not_offered_routing_is_exactly_as_before(utterance):
    assert routing.identify_caller(utterance) != PROVIDER
    assert routing.identity_statement(utterance) != PROVIDER
    # Without the option, the rules read the sentence the way they did before providers existed.
    without_provider_words = routing.identify_caller(utterance)
    assert without_provider_words == routing.identify_caller(utterance, providers=False)


# --- which questions a provider is asked -------------------------------------------------------------------------


@pytest.mark.parametrize("description, category, expected", [
    ("eCW keeps freezing when I open the orders tab", "eClinicalWorks", dx.PROVIDER_EHR),
    ("I can't open any charts", "Other", dx.PROVIDER_EHR),
    ("The progress note template is missing a section", "Other", dx.PROVIDER_EHR),
    ("e-prescribing won't send my prescriptions to the pharmacy", "eClinicalWorks", dx.PROVIDER_ERX),
    ("My EPCS token isn't working", "Other", dx.PROVIDER_ERX),
    ("Dragon won't put my dictation into the note in eCW", "eClinicalWorks", dx.PROVIDER_DICTATION),
    ("My healow video visit won't start", "Other", dx.PROVIDER_TELEHEALTH),
    ("Lab results aren't showing up in eCW", "eClinicalWorks", dx.PROVIDER_RESULTS),
    ("Imaging results aren't available yet", "Other", dx.PROVIDER_RESULTS),
    ("Incoming faxes aren't coming in", "Other", dx.PROVIDER_FAX),
    ("eClinicalWorks is down", "eClinicalWorks", dx.PROVIDER_EHR),
    # Not a clinical system, or who fixes it is the account team: the same questions an employee would get.
    ("I'm locked out of eCW", "Password", dx.ACCOUNT),
    ("My password expired", "Password", dx.ACCOUNT),
    ("The printer in exam room 3 is jammed", "Printer", dx.PRINTER),
    ("Outlook keeps crashing", "Microsoft 365", dx.OUTLOOK),
])
async def test_a_provider_gets_the_questions_for_the_system_they_name(description, category, expected):
    assert dx.choose(category, description, provider=True) is expected


async def test_an_employee_never_gets_the_provider_questions():
    for description in ("eCW keeps freezing", "Dragon won't open", "e-prescribing is down", "Lab results are missing"):
        assert dx.choose("eClinicalWorks", description) not in dx.PROVIDER_PLAYBOOKS


async def test_what_the_provider_already_said_is_not_asked_again():
    state = dx.begin("eClinicalWorks", "eCW freezes when I open the orders tab, on every patient", None, provider=True)

    values = state["values"]
    assert (values["part"], values["symptom"], values["every_patient"]) == ("orders", "slow", "every")
    key, prompt = dx.next_prompt(state)
    assert key == "error" and prompt == dx._PROVIDER_ERROR.prompt


async def test_no_question_asks_which_patient():
    reassurance = "You don't need to tell me which patient."
    assert reassurance in dx._EVERY_PATIENT.prompt
    for playbook in dx.PROVIDER_PLAYBOOKS:
        for question in playbook.questions:
            asked = question.prompt.replace(reassurance, "").lower()
            assert not any(word in asked for word in ("patient's name", "date of birth", "mrn", "which patient"))


async def test_results_in_the_wrong_chart_and_a_waiting_patient_are_urgent():
    wrong_chart = dx.begin("eClinicalWorks", "Lab results are showing up in the wrong chart", None, provider=True)
    waiting = dx.begin("Other", "My telehealth visit won't start and the patient is waiting", None, provider=True)

    for state in (wrong_chart, waiting):
        assert dx.priority_floor(state)[0] is Priority.URGENT
        assert dx.next_prompt(state) is None  # urgent: straight to name and callback number


async def test_a_corrected_provider_issue_gets_the_right_provider_questions():
    state = dx.begin("eClinicalWorks", "eCW is acting up", None, provider=True)
    state["pending"] = "part"

    fix = dx.correction(state, "No, it's not the eCW, it's the Dragon software")

    assert fix["playbook"] == dx.PROVIDER_DICTATION.name


# --- priority ------------------------------------------------------------------------------------------------


async def test_others_affected_is_at_least_high_for_a_provider():
    medium = priority_rules.Assessment(Priority.MEDIUM, None, "Several people, can still work", "others_affected")

    raised = priority_rules.provider_floor(medium, "several_people")

    assert (raised.priority, raised.reason) == (Priority.HIGH, "others have the same problem")
    assert priority_rules.provider_floor(medium, "one_person") is medium
    urgent = priority_rules.Assessment(Priority.URGENT, "it's affecting patient care", "", "patient_care")
    assert priority_rules.provider_floor(urgent, "several_people") is urgent  # never lowered


# --- whole calls ---------------------------------------------------------------------------------------------


async def test_the_opening_question_offers_provider_only_when_the_flow_is_on(make_call, monkeypatch):
    assert _text(await make_call("prov-greet-on").start()) == scripts.GREETING_IDENTIFY_PROVIDER

    monkeypatch.setattr(settings, "voice_provider_flow", False)
    assert _text(await make_call("prov-greet-off").start()) == scripts.GREETING_IDENTIFY


async def test_with_the_flow_off_a_provider_answer_is_not_a_provider_call(make_call, db_session, monkeypatch):
    monkeypatch.setattr(settings, "voice_provider_flow", False)
    call = make_call("prov-off")
    call.classify_as("INTERNAL_IT", 0.95)

    await _as_provider(call, "I'm a doctor, I work at the clinic")

    assert (await _session(db_session, "prov-off")).collected["caller_type"] == EMPLOYEE


async def test_a_provider_is_asked_an_open_question_and_given_time(make_call, db_session):
    call = make_call("prov-open")

    asked = await _as_provider(call)
    assert _text(asked) == scripts.PROVIDER_ASK

    cut_off = await call.turn("eCW keeps freezing when I open the orders tab, and")
    assert _text(cut_off) == scripts.PROBLEM_GO_ON  # never half a problem

    said = "eCW keeps freezing when I open the orders tab, and it happens on every patient since about nine."
    _description(call, said, started="since about nine")
    out = await call.turn("it happens on every patient since about nine.")

    session = await _session(db_session, "prov-open")
    assert session.collected["caller_type"] == PROVIDER
    assert "every patient" in session.collected["problem_words"]  # both halves, in the provider's words
    assert _text(out).endswith(scripts.PROVIDER_DETAILS_ASK_BLOCKED)  # when it started was said: only the impact


async def test_an_ecw_problem_affecting_others_is_filed_high_under_providers_to_the_it_inbox(
    make_call, client, db_session, sent_emails
):
    call = make_call("prov-ecw")
    await _as_provider(call)
    said = "eCW keeps freezing when I open the orders tab, on every patient, since about nine."
    _description(call, said, started="since about nine")
    await call.turn(said)

    _details(call, blocked=False)
    out = await call.turn("I can still see them, I'm writing orders on paper.")
    assert _text(out).endswith(dx._PROVIDER_ERROR.prompt)  # which part, what happens, every patient: all said

    out = await call.turn("It just says not responding.")
    assert _text(out).endswith(dx._SCOPE.prompt)
    out = await call.turn("Yes, Dr. Shah next door has it too.")

    filed = await _file(call, out)

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["caller_type"], ticket["category"]["name"], ticket["priority"]) == ("PROVIDER", "Providers", "HIGH")
    intake = _intake(ticket)
    for expected in ("Caller: provider (said by the caller)", "System: eClinicalWorks",
                     "Issue type: eClinicalWorks (provider)", "Part of eClinicalWorks: orders",
                     "Every patient or one chart: every", "Error message: It just says not responding.",
                     "Others affected: yes", "Work blocked: no"):
        assert expected in intake, expected
    assert len(sent_emails) == 1  # the same IT helpdesk notification as every other ticket
    # The read-back says the level the ticket is filed at (a first version read "medium" and filed High).
    spoken = " ".join(t["text"] for t in (await _session(db_session, "prov-ecw")).turns if t["role"] == "agent")
    assert "Since others have the same problem, I'll mark this high priority." in spoken
    assert "medium priority" not in spoken


@pytest.mark.parametrize("strict", [False, True])
async def test_a_provider_who_cannot_see_patients_is_critical_and_told_straight_away(
    make_call, client, db_session, sent_emails, monkeypatch, strict
):
    monkeypatch.setattr(settings, "voice_strict_extraction", strict)
    call = make_call("prov-critical")
    await _as_provider(call)
    said = "eClinicalWorks won't open at all."
    _description(call, said, started="just now")
    await call.turn(said)

    _details(call, blocked=True)
    # Strict extraction asks the impact as a clarification question, read by its own model call.
    call._say(interpret_clarification=nlu.TurnResult(confidence="high", unable_to_determine=False,
                                                     extras={"answer": "yes"}))
    out = await call.turn("Yes, I have a full clinic and I can't do anything.")

    assert "patient care is affected" in _text(out)  # the urgent notice comes first
    assert any(option in _text(out) for option in scripts.NAME_ASK_OPTIONS)  # then only who and where to call
    session = await _session(db_session, "prov-critical")
    assert session.collected["patient_care_affected"] is True

    filed = await _file(call, out)
    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["priority"], ticket["category"]["name"]) == ("URGENT", "Providers")


async def test_a_provider_with_a_printer_problem_gets_the_printer_questions(make_call, db_session):
    call = make_call("prov-printer")
    await _as_provider(call)
    said = "The printer in exam room 3 keeps jamming."
    _description(call, said, "Printer", short="the printer", blocked=False)

    await call.turn(said)

    state = (await _session(db_session, "prov-printer")).collected["diagnostics"]
    assert state["playbook"] == dx.PRINTER.name and state["provider"] is True


async def test_an_employee_who_corrects_to_provider_at_the_first_question_is_moved(make_call, db_session):
    call = make_call("prov-correct")
    await call.start()
    await call.turn("I'm an employee.")

    out = await call.turn("Actually, I'm a provider.")

    assert _text(out) == f"{scripts.TYPE_CORRECTED} {scripts.PROVIDER_ASK}"
    assert (await _session(db_session, "prov-correct")).collected["caller_type"] == PROVIDER


async def test_a_patient_call_is_unchanged_with_providers_offered(make_call, db_session):
    call = make_call("prov-patient")
    await call.start()

    out = await call.turn("I'm a patient")

    assert _text(out) == scripts.PATIENT_INTENT_ASK
    assert (await _session(db_session, "prov-patient")).collected["caller_type"] == PATIENT


async def test_a_provider_hears_the_it_escalation_wording():
    session = VoiceCallSession(call_id="prov-esc", state=VoiceCallState.COLLECT_NAME,
                               collected={"caller_type": CallerType.PROVIDER.value})

    assert orchestrator._escalation_lines(session) == orchestrator._ESCALATION_LINES[CallerType.INTERNAL_IT.value]

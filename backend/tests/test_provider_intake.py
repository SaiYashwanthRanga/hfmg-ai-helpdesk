"""Provider calls (CALL_FLOW.md 2.5): a doctor, nurse practitioner or physician assistant calling the help desk.

A provider calls when no human IT representative is free, so the agent never transfers or dials anyone. It takes the
problem, the provider's name, the application or system (only when the description does not name it) and a callback
number the provider confirms; files a High ticket under Providers; emails the IT inbox; and only then says the IT
team will use the confirmed number. No diagnostic questions, department, extension, email or read-back.

Off by default (VOICE_PROVIDER_FLOW): then nothing about a call changes. Real /start and /turn routes; the language
model is stubbed for the description, the name, the phone number and yes/no. Synthetic callers only; no patient is
ever named.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import CallerType, Category, Priority, Ticket, TicketSource, VoiceCallSession, VoiceCallState
from app.notifications import email as email_module
from app.services import ticket_service
from app.voice import caller_routing as routing
from app.voice import diagnostics as dx
from app.voice import nlu, orchestrator, scripts
from app.voice import priority as priority_rules
from tests.test_directory_identity import DirCall
from tests.test_multi_issue_calls import AUTH, SIP, _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550177"
CALLBACK = "8455550123"
OTHER_CALLBACK = "8455550188"
PROVIDER, EMPLOYEE, PATIENT = routing.PROVIDER, routing.EMPLOYEE, routing.PATIENT
BANNER = "HIGH PRIORITY — PROVIDER CALLBACK REQUIRED"


@pytest.fixture(autouse=True)
async def _production_flow(monkeypatch, db_session):
    for name, value in (("voice_ask_caller_type", True), ("voice_provider_flow", True),
                        ("voice_diagnostic_questions", True), ("voice_ask_extension", True),
                        ("voice_confirm_summary", True), ("voice_confirm_callback", True)):
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


def _name(call, name="Ana Rivera"):
    call._say(interpret_name=nlu.TurnResult(value=name, confidence="high", unable_to_determine=False,
                                            extras={"name_confidence": "high"}))


async def _as_provider(call, said="This is Dr. Rivera, I'm a provider."):
    await call.start()
    return await call.turn(said)


async def _report(call, said="eCW keeps freezing when I open the orders tab.", **kwargs):
    """Provider identified, then the problem described: the next thing asked is the name."""
    await _as_provider(call)
    _description(call, said, **kwargs)
    return await call.turn(said)


async def _up_to_the_callback_question(call, **kwargs):
    out = await _report(call, **kwargs)
    assert _text(out).endswith(scripts.PROVIDER_NAME_ASK)
    _name(call)
    return await call.turn("Ana Rivera")


async def _up_to_the_confirmation(call, number=CALLBACK, **kwargs):
    out = await _up_to_the_callback_question(call, **kwargs)
    assert _text(out) == scripts.PROVIDER_PHONE_ASK
    call.phone_says(number)
    return await call.turn("eight four five, five five five, zero one two three")


async def _file(call, number=CALLBACK, **kwargs):
    """The whole call up to the provider's yes to the read-back of the callback number."""
    await _up_to_the_confirmation(call, number, **kwargs)
    return await call.answer(True)


async def _stored_priority(db_session, ticket_id):
    await db_session.rollback()  # a fresh read: the ticket was written by the app's own session
    return (await db_session.execute(select(Ticket.priority).where(Ticket.id == uuid.UUID(ticket_id)))).scalar_one()


async def _ticket_count(db_session):
    await db_session.rollback()
    return (await db_session.execute(
        select(func.count()).select_from(Ticket).where(Ticket.source == TicketSource.PHONE)
    )).scalar_one()


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


# --- whole calls: the callback request ------------------------------------------------------------------------


class _Mailbox:
    """The helpdesk notification as the email provider receives it: every attempt, and the ones that went through."""

    def __init__(self):
        self.attempts: list[dict] = []
        self.sent: list[dict] = []
        self.fail_first = 0  # the first N attempts fail


@pytest.fixture
def mailbox(monkeypatch):
    box = _Mailbox()

    class Provider:
        is_configured = True

        async def send(self, **kwargs):
            box.attempts.append(kwargs)
            if len(box.attempts) <= box.fail_first:
                return False
            box.sent.append(kwargs)
            return True

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(settings, "enable_email_notifications", True)
    monkeypatch.setattr(settings, "helpdesk_email", "helpdesk@example.org")
    monkeypatch.setattr(settings, "notification_max_attempts", 4)
    monkeypatch.setattr(settings, "voice_max_phone_attempts", 2)
    monkeypatch.setattr(email_module, "get_email_provider", lambda: Provider())
    monkeypatch.setattr(email_module.asyncio, "sleep", no_sleep)
    return box


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
    assert _text(out).endswith(scripts.PROVIDER_NAME_ASK)  # the system was named, so straight on to who is calling


async def test_the_provider_is_asked_only_what_a_callback_ticket_needs(
    make_call, client, db_session, mailbox
):
    call = make_call("prov-seq")
    assert _text(await _as_provider(call)) == scripts.PROVIDER_ASK
    said = "eCW keeps freezing when I open the orders tab."
    _description(call, said)
    assert _text(await call.turn(said)).endswith(scripts.PROVIDER_NAME_ASK)
    _name(call)
    asked = await call.turn("Ana Rivera")
    assert _text(asked) == "What is the best phone number for our IT team to call you back on?"

    call.phone_says(CALLBACK)
    confirm = await call.turn("eight four five, five five five, zero one two three")
    assert _text(confirm) == scripts.PROVIDER_CALLBACK_CONFIRM.format(number=scripts.spoken_phone_number(CALLBACK))
    assert "Is that the correct number for our IT team to call you back on?" in _text(confirm)
    session = await _session(db_session, "prov-seq")
    assert session.state == VoiceCallState.CONFIRM_CALLBACK_NUMBER
    assert await _ticket_count(db_session) == 0  # nothing is filed, or promised, before the number is confirmed

    done = await call.answer(True)

    assert done["ticket_id"]
    heard = _text(done)
    assert "I've recorded your request" in heard and "the number you confirmed" in heard
    assert scripts.spoken_ticket_number((await _ticket(client, done["ticket_id"]))["ticket_number"]) in heard
    for promise in ("minute", "hour", "shortly", "soon", "today", "right away"):
        assert promise not in heard.lower(), promise  # no callback time is promised
    # Nothing else was asked: no diagnostics, impact, department, extension, email or read-back of the ticket.
    spoken = " ".join(t["text"] for t in (await _session(db_session, "prov-seq")).turns if t["role"] == "agent").lower()
    for unasked in ("department", "email", "extension", "error message", "anyone else", "when did this start",
                    "stopping you", "is that all correct", "let me make sure"):
        assert unasked not in spoken, unasked


async def test_the_ticket_is_a_provider_callback_request_saved_at_high(
    make_call, client, db_session, mailbox
):
    call = make_call("prov-ticket")
    # The model reads this as Low and nothing is blocked: a provider's request is still High.
    done = await _file(call, priority=Priority.LOW, blocked=False)

    ticket = await _ticket(client, done["ticket_id"])
    assert (ticket["caller_type"], ticket["category"]["name"], ticket["priority"]) == ("PROVIDER", "Providers", "HIGH")
    assert await _stored_priority(db_session, done["ticket_id"]) is Priority.HIGH  # in the database, not just the email
    assert ticket["callback_number"] == CALLBACK
    assert ticket["caller_name"] == "Ana Rivera"
    intake = _intake(ticket)
    for expected in ("Caller type: Provider (said by the caller)", "Provider: Ana Rivera", "System: eClinicalWorks",
                     f"Callback number: {CALLBACK} (confirmed by the provider)", "Priority: High: provider callback request",
                     f"Action required: IT Helpdesk must call the provider back at {CALLBACK}"):
        assert expected in intake, expected
    assert "eCW keeps freezing" in ticket["description"]
    assert "Department" not in intake


async def test_the_helpdesk_email_leads_with_the_callback_request(make_call, client, mailbox):
    call = make_call("prov-mail")
    done = await _file(call)
    number = (await _ticket(client, done["ticket_id"]))["ticket_number"]

    assert len(mailbox.sent) == 1
    mail = mailbox.sent[0]
    assert mail["to"] == ["helpdesk@example.org"]  # the configured recipients, through the existing provider
    assert mail["subject"].startswith(BANNER) and number in mail["subject"]
    body = mail["body"]
    assert body.startswith(BANNER)
    for expected in ("Priority:\nHIGH", f"Ticket Number:\n{number}", "Provider:\nAna Rivera",
                     f"Callback Number:\n{CALLBACK}", "System:\neClinicalWorks", "Issue:\neCW keeps freezing",
                     f"Required Action:\nContact the provider at the confirmed callback number: {CALLBACK}"):
        assert expected in body, expected
    assert "Extension" not in body  # a provider is not asked for one


async def test_other_tickets_are_emailed_exactly_as_before():
    plain = email_module._build_body("Maria Lopez", "8455550142", "Outlook will not open.", "A summary.",
                                     ticket_number="HFMG-2026-000001")

    assert plain == ("Caller:\nMaria Lopez\n\nCallback Number:\n8455550142\n\nEmail:\nNot provided\n\n"
                     "Issue:\nOutlook will not open.\n\nSummary:\nA summary.\n\nTicket Number:\nHFMG-2026-000001")


async def test_the_system_is_asked_once_when_the_description_does_not_name_it(
    make_call, client, db_session, mailbox
):
    call = make_call("prov-system")
    asked = await _report(call, "Something is wrong with the thing on my desk.", category="Other", short="a problem")
    assert _text(asked).endswith(scripts.PROVIDER_SYSTEM_ASK)
    assert (await _session(db_session, "prov-system")).state == VoiceCallState.COLLECT_DETAILS

    out = await call.turn("It's the Dragon dictation.")
    assert _text(out) == scripts.PROVIDER_NAME_ASK  # not asked again, and nothing about impact
    _name(call)
    await call.turn("Ana Rivera")
    call.phone_says(CALLBACK)
    await call.turn("eight four five, five five five, zero one two three")
    done = await call.answer(True)

    ticket = await _ticket(client, done["ticket_id"])
    assert "System: It's the Dragon dictation." in _intake(ticket)
    assert "System:\nIt's the Dragon dictation." in mailbox.sent[0]["body"]


@pytest.mark.parametrize("answer", ["I don't know", ""])
async def test_a_provider_who_cannot_name_the_system_is_not_pressed(make_call, client, db_session, mailbox, answer):
    call = make_call("prov-nosystem")
    await _report(call, "Something is wrong with the thing on my desk.", category="Other", short="a problem")

    out = await call.turn(answer)

    assert _text(out) == scripts.PROVIDER_NAME_ASK
    _name(call)
    await call.turn("Ana Rivera")
    call.phone_says(CALLBACK)
    await call.turn("eight four five, five five five, zero one two three")
    done = await call.answer(True)
    assert "System:" not in _intake(await _ticket(client, done["ticket_id"]))  # nothing is invented


async def test_a_number_that_is_not_understood_is_asked_for_again(make_call, db_session, mailbox):
    call = make_call("prov-unclear")
    await _up_to_the_callback_question(call)

    call.phone_says(None)
    retry = await call.turn("mumble mumble")
    assert _text(retry) == scripts.phone_retry(0, 1)
    assert (await _session(db_session, "prov-unclear")).state == VoiceCallState.COLLECT_PHONE

    call.phone_says(CALLBACK)
    confirm = await call.turn("eight four five, five five five, zero one two three")
    assert scripts.spoken_phone_number(CALLBACK) in _text(confirm)
    assert await _ticket_count(db_session) == 0


async def test_when_no_number_is_understood_the_number_calling_in_is_offered_for_confirmation(
    make_call, client, db_session, mailbox
):
    call = make_call("prov-fallback")
    await _up_to_the_callback_question(call)

    call.phone_says(None)
    await call.turn("mumble mumble")
    out = await call.turn("mumble")

    session = await _session(db_session, "prov-fallback")
    on_file = session.collected["phone_number"]
    assert _text(out).startswith(scripts.PROVIDER_PHONE_FALLBACK)
    assert scripts.PROVIDER_CALLBACK_CONFIRM.format(number=scripts.spoken_phone_number(on_file)) in _text(out)
    assert await _ticket_count(db_session) == 0  # still not filed until the provider says yes

    done = await call.answer(True)
    ticket = await _ticket(client, done["ticket_id"])
    assert ticket["callback_number"] == on_file


async def test_a_corrected_number_is_confirmed_before_the_ticket_is_filed(make_call, client, db_session, mailbox):
    call = make_call("prov-correct-number")
    await _up_to_the_confirmation(call)

    asked = await call.answer(False)  # "No, that's not right"
    assert _text(asked) == scripts.PROVIDER_CALLBACK_ASK_OTHER
    assert await _ticket_count(db_session) == 0

    call.phone_says(OTHER_CALLBACK)
    confirm = await call.turn("eight four five, five five five, zero one eight eight")
    assert _text(confirm) == scripts.CALLBACK_CONFIRM_NEW.format(number=scripts.spoken_phone_number(OTHER_CALLBACK))
    assert await _ticket_count(db_session) == 0  # the corrected number is read back first

    done = await call.answer(True)

    ticket = await _ticket(client, done["ticket_id"])
    assert ticket["callback_number"] == OTHER_CALLBACK
    assert OTHER_CALLBACK in mailbox.sent[0]["body"] and CALLBACK not in mailbox.sent[0]["body"]


async def test_a_provider_call_never_transfers_or_dials(make_call, db_session, mailbox):
    from app.voice.sip_routes import TurnResponse

    call = make_call("prov-notransfer")
    outcomes = [await _as_provider(call)]
    out = await _up_to_the_callback_question(call)
    outcomes += [out]
    call.phone_says(CALLBACK)
    outcomes += [await call.turn("eight four five, five five five, zero one two three"), await call.answer(True)]

    # The gateway contract has nothing to carry a transfer or a number to dial, and none was sent.
    assert set(TurnResponse.model_fields) == {"lines", "expect_reply", "ticket_id", "stt_prompt"}
    for outcome in outcomes:
        assert set(outcome) <= set(TurnResponse.model_fields)
        assert not any(word in _text(outcome).lower() for word in ("transfer", "dial", "connect you", "hold on"))
    session = await _session(db_session, "prov-notransfer")
    assert session.escalated is False and session.state == VoiceCallState.ANYTHING_ELSE
    assert len(mailbox.sent) == 1  # the one ticket email, and nothing else went out


async def test_a_provider_who_asks_for_a_person_gets_a_high_callback_ticket_not_a_transfer(
    make_call, client, db_session, mailbox
):
    call = make_call("prov-person")
    await _report(call)
    call._say(interpret_name=nlu.TurnResult(escalation_requested=True))  # the model hears a request for a person

    out = await call.turn("I want to talk to a person")

    assert out["ticket_id"] and out["expect_reply"] is False
    heard = _text(out).lower()
    assert "it team call you back" in heard
    assert "transfer" not in heard and "dial" not in heard
    session = await _session(db_session, "prov-person")
    assert session.state == VoiceCallState.ESCALATED
    ticket = await _ticket(client, out["ticket_id"])
    assert (ticket["caller_type"], ticket["category"]["name"], ticket["priority"]) == ("PROVIDER", "Providers", "HIGH")
    assert "CALLBACK REQUESTED" in ticket["description"]
    assert mailbox.sent[0]["body"].startswith(BANNER)


async def test_a_provider_who_hangs_up_keeps_what_they_said(make_call, client, db_session, mailbox):
    call = make_call("prov-hangup")
    await _report(call)  # described, then the call drops while the name is being asked

    response = await client.post(f"{SIP}/status", json={"call_id": "prov-hangup", "reason": "hangup"}, headers=AUTH)

    assert response.status_code == 204
    session = await _session(db_session, "prov-hangup")
    ticket = await _ticket(client, str(session.ticket_id))
    assert (ticket["category"]["name"], ticket["priority"]) == ("Providers", "HIGH")
    assert ticket["description"].startswith("INCOMPLETE VOICE INTAKE")
    assert "eCW keeps freezing" in ticket["description"]
    assert "The provider did not confirm this number" in _intake(ticket)
    assert "did not confirm this number" in mailbox.sent[0]["body"]  # the IT team is not told it was confirmed


async def test_a_failed_ticket_is_never_reported_as_created(make_call, client, db_session, mailbox, monkeypatch):
    call = make_call("prov-fail")
    await _up_to_the_confirmation(call)

    async def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    with monkeypatch.context() as patched:
        patched.setattr(ticket_service, "create_ticket", broken)
        out = await call.answer(True)

    assert _text(out) == scripts.SYSTEM_ERROR
    assert out["ticket_id"] is None and out["expect_reply"] is False
    assert "ticket number" not in _text(out).lower() and "recorded" not in _text(out).lower()
    assert await _ticket_count(db_session) == 0
    assert mailbox.attempts == []

    # The gateway tries the same turn again once the database is back: exactly one ticket, one email.
    retried = await call.answer(True)
    assert retried["ticket_id"]
    assert await _ticket_count(db_session) == 1
    assert len(mailbox.sent) == 1


async def test_a_failed_email_keeps_the_ticket_and_is_retried(make_call, client, db_session, mailbox):
    call = make_call("prov-email-retry")
    mailbox.fail_first = 2

    done = await _file(call)

    assert done["ticket_id"] and "I've recorded your request" in _text(done)  # the caller is not told about the email
    assert await _ticket_count(db_session) == 1
    assert len(mailbox.attempts) == 3 and len(mailbox.sent) == 1  # failed twice, then sent
    assert (await _ticket(client, done["ticket_id"]))["priority"] == "HIGH"


async def test_an_email_that_never_goes_through_still_leaves_the_ticket(make_call, client, db_session, mailbox):
    call = make_call("prov-email-down")
    mailbox.fail_first = 10_000

    done = await _file(call)

    assert done["ticket_id"]
    assert await _ticket_count(db_session) == 1
    assert len(mailbox.attempts) == settings.notification_max_attempts  # the existing retry limit, then it stops
    assert mailbox.sent == []
    assert (await _ticket(client, done["ticket_id"]))["callback_number"] == CALLBACK


async def test_a_repeated_request_from_the_gateway_files_one_ticket_and_sends_one_email(
    make_call, client, db_session, mailbox
):
    call = make_call("prov-dup")
    await _up_to_the_confirmation(call)
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=True, unable_to_determine=False))
    request = {"call_id": "prov-dup", "utterance": "yes", "turn_id": "turn-confirm-1"}

    first = await client.post(f"{SIP}/turn", json=request, headers=AUTH)
    second = await client.post(f"{SIP}/turn", json=request, headers=AUTH)

    assert first.status_code == second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert first.json()["ticket_id"] and second.json()["ticket_id"] == first.json()["ticket_id"]
    assert second.json()["lines"] == first.json()["lines"]
    assert await _ticket_count(db_session) == 1
    assert len(mailbox.sent) == 1


# --- the other flows are untouched ---------------------------------------------------------------------------


async def test_an_employee_call_is_unchanged_with_providers_offered(make_call, db_session):
    call = make_call("prov-employee")
    await call.start()
    await call.turn("I'm an employee.")

    out = await call.describe_it_issue()

    session = await _session(db_session, "prov-employee")
    assert session.collected["caller_type"] == EMPLOYEE
    assert session.state == VoiceCallState.COLLECT_DIAGNOSTICS  # the employee issue questions still run
    assert session.collected.get("priority_rule") != "provider_callback"
    assert scripts.PROVIDER_NAME_ASK not in _text(out)


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

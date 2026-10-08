"""Patient support dual-intake flow: patients get a shorter call with no IT questions.

The agent classifies callers as INTERNAL_IT or PATIENT_SUPPORT after their first
description. Patient calls skip department, email, details, severity, and summary
read-back. The ticket stores caller_type for reporting and lands in a patient category.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import CallerType, Category, Priority, VoiceCallSession, VoiceCallState
from app.voice import nlu, orchestrator, scripts
from tests.test_multi_issue_calls import AUTH, SIP, Call, _async, _session
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER_ID = "+18455550142"
PATIENT_ISSUE = "I'm trying to book an appointment on the website but it's not working."
IT_ISSUE = "My Outlook keeps crashing when I open it."


@pytest.fixture(autouse=True)
def _callback_on(monkeypatch):
    monkeypatch.setattr(settings, "voice_confirm_callback", True)


@pytest.fixture(autouse=True)
async def _seed_patient_categories(db_session):
    for name in ("Other Patient Support", "Appointment Booking", "Patient Portal Login",
                 "Website Error", "Other"):
        existing = (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none()
        if existing is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


class PatientCall(Call):
    """A SIP call that can stub the caller type classification."""

    def __init__(self, client, monkeypatch, call_id, from_number=CALLER_ID):
        super().__init__(client, monkeypatch, call_id)
        self.from_number = from_number

    async def start(self):
        await self.client.post(
            f"{SIP}/start", json={"call_id": self.call_id, "from_number": self.from_number}, headers=AUTH
        )

    def classify_as(self, caller_type, confidence=0.95, patient_category=None):
        result = nlu.CallerClassification(caller_type, confidence, patient_category)
        self.monkeypatch.setattr(nlu, "classify_caller_type", lambda desc: _async(result))

    async def describe_patient_issue(self, issue=PATIENT_ISSUE):
        self.classify_as("PATIENT_SUPPORT", 0.95, "Appointment Booking")
        result = nlu.TurnResult(
            value=issue, confidence="high", unable_to_determine=False, category="Other",
            priority=Priority.MEDIUM,
            extras={"short_issue": "the appointment website"},
        )
        self._say(interpret_description=result)
        return await self.turn(issue)

    async def describe_it_issue(self, issue=IT_ISSUE):
        self.classify_as("INTERNAL_IT", 0.95)
        result = nlu.TurnResult(
            value=issue, confidence="high", unable_to_determine=False, category="Microsoft 365",
            priority=Priority.MEDIUM,
            extras={"short_issue": "your Outlook", "started": "today", "work_blocked": False},
        )
        self._say(interpret_description=result)
        return await self.turn(issue)

    def phone_says(self, number):
        result = nlu.TurnResult() if number is None else nlu.TurnResult(
            value=number, confidence="high", unable_to_determine=False
        )
        self.monkeypatch.setattr(nlu, "interpret_phone", lambda u: _async(result))


@pytest.fixture
def phone_call(client, monkeypatch, _call_fixture):
    def make(call_id, from_number=CALLER_ID):
        return PatientCall(client, monkeypatch, call_id, from_number)
    return make


async def _ticket(client, ticket_id) -> dict:
    return (await client.get(f"/api/v1/tickets/{ticket_id}")).json()


# --- 1. patient flow: name -> phone -> callback -> ticket ---------------------------------


async def test_patient_call_skips_details_department_email(phone_call, client, db_session, sent_emails):
    call = phone_call("patient-1")
    await call.start()
    after_desc = await call.describe_patient_issue()

    # Patient flow: agent asks for name, not details or department.
    assert "name" in " ".join(after_desc["lines"]).lower()
    assert (await _session(db_session, "patient-1")).state == VoiceCallState.COLLECT_NAME

    call._say(interpret_name=nlu.TurnResult(value="John Smith", confidence="high", unable_to_determine=False))
    after_name = await call.turn("John Smith")

    # Should go to phone, not department or email.
    session = await _session(db_session, "patient-1")
    assert session.state in (VoiceCallState.COLLECT_PHONE, VoiceCallState.CONFIRM_CALLBACK_NUMBER)

    # Caller ID was captured, so it goes to callback confirmation
    if session.state == VoiceCallState.CONFIRM_CALLBACK_NUMBER:
        filed = await call.answer(True)
    else:
        call.phone_says(CALLER_ID)
        await call.turn("845 555 0142")
        filed = await call.answer(True)

    assert filed["ticket_id"] is not None
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["caller_type"] == "PATIENT_SUPPORT"
    assert ticket["caller_name"] == "John Smith"
    assert "Appointment Booking" in ticket["category"]["name"]
    assert ticket["priority"] == "MEDIUM"
    # No intake details in description.
    assert "Department:" not in ticket["description"]
    assert "Work blocked:" not in ticket["description"]
    assert "Started:" not in ticket["description"]
    # Email shows Caller / Callback Number / Issue / Summary pattern.
    body = sent_emails[0]["body_html"]
    assert "Caller:<br>" in body
    assert "John Smith" in body


# --- 2. the full IT flow still works ---------------------------------------------------


async def test_it_call_gets_full_flow(phone_call, client, db_session):
    call = phone_call("it-1")
    await call.start()
    after_desc = await call.describe_it_issue()

    # IT flow: details or name question (depending on what was volunteered).
    session = await _session(db_session, "it-1")
    assert session.state in (VoiceCallState.COLLECT_DETAILS, VoiceCallState.COLLECT_NAME)
    assert session.collected.get("caller_type") == "INTERNAL_IT"


# --- 3. low-confidence classification asks a clarifying question -------------------------


async def test_low_confidence_asks_clarification(phone_call, client, db_session):
    call = phone_call("classify-1")
    await call.start()

    call.classify_as("PATIENT_SUPPORT", 0.60)
    result = nlu.TurnResult(
        value="I need help.", confidence="high", unable_to_determine=False, category="Other",
        priority=Priority.MEDIUM, extras={"short_issue": "help"},
    )
    call._say(interpret_description=result)
    after_desc = await call.turn("I need help")

    # Low confidence: ask "patient service or IT issue?"
    text = " ".join(after_desc["lines"])
    assert "patient service" in text.lower() or "employee IT" in text.lower()
    assert (await _session(db_session, "classify-1")).state == VoiceCallState.CLASSIFY_CALLER_TYPE

    # Caller answers "patient"
    call.monkeypatch.setattr(
        nlu, "interpret_caller_type_answer",
        lambda u: _async(nlu.CallerClassification("PATIENT_SUPPORT", 0.95, "Other Patient Support"))
    )
    after_clarify = await call.turn("It's about a patient appointment")

    session = await _session(db_session, "classify-1")
    assert session.collected["caller_type"] == "PATIENT_SUPPORT"
    # Now in the patient flow (name, not details).
    assert session.state == VoiceCallState.COLLECT_NAME


async def test_low_confidence_caller_says_it(phone_call, client, db_session):
    call = phone_call("classify-2")
    await call.start()

    call.classify_as("INTERNAL_IT", 0.60)
    result = nlu.TurnResult(
        value="Something is wrong.", confidence="high", unable_to_determine=False, category="Other",
        priority=Priority.MEDIUM, extras={},
    )
    call._say(interpret_description=result)
    await call.turn("Something is wrong")

    assert (await _session(db_session, "classify-2")).state == VoiceCallState.CLASSIFY_CALLER_TYPE

    call.monkeypatch.setattr(
        nlu, "interpret_caller_type_answer",
        lambda u: _async(nlu.CallerClassification("INTERNAL_IT", 0.95))
    )
    await call.turn("It's a computer issue")

    session = await _session(db_session, "classify-2")
    assert session.collected["caller_type"] == "INTERNAL_IT"
    # IT flow: details or name.
    assert session.state in (VoiceCallState.COLLECT_DETAILS, VoiceCallState.COLLECT_NAME)


# --- 4. silence at classification defaults to IT -----------------------------------------


async def test_silence_at_classification_defaults_to_it(phone_call, client, db_session):
    call = phone_call("classify-silent")
    await call.start()

    call.classify_as("PATIENT_SUPPORT", 0.50)
    result = nlu.TurnResult(
        value="Help.", confidence="high", unable_to_determine=False, category="Other",
        priority=Priority.MEDIUM, extras={},
    )
    call._say(interpret_description=result)
    await call.turn("Help")

    assert (await _session(db_session, "classify-silent")).state == VoiceCallState.CLASSIFY_CALLER_TYPE

    # Silence: empty utterance
    after_silence = await call.turn("")

    session = await _session(db_session, "classify-silent")
    assert session.collected["caller_type"] == "INTERNAL_IT"


# --- 5. patient ticket has no IT-specific fields ----------------------------------------


async def test_patient_ticket_has_no_severity_or_impact(phone_call, client, db_session):
    call = phone_call("patient-no-it")
    await call.start()
    await call.describe_patient_issue("I can't log into the patient portal.")

    call._say(interpret_name=nlu.TurnResult(value="Jane Doe", confidence="high", unable_to_determine=False))
    await call.turn("Jane Doe")
    filed = await call.answer(True)  # callback confirmation

    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["priority"] == "MEDIUM"
    assert "Priority:" not in ticket["description"]
    assert "Impact:" not in ticket["description"]
    assert "Work blocked:" not in ticket["description"]


# --- 6. patient second issue on the same call -----------------------------------------


async def test_patient_second_issue_keeps_caller_type(phone_call, client, db_session):
    call = phone_call("patient-two")
    await call.start()
    await call.describe_patient_issue()

    call._say(interpret_name=nlu.TurnResult(value="John Smith", confidence="high", unable_to_determine=False))
    await call.turn("John Smith")
    first = await call.answer(True)  # callback

    assert first["ticket_id"] is not None
    first_ticket = await _ticket(client, first["ticket_id"])
    assert first_ticket["caller_type"] == "PATIENT_SUPPORT"

    # "Yes, anything else"
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=True, unable_to_determine=False))
    await call.turn("yes")

    # Second patient issue: reclassify patient_category
    call.classify_as("PATIENT_SUPPORT", 0.95, "Patient Portal Login")
    result = nlu.TurnResult(
        value="I also can't log into the patient portal.", confidence="high",
        unable_to_determine=False, category="Other", priority=Priority.MEDIUM,
        extras={"short_issue": "the patient portal"},
    )
    call._say(interpret_description=result)
    after_second = await call.turn("I also can't log into the patient portal")

    # Should skip name (already known) and go to callback or create
    session = await _session(db_session, "patient-two")
    assert session.collected["caller_type"] == "PATIENT_SUPPORT"
    assert session.collected.get("patient_category") == "Patient Portal Login"


# --- 7. patient caller escalation works ------------------------------------------------


async def test_patient_escalation(phone_call, db_session):
    call = phone_call("patient-esc")
    await call.start()
    await call.describe_patient_issue()

    call._say(interpret_name=nlu.TurnResult(
        escalation_requested=True, value=None, unable_to_determine=True
    ))
    await call.turn("let me speak to a person")

    session = await _session(db_session, "patient-esc")
    assert session.state == VoiceCallState.ESCALATED


# --- 8. patient ticket email format ---------------------------------------------------


async def test_patient_email_format(phone_call, client, db_session, sent_emails):
    call = phone_call("patient-email")
    await call.start()

    call.classify_as("PATIENT_SUPPORT", 0.95, "Website Error")
    result = nlu.TurnResult(
        value="The HFMG website is showing an error.", confidence="high",
        unable_to_determine=False, category="Other", priority=Priority.MEDIUM,
        extras={"short_issue": "the website"},
    )
    call._say(interpret_description=result)
    await call.turn("The website is showing an error")

    call._say(interpret_name=nlu.TurnResult(value="Maria Garcia", confidence="high", unable_to_determine=False))
    await call.turn("Maria Garcia")
    filed = await call.answer(True)  # callback

    body = sent_emails[0]["body_html"]
    assert "Caller:<br>\nMaria Garcia" in body
    assert f"Callback Number:<br>\n{CALLER_ID}" in body
    assert "Issue:<br>" in body
    # No IT-specific fields.
    assert "Department" not in body
    assert "Priority" not in body


# --- 9. caller_type is persisted for reporting -----------------------------------------


async def test_caller_type_on_ticket(phone_call, client, db_session):
    call = phone_call("patient-type")
    await call.start()
    await call.describe_patient_issue()

    call._say(interpret_name=nlu.TurnResult(value="Test User", confidence="high", unable_to_determine=False))
    await call.turn("Test User")
    filed = await call.answer(True)

    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["caller_type"] == "PATIENT_SUPPORT"
    assert ticket["category"]["name"] == "Appointment Booking"


async def test_it_caller_type_on_ticket(phone_call, client, db_session):
    call = phone_call("it-type")
    await call.start()
    await call.describe_it_issue()

    # Complete the IT flow minimally
    call._say(interpret_name=nlu.TurnResult(value="IT User", confidence="high", unable_to_determine=False))
    await call.turn("IT User, from billing")
    call._say(interpret_email=nlu.TurnResult(value="it@hfmg.net", confidence="high", unable_to_determine=False))
    await call.turn("it at hfmg dot net")
    await call.answer(True)  # email
    await call.answer(True)  # callback
    filed = await call.answer(True)  # summary

    if filed["ticket_id"]:
        ticket = await _ticket(client, filed["ticket_id"])
        assert ticket["caller_type"] == "INTERNAL_IT"


# --- 10. web tickets are not affected --------------------------------------------------


async def test_web_tickets_have_no_caller_type(client, db_session):
    cat = (await db_session.execute(select(Category).where(Category.name == "Other"))).scalar_one()
    created = (await client.post("/api/v1/tickets", json={
        "caller_name": "Web User", "phone_number": "+15550001111",
        "category_id": str(cat.id), "description": "Portal login issue.",
    })).json()

    assert created["caller_type"] is None


# --- 11. NLU keyword classification fallback ------------------------------------------


async def test_keyword_classification_patient():
    result = nlu._keyword_classify("I need to schedule an appointment on the patient portal")
    assert result is not None
    assert result.caller_type == "PATIENT_SUPPORT"


async def test_keyword_classification_it():
    result = nlu._keyword_classify("My Outlook email keeps crashing and the VPN won't connect")
    assert result is not None
    assert result.caller_type == "INTERNAL_IT"


async def test_keyword_classification_ambiguous():
    result = nlu._keyword_classify("I have a problem")
    assert result is None

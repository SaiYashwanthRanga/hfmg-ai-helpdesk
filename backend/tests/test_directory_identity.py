"""Employee directory in the voice intake: a caller-ID match is proposed, never assumed.

Every employee here is synthetic (example.test, 555 numbers). The directory is the real
service loaded from a temporary CSV; calls run through the real /start and /turn routes with the
language model stubbed, as in the other voice tests.
"""

import asyncio

import pytest
from sqlalchemy import func, select

from app.ai import summarizer
from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket, VoiceCallState
from app.main import app
from app.services.user_directory import DirectoryEntry, LookupStatus, UserDirectoryService
from app.voice import directory_identity, nlu, scripts
from tests.test_multi_issue_calls import AUTH, SIP, PerIssueProvider, _async, _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_patient_intake import PatientCall
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

MARIA, SAM, LEE = "+18455550201", "+18455550206", "+18455550207"
AMBIGUOUS, SHARED_ROOM, LEFT_COMPANY, UNKNOWN = "+18455550204", "+18455550203", "+18455550205", "+18455550299"
PROPOSAL = "I found a directory entry for {name}. Am I speaking with {name}?"


def _directory_rows():
    return [
        row("Maria Example", phone="(845) 555-0201", ext="2001", team="Service, Billing Team", location="Test Clinic",
            email="maria.example@example.test"),
        row("Room 3 Test Clinic", phone="(845) 555-0203", ext="2003", location="Test Clinic"),
        row("Pat One", phone="(845) 555-0204", ext="2004"),
        row("Pat Two", phone="(845) 555-0204", ext="2004"),  # shares both the phone and the extension
        row("Quinn Former", phone="(845) 555-0205", ext="2006", status="Inactive"),
        row("Sam Second", phone="(845) 555-0206", ext="2002", email="sam.second@example.test"),
        row("Lee Third", phone="(845) 555-0207", ext="2010", email="lee.third@example.test"),
    ]


@pytest.fixture
async def directory(tmp_path, monkeypatch):
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, _directory_rows()))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)
    counts = {"phone": 0, "identifier": 0}
    real_phone, real_identifier = service.find_by_phone, service.find_by_identifier

    def counting_phone(phone):
        counts["phone"] += 1
        return real_phone(phone)

    def counting_identifier(identifier):
        counts["identifier"] += 1
        return real_identifier(identifier)

    monkeypatch.setattr(service, "find_by_phone", counting_phone)
    monkeypatch.setattr(service, "find_by_identifier", counting_identifier)
    service.counts = counts
    return service


class DirCall(PatientCall):
    """A SIP call from a chosen caller ID, driven through the employee IT flow."""

    async def start(self):
        self.started = (await self.client.post(
            f"{SIP}/start", json={"call_id": self.call_id, "from_number": self.from_number}, headers=AUTH
        )).json()
        return self.started

    async def report(self, issue="My Outlook keeps crashing when I open it."):
        """Describe an IT issue; start time and work status are volunteered, so no details question."""
        return await self.describe_it_issue(issue)

    async def report_needing_details(self):
        result = nlu.TurnResult(
            value="My Outlook keeps crashing.", confidence="high", unable_to_determine=False,
            category="Microsoft 365", priority=Priority.MEDIUM, extras={"short_issue": "your Outlook"},
        )
        self.classify_as("INTERNAL_IT", 0.95)
        self._say(interpret_description=result)
        return await self.turn("My Outlook keeps crashing.")

    last_said = ""

    async def turn(self, utterance):
        outcome = await super().turn(utterance)
        self.last_said = " ".join(outcome.get("lines") or [])
        return outcome

    async def _past_the_last_name_question(self, name):
        """A caller the caller ID did not find is asked for a last name before the name question. These calls are
        about what comes after, so it is answered with a last name the directory does not have (the usual name
        question follows). The last-name step itself is tested in test_last_name_identity.py."""
        if scripts.LAST_NAME_ASK in self.last_said:
            last = name.split()[-1]
            self._say(interpret_name=nlu.TurnResult(value=last, confidence="high", unable_to_determine=False))
            await self.turn(last)

    async def last_name(self, said, *, heard=None):
        """Answer "What's your last name?". The language model is stubbed to hear `heard` (default: what was said)."""
        self._say(interpret_name=nlu.TurnResult(value=heard or said, confidence="high", unable_to_determine=False))
        return await self.turn(said)

    async def department(self, name, department="Billing"):
        await self._past_the_last_name_question(name)
        self._say(interpret_name=nlu.TurnResult(
            value=name, confidence="high", unable_to_determine=False, extras={"department": department}
        ))
        return await self.turn(department)

    async def name_and_department(self, name, department="Billing"):
        await self._past_the_last_name_question(name)
        self._say(interpret_name=nlu.TurnResult(
            value=name, confidence="high", unable_to_determine=False, extras={"department": department}
        ))
        return await self.turn(f"{name}, {department}")

    async def skip_email(self):
        """Decline email. A caller whose directory entry has a usable address is first offered it
        ("I have ... on file"); "skip" declines that offer and the usual question follows."""
        outcome = await self.turn("skip")
        if not outcome["ticket_id"]:
            outcome = await self.turn("skip")
        return outcome


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture, directory):  # noqa: F811
    def make(call_id, from_number=MARIA):
        return DirCall(client, monkeypatch, call_id, from_number)

    return make


def _said(outcome) -> str:
    return " ".join(outcome["lines"])


async def _confirmed_ticket(call, name="Maria Example"):
    """Start, describe, confirm the proposed name, give the department, skip the email."""
    await call.start()
    await call.report()
    await call.turn("yes")
    await call.department(name)
    return await call.skip_email()


# --- 1. a unique match at call start --------------------------------------------------------------


async def test_nothing_is_looked_up_at_start_and_a_unique_match_is_stored_once_the_call_is_an_employee_call(
    make_call, client, db_session, directory
):
    call = make_call("dir-start")

    started = await call.start()

    assert started == {"lines": [scripts.GREETING], "expect_reply": True, "ticket_id": None, "stt_prompt": started["stt_prompt"]}
    session = await _session(db_session, "dir-start")
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION  # nothing asked yet, nothing confirmed
    assert "directory" not in session.collected  # who is calling is not known yet, so nobody is looked up
    assert directory.counts == {"phone": 0, "identifier": 0}

    await call.report()  # an employee IT call: now the caller ID is looked up

    session = await _session(db_session, "dir-start")
    stored = session.collected["directory"]
    assert (stored["status"], stored["proposed_name"]) == ("matched", "Maria Example")
    assert stored["entry"]["extension"] == "2001" and stored["entry"]["active"] is True
    assert "caller_name" not in session.collected and "identity_confirmed" not in session.collected
    assert directory.counts["phone"] == 1


# --- 2 / 3. the caller confirms or rejects the proposed name ---------------------------------------


async def test_the_caller_confirms_the_proposed_name(make_call, db_session):
    call = make_call("dir-yes")
    await call.start()

    asked = await call.report()

    assert PROPOSAL.format(name="Maria Example") in _said(asked)
    session = await _session(db_session, "dir-yes")
    assert session.state == VoiceCallState.CONFIRM_IDENTITY
    assert "caller_name" not in session.collected  # a proposal until the caller says yes

    next_question = await call.turn("yes")

    session = await _session(db_session, "dir-yes")
    c = session.collected
    assert (c["caller_name"], c["identity_confirmed"]) == ("Maria Example", True)
    assert c["directory_identity"]["extension"] == "2001" and c["directory_identity"]["location"] == "Test Clinic"
    assert c["directory_identity"]["teams"] == ["Service", "Billing Team"] and c["directory_identity"]["active"] is True
    assert c["directory_identity"]["email"] == "maria.example@example.test"
    assert "Maria" in _said(next_question) and "department" in _said(next_question).lower()  # on to the next question


async def test_the_caller_rejects_the_proposed_name_and_it_is_forgotten(make_call, client, db_session, sent_emails):
    call = make_call("dir-no")
    await call.start()
    await call.report()

    asked_again = await call.turn("no")

    session = await _session(db_session, "dir-no")
    assert any(option in _said(asked_again) for option in scripts.NAME_ASK_OPTIONS + (scripts.NAME_ASK,))
    assert session.state == VoiceCallState.COLLECT_NAME
    assert session.collected["directory"] == {"status": "matched", "rejected": True}  # the entry is discarded
    assert "caller_name" not in session.collected and not session.collected.get("identity_confirmed")

    await call.name_and_department("Pat Real")
    filed = await call.skip_email()

    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["caller_name"] == "Pat Real"
    intake = ticket["description"].split("--- Call transcript ---")[0]  # the transcript has the question itself
    for leaked in ("Maria", "2001", "Test Clinic", "Identity:"):
        assert leaked not in intake


# --- 4 / 5 / 6 / 7. no proposal: ambiguous, unknown, no number, directory unavailable -----------------


@pytest.mark.parametrize("from_number, status, count", [
    (AMBIGUOUS, "ambiguous", 2), (UNKNOWN, "not_found", 0), (SHARED_ROOM, "matched", 0), (LEFT_COMPANY, "matched", 0),
])
async def test_without_a_sayable_unique_live_match_the_agent_just_asks_who_is_calling(
    make_call, client, db_session, from_number, status, count
):
    call = make_call(f"dir-none-{status}-{from_number[-2:]}", from_number)
    await call.start()

    asked = await call.report()

    session = await _session(db_session, call.call_id)
    stored = session.collected["directory"]
    assert stored["status"] == status and stored.get("candidate_count", 0) == count
    assert "directory entry" not in _said(asked)
    assert session.state == VoiceCallState.COLLECT_NAME
    # Nobody found by caller ID: a last name is asked for first. A shared line or a former employee is a match
    # that cannot be proposed, and gets the normal name question.
    if status in ("ambiguous", "not_found"):
        assert scripts.LAST_NAME_ASK in _said(asked)
    else:
        assert any(option in _said(asked) for option in scripts.NAME_ASK_OPTIONS)
    assert not session.collected.get("caller_name")
    if status == "ambiguous":
        assert "entry" not in stored  # which records matched is never kept, so none can be picked
    await call.name_and_department("Pat Real")
    filed = await call.skip_email()
    assert (await _ticket(client, filed["ticket_id"]))["caller_name"] == "Pat Real"


@pytest.mark.parametrize("from_number", ["", "anonymous", "unknown", "restricted"])
async def test_a_missing_or_withheld_caller_number_means_no_lookup_and_the_usual_questions(
    make_call, db_session, directory, from_number
):
    call = make_call(f"dir-nonumber-{from_number or 'blank'}", from_number)

    await call.start()
    asked = await call.report()

    session = await _session(db_session, call.call_id)
    assert "directory" not in session.collected
    assert directory.counts == {"phone": 0, "identifier": 0}
    assert session.state == VoiceCallState.COLLECT_NAME and "directory entry" not in _said(asked)


async def test_an_unavailable_directory_changes_nothing_for_the_caller(make_call, db_session, monkeypatch):
    monkeypatch.setattr(app.state, "user_directory", UserDirectoryService(), raising=False)  # never loaded
    call = make_call("dir-unavailable")

    await call.start()
    asked = await call.report()

    session = await _session(db_session, "dir-unavailable")
    assert session.collected["directory"]["status"] == "unavailable"
    assert session.state == VoiceCallState.COLLECT_NAME and "directory entry" not in _said(asked)


async def test_no_directory_configured_at_all_leaves_the_call_exactly_as_it_was(make_call, db_session, monkeypatch):
    monkeypatch.delattr(app.state, "user_directory", raising=False)
    call = make_call("dir-absent")

    await call.start()
    await call.report()

    session = await _session(db_session, "dir-absent")
    assert session.collected["directory"]["status"] == "unavailable"
    assert session.state == VoiceCallState.COLLECT_NAME


# --- 8. a blank or unclear answer -------------------------------------------------------------------------


async def test_an_unclear_answer_is_asked_once_more_then_treated_as_not_confirmed(make_call, db_session):
    call = make_call("dir-unclear")
    await call.start()
    await call.report()

    first = await call.turn("um I don't know")
    assert scripts.IDENTITY_REASK.format(name="Maria Example") in _said(first)
    session = await _session(db_session, "dir-unclear")
    assert session.state == VoiceCallState.CONFIRM_IDENTITY and not session.collected.get("identity_confirmed")

    second = await call.turn("hmm")

    session = await _session(db_session, "dir-unclear")
    assert session.state == VoiceCallState.COLLECT_NAME and scripts.IDENTITY_NOT_CONFIRMED in _said(second)
    assert not session.collected.get("identity_confirmed") and "caller_name" not in session.collected
    assert session.collected["directory"] == {"status": "matched", "rejected": True}


async def test_silence_is_not_a_yes(make_call, db_session):
    call = make_call("dir-silence")
    await call.start()
    await call.report()

    reasked = await call.turn("")

    assert scripts.IDENTITY_REASK.format(name="Maria Example") in _said(reasked)
    session = await _session(db_session, "dir-silence")
    assert session.state == VoiceCallState.CONFIRM_IDENTITY and not session.collected.get("identity_confirmed")
    assert session.misunderstanding_count == 0  # an unclear confirmation never counts toward escalation


async def test_asking_for_a_person_during_the_question_escalates_as_everywhere_else(make_call, db_session):
    call = make_call("dir-human")
    await call.start()
    await call.report()

    outcome = await call.turn("I want to talk to a human")

    assert outcome["expect_reply"] is False
    assert (await _session(db_session, "dir-human")).state == VoiceCallState.ESCALATED


# --- 9. a different identifier ---------------------------------------------------------------------------------


async def test_a_different_extension_proposes_that_employee_instead(make_call, db_session, directory):
    call = make_call("dir-ext")
    await call.start()
    await call.report()

    asked = await call.turn("No, my extension is two zero zero two")

    assert PROPOSAL.format(name="Sam Second") in _said(asked)
    session = await _session(db_session, "dir-ext")
    assert session.state == VoiceCallState.CONFIRM_IDENTITY and session.collected["identity_rounds"] == 2
    assert not session.collected.get("caller_name")  # Maria was never confirmed, and Sam is only proposed
    assert directory.counts["identifier"] == 1

    await call.turn("yes")

    session = await _session(db_session, "dir-ext")
    assert (session.collected["caller_name"], session.collected["directory_identity"]["extension"]) == ("Sam Second", "2002")


async def test_a_different_email_works_the_same_way(make_call, db_session):
    call = make_call("dir-email")
    await call.start()
    await call.report()

    asked = await call.turn("no it's sam.second@example.test")

    assert PROPOSAL.format(name="Sam Second") in _said(asked)


@pytest.mark.parametrize("utterance", [
    "No, extension nine nine nine nine",  # nobody has it
    "No, it's extension two zero zero one",  # the entry already proposed
    "No, extension two zero zero three",  # a shared room phone
    "No, extension two zero zero six",  # a deactivated employee
    "No, extension two zero zero four",  # shared by two records
])
async def test_an_identifier_that_does_not_yield_one_sayable_live_person_falls_back_to_asking(
    make_call, db_session, utterance
):
    call = make_call("dir-id-fallback")
    await call.start()
    await call.report()

    asked = await call.turn(utterance)

    session = await _session(db_session, "dir-id-fallback")
    assert session.state == VoiceCallState.COLLECT_NAME and "directory entry" not in _said(asked)
    assert session.collected["directory"] == {"status": "matched", "rejected": True}


async def test_the_agent_proposes_at_most_two_names_in_one_call(make_call, db_session):
    call = make_call("dir-budget")
    await call.start()
    await call.report()
    await call.turn("No, extension two zero zero two")  # the second proposal (Sam)

    asked = await call.turn("No, my extension is two zero one zero")  # Lee exists, but the budget is spent

    session = await _session(db_session, "dir-budget")
    assert session.state == VoiceCallState.COLLECT_NAME and "directory entry" not in _said(asked)
    assert session.collected["identity_rounds"] == 2 and not session.collected.get("caller_name")


# --- 10. no repeated prompt after confirmation -----------------------------------------------------------------------------


async def test_a_confirmed_identity_is_never_asked_again_and_never_looked_up_again(
    make_call, client, db_session, directory, sent_emails
):
    call = make_call("dir-twice")
    first = await _confirmed_ticket(call)

    await call.answer(True)  # "anything else?" yes
    second = await call.report("My printer is jammed.")

    assert first["ticket_id"] and second["ticket_id"] and first["ticket_id"] != second["ticket_id"]
    assert "directory entry" not in _said(second)  # the second issue went straight to a ticket
    assert (await _session(db_session, "dir-twice")).collected["identity_confirmed"] is True
    assert (await _ticket(client, second["ticket_id"]))["caller_name"] == "Maria Example"
    assert directory.counts["phone"] == 1  # one lookup for the whole call
    assert "Identity:" in (await _ticket(client, second["ticket_id"]))["description"]


async def test_a_retried_start_looks_nobody_up_and_the_routed_call_is_looked_up_once(make_call, directory):
    call = make_call("dir-retry")
    await call.start()
    await call.start()
    await call.start()

    assert directory.counts["phone"] == 0

    await call.report()

    assert directory.counts["phone"] == 1


# --- 11. separate identities on simultaneous calls -----------------------------------------------------------------------------


async def test_simultaneous_calls_keep_their_own_directory_identities(make_call, client, db_session, directory):
    maria, sam, lee = make_call("dir-par-1", MARIA), make_call("dir-par-2", SAM), make_call("dir-par-3", LEE)
    await asyncio.gather(maria.start(), sam.start(), lee.start())

    asked = await asyncio.gather(maria.report(), sam.report(), lee.report())

    for name, outcome in zip(("Maria Example", "Sam Second", "Lee Third"), asked):
        assert PROPOSAL.format(name=name) in _said(outcome)
    await asyncio.gather(maria.turn("yes"), sam.turn("yes"), lee.turn("no"))  # Lee says "no": it is not Lee
    states = [await _session(db_session, f"dir-par-{n}") for n in (1, 2, 3)]
    assert [s.collected.get("caller_name") for s in states] == ["Maria Example", "Sam Second", None]
    assert [s.collected["directory"].get("rejected", False) for s in states] == [False, False, True]
    assert [s.collected.get("directory_identity", {}).get("extension") for s in states] == ["2001", "2002", None]


# --- 12. disconnect cleanup ------------------------------------------------------------------------------------------------------------


async def test_a_disconnect_while_the_name_is_proposed_cleans_up_on_the_existing_path_without_using_it(
    make_call, client, db_session, sent_emails
):
    call = make_call("dir-drop")
    await call.start()
    await call.report()
    assert (await _session(db_session, "dir-drop")).state == VoiceCallState.CONFIRM_IDENTITY

    first = await client.post(f"{SIP}/status", json={"call_id": "dir-drop", "reason": "hangup"}, headers=AUTH)
    again = await client.post(f"{SIP}/status", json={"call_id": "dir-drop", "reason": "hangup"}, headers=AUTH)

    assert first.status_code == again.status_code == 204
    session = await _session(db_session, "dir-drop")
    assert session.state == VoiceCallState.ABANDONED and session.ended_at is not None
    assert not session.collected.get("identity_confirmed")
    tickets = (await db_session.execute(select(Ticket))).scalars().all()
    assert len(tickets) == 1  # salvaged once, however many times the gateway reports the hangup
    assert tickets[0].caller_name == "Unknown caller (voice)"  # the unconfirmed directory name is not used
    assert "Identity:" not in tickets[0].description and "Extension:" not in tickets[0].description


# --- 13. a match, or a yes, is not authorization --------------------------------------------------------------------------------------------


async def test_neither_a_match_nor_a_yes_authenticates_or_authorizes_anything(make_call, client, db_session, sent_emails):
    call = make_call("dir-auth")
    await call.start()
    await call.report()
    before_yes = (await _session(db_session, "dir-auth")).collected
    assert not before_yes.get("identity_confirmed") and "caller_name" not in before_yes  # a match alone confirms nothing

    await call.turn("yes")
    await call.department("Maria Example")
    filed = await call.skip_email()

    collected = (await _session(db_session, "dir-auth")).collected
    assert collected["identity_assurance"] == "caller_confirmed_directory_match"
    assert not {key for key in collected if "authenticated" in key or "authorized" in key or "verified_identity" in key}
    ticket = await _ticket(client, filed["ticket_id"])
    assert "directory entry confirmed by the caller; not authenticated" in ticket["description"]
    assert ticket["priority"] == "MEDIUM"  # who the caller is changes nothing about how urgent the problem is


# --- 14. caller classification stays as it was ----------------------------------------------------------------------------------------------------


async def test_a_low_confidence_classification_is_still_asked_before_any_identity_question(make_call, db_session):
    call = make_call("dir-classify")
    await call.start()
    call.monkeypatch.setattr(nlu, "classify_caller_type", lambda d: _async(nlu.CallerClassification("INTERNAL_IT", 0.5)))
    call._say(interpret_description=nlu.TurnResult(
        value="Something is wrong.", confidence="high", unable_to_determine=False, category="Other",
        priority=Priority.MEDIUM, extras={"short_issue": "something", "started": "today", "work_blocked": True},
    ))

    asked_type = await call.turn("Something is wrong.")
    assert (await _session(db_session, "dir-classify")).state == VoiceCallState.CLASSIFY_CALLER_TYPE
    assert scripts.CLASSIFY_CALLER_TYPE_ASK in _said(asked_type) and "directory entry" not in _said(asked_type)

    asked_name = await call.turn("It's a computer problem")

    session = await _session(db_session, "dir-classify")
    assert session.collected["caller_type"] == "INTERNAL_IT"
    assert PROPOSAL.format(name="Maria Example") in _said(asked_name) and session.state == VoiceCallState.CONFIRM_IDENTITY


async def test_a_directory_match_never_decides_whether_the_caller_is_a_patient(make_call, client, db_session, sent_emails):
    """Maria's number is in the directory, but she is calling about an appointment: patient intake, untouched."""
    for name in ("Other Patient Support", "Appointment Booking"):
        if not (await db_session.execute(select(Category).where(Category.name == name))).first():
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()
    call = make_call("dir-patient")
    await call.start()

    asked = await call.describe_patient_issue()

    session = await _session(db_session, "dir-patient")
    assert session.collected["caller_type"] == "PATIENT_SUPPORT" and session.state == VoiceCallState.COLLECT_NAME
    assert scripts.PATIENT_NAME_ASK in _said(asked) and "directory entry" not in _said(asked)
    call._say(interpret_name=nlu.TurnResult(value="John Smith", confidence="high", unable_to_determine=False))
    filed = await call.turn("John Smith")
    if not filed["ticket_id"]:  # the callback question comes first when it is on; confirm and file
        filed = await call.answer(True)
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["caller_name"] == "John Smith" and ticket["caller_type"] == "PATIENT_SUPPORT"
    assert "Maria" not in ticket["description"].split("--- Call transcript ---")[0] and "Identity:" not in ticket["description"]


# --- 15. issue collection unchanged -----------------------------------------------------------------------------------------------------------------


async def test_the_details_question_still_comes_first_and_the_issue_fields_are_the_same_with_or_without_a_match(
    make_call, client, db_session, sent_emails
):
    matched, unmatched = make_call("dir-issue-a", MARIA), make_call("dir-issue-b", UNKNOWN)
    answers = {}
    for call, who in ((matched, "Maria Example"), (unmatched, "Pat Real")):
        await call.start()
        asked_details = await call.report_needing_details()
        assert (await _session(db_session, call.call_id)).state == VoiceCallState.COLLECT_DETAILS  # unchanged order
        call._say(interpret_details=nlu.TurnResult(
            value="this morning", unable_to_determine=False, extras={"started": "this morning", "work_blocked": False, "details": "It crashes on open."}
        ))
        after_details = await call.turn("this morning, I can still work")
        if call is matched:
            assert PROPOSAL.format(name=who) in _said(after_details)
            await call.turn("yes")
            await call.department(who)
        else:
            await call.name_and_department(who)
        filed = await call.skip_email()
        answers[call.call_id] = await _session(db_session, call.call_id), await _ticket(client, filed["ticket_id"])

    (session_a, ticket_a), (session_b, ticket_b) = answers["dir-issue-a"], answers["dir-issue-b"]
    for field in ("description", "short_issue", "category", "started", "work_blocked", "details", "priority", "caller_type"):
        assert session_a.collected.get(field) == session_b.collected.get(field), field
    assert (ticket_a["priority"], ticket_a["category"]) == (ticket_b["priority"], ticket_b["category"])
    assert "--- Call transcript ---" in ticket_a["description"] and "It crashes on open." in ticket_a["description"]


# --- 16. the ticket's requester fields (there is no Freshservice integration to map to) ------------------------------------------------------------------


async def test_the_ticket_takes_the_confirmed_name_and_nothing_else_about_the_requester_from_the_directory(
    make_call, client, db_session, sent_emails
):
    call = make_call("dir-requester")

    filed = await _confirmed_ticket(call)

    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["caller_name"] == "Maria Example"
    assert ticket["phone_number"] == MARIA and ticket["source"] == "PHONE" and ticket["caller_type"] == "INTERNAL_IT"
    assert ticket["email"] is None  # the directory's email is kept in call context, never filled in for the caller
    assert "requester_user_id" not in ticket
    description = ticket["description"]
    assert "Extension: 2001" in description and "Location: Test Clinic" in description and "Teams: Service, Billing Team" in description


async def test_an_email_the_caller_gives_is_what_the_ticket_uses(make_call, client, db_session, sent_emails):
    call = make_call("dir-own-email")
    await call.start()
    await call.report()
    await call.turn("yes")
    await call.department("Maria Example")
    call._say(interpret_email=nlu.TurnResult(value="m.example@hfmg.net", confidence="high", unable_to_determine=False))
    await call.turn("m dot example at hfmg dot net")
    filed = await call.answer(True)

    assert (await _ticket(client, filed["ticket_id"]))["email"] == "m.example@hfmg.net"


# --- 17. one ticket, however the last turn is retried -----------------------------------------------------------------------------------------------------


async def test_filing_makes_one_ticket_and_a_retried_final_turn_does_not_make_another(make_call, client, db_session, sent_emails):
    call = make_call("dir-dup")
    await call.start()
    await call.report()
    await call.turn("yes")
    await call.department("Maria Example")
    await call.turn("no")  # not the address on file: the usual email question follows
    body = {"call_id": "dir-dup", "utterance": "skip", "turn_id": "turn-final"}

    first = await client.post(f"{SIP}/turn", json=body, headers=AUTH)
    retry = await client.post(f"{SIP}/turn", json=body, headers=AUTH)

    assert first.json()["ticket_id"] and retry.json() == first.json()
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar_one() == 1


# --- 18. summary and email unchanged -------------------------------------------------------------------------------------------------------------------------


async def test_the_summary_is_written_once_and_the_email_has_the_same_shape_with_no_directory_fields(
    make_call, client, db_session, monkeypatch, sent_emails
):
    provider = PerIssueProvider()
    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)
    call = make_call("dir-email-body")
    await call.start()
    await call.report("My Bluetooth is not working.")
    await call.turn("yes")
    await call.department("Maria Example")

    filed = await call.skip_email()

    assert len(provider.calls) == 1  # one summary request, as for any ticket
    assert len(sent_emails) == 1
    body = sent_emails[0]["body_html"]
    assert "Caller:" in body and "Maria Example" in body and "Issue:" in body and "Summary:" in body
    assert "Summary of issue A." in body
    # The office extension is shown like the email: here the one on the directory entry the caller said yes to.
    # Nothing else from the directory reaches the email.
    assert "Extension:<br>\n2001<br>\n" in body
    for absent in ("Location", "Teams", "Identity", "Test Clinic", "directory"):
        assert absent not in body
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["ai_summary"] == "Summary of issue A."


# --- 20. API schemas unchanged ---------------------------------------------------------------------------------------------------------------------------------


async def test_the_start_and_turn_contracts_are_unchanged(make_call, client, db_session, directory):
    schemas = app.openapi()["components"]["schemas"]
    assert set(schemas["StartCallRequest"]["properties"]) == {"call_id", "from_number", "to_number"}
    assert set(schemas["TurnRequest"]["properties"]) == {"call_id", "utterance", "confidence", "turn_id"}
    assert set(schemas["TurnResponse"]["properties"]) == {"lines", "expect_reply", "ticket_id", "stt_prompt"}
    assert set(schemas["TurnReply"]["properties"]) == {"lines", "expect_reply", "ticket_id", "stt_prompt", "turn_id"}

    for from_number in (MARIA, UNKNOWN, ""):  # matched, not found, none: the same four keys every time
        started = await make_call(f"dir-schema{from_number[-3:]}", from_number).start()
        assert set(started) == {"lines", "expect_reply", "ticket_id", "stt_prompt"}
    plain = await client.post(f"{SIP}/turn", json={"call_id": "dir-schema201", "utterance": "hello"}, headers=AUTH)
    assert plain.status_code == 200 and set(plain.json()) == {"lines", "expect_reply", "ticket_id", "stt_prompt"}


async def test_start_still_accepts_a_request_without_a_caller_number(client, _call_fixture, directory):
    response = await client.post(f"{SIP}/start", json={"call_id": "dir-bare"}, headers=AUTH)

    assert response.status_code == 200 and response.json()["expect_reply"] is True


# --- the name the agent says aloud --------------------------------------------------------------------------------------------------------------------------------


def _entry(name, *, location="Test Clinic", teams=("Service",), active=True) -> DirectoryEntry:
    return DirectoryEntry(name=name, email=None, extension=None, teams=tuple(teams), location=location, active=active, source_row=2)


@pytest.mark.parametrize("label, location, teams, expected", [
    ("Maria Example", "Test Clinic", ("Service",), "Maria Example"),
    ("1006 Chandu Example", "Goshen - Exec", (), "Chandu Example"),  # an extension prefix
    ("1001 Exec Dana Sample", "Goshen - Exec", ("Service",), "Dana Sample"),  # extension + the site's own word
    ("Billing Ellis Example", "Goshen - Billing/Accounting", ("Patient Billing",), "Ellis Example"),
    ("1002 70 Hatfield-GI Candy Example", "70 Hatfield GI", ("70Hatfield-FrontDesk",), "Candy Example"),
    ("Slate Hill Tia Example", "Slate Hill", ("SlateHill-FrontDesk",), "Tia Example"),
    ("NWJensen Clara Example", "New Windsor - Jensen", ("NWJensen-Private",), "Clara Example"),  # run together
    ("1042-CornwallOpth Jo Example", "Cornwall - Opthalmology", ("CornwallOpth-Front Desk",), "Jo Example"),
    ("Sam O'Neil", "Test Clinic", (), "Sam O'Neil"),
    ("Jean-Luc Example", "Test Clinic", (), "Jean-Luc Example"),
])
async def test_a_person_name_is_derived_from_a_phone_system_label_when_that_is_safe(label, location, teams, expected):
    assert directory_identity.person_name(_entry(label, location=location, teams=teams)) == expected


@pytest.mark.parametrize("label, location, teams", [
    ("Room 3 Test Clinic", "Test Clinic", ()),
    ("Slate Hill Lab", "Slate Hill", ()),  # leaves a label word
    ("Kitchen Slate Hill", "Slate Hill", ()),
    ("CornwallOpth Maxwell", "Cornwall - Opthalmology", ()),  # two words, but the first is the clinic
    ("1004 IT Abin", "Goshen - Exec", ("IT-Helpdesk",)),  # one real word left
    ("Billing Lead", "Goshen - Billing/Accounting", ()),
    ("Call Park 1", "Test Clinic", ("Call Parks",)),
    ("Dr. Chan Test", "Test Clinic", ()),
    ("User1 Lab", "Test Clinic", ()),
    ("Maria", "Test Clinic", ()),  # one word
    ("Mary Ann Example", "Test Clinic", ()),  # three words: not guessed at
    ("NW Andin Claudia Example", "New Windsor - Andin", ()),  # an abbreviation the data does not explain
    ("", "Test Clinic", ()),
])
async def test_a_label_that_is_not_clearly_a_persons_name_is_never_said_aloud(label, location, teams):
    assert directory_identity.person_name(_entry(label, location=location, teams=teams)) is None


async def test_what_is_stored_for_each_lookup_outcome():
    entry = _entry("Maria Example")
    stored = directory_identity.compact_lookup(
        type("R", (), {"status": LookupStatus.MATCHED, "entry": entry, "candidate_count": 0, "reason": None, "matched": True})()
    )
    assert (stored["status"], stored["proposed_name"], stored["entry"]["active"]) == ("matched", "Maria Example", True)
    inactive = directory_identity.compact_lookup(
        type("R", (), {"status": LookupStatus.MATCHED, "entry": _entry("Maria Example", active=False), "candidate_count": 0, "reason": None, "matched": True})()
    )
    assert inactive["proposed_name"] is None and inactive["reason"] == "inactive_entry"
    assert directory_identity.compact_lookup(None) == {"status": "not_looked_up", "reason": "no_caller_number"}


# --- the name the agent says for an entry of the merged directory (data/directory_merged.csv) ---------------------------------------------------


def _merged_entry(kind, *, display="Maria Example", label="Maria Example", location="Test Clinic", active=True, **fields):
    return DirectoryEntry(
        name=label, email=None, extension="2001", teams=("Service",), location=location, active=active, source_row=2,
        account_type=kind, display_name=display, **fields,
    )


def _matched(entry):
    return type("R", (), {"status": LookupStatus.MATCHED, "entry": entry, "candidate_count": 0, "reason": None, "matched": True})()


@pytest.mark.parametrize("kind, display, expected", [
    ("person", "Maria Example", "Maria Example"),
    ("person", "Rita T. Petrarca", "Rita T. Petrarca"),
    ("person", "Jean-Luc O'Neil", "Jean-Luc O'Neil"),
    ("person", "José Muñoz", "José Muñoz"),
    ("person", "  Maria   Example ", "Maria Example"),
    ("person", "", None),
    ("person", None, None),
    ("person", "Maria", None),  # one word
    ("person", "Akash C", None),  # an initial is not a surname
    ("person", "Front Desk 2", None),  # a digit
    ("person", "maria@example.test", None),  # an address
    ("person", "A Bb Cc Dd Ee", None),  # too many words
    ("shared_line", "Front Desk", None),
    ("call_park", "Call Park", None),
    ("shared_or_service", "IT Helpdesk Team", None),
    ("system_account", "Exchange Admin", None),
    ("test", "Test User", None),
    ("test_or_unassigned", "Test Phone", None),
    ("external_or_student", "Vendor Rep", None),
    ("", "Maria Example", None),  # the column is there but this cell is blank: not a person
    ("something_new", "Maria Example", None),  # a type this code does not know is not a person
])
async def test_only_a_person_is_named_and_by_the_clean_display_name(kind, display, expected):
    assert directory_identity.employee_name(_merged_entry(kind, display=display)) == expected


async def test_a_likely_person_is_named_by_the_label_rule_and_a_plain_entry_exactly_as_before():
    assert directory_identity.employee_name(_merged_entry("likely_person", display=None, label="Maria Example")) == "Maria Example"
    assert directory_identity.employee_name(_merged_entry("likely_person", display=None, label="Room 3 Test Clinic")) is None
    plain = _entry("1006 Chandu Example", location="Goshen - Exec", teams=())
    assert directory_identity.employee_name(plain) == directory_identity.person_name(plain) == "Chandu Example"


async def test_a_persons_clean_name_is_used_instead_of_the_phone_label():
    entry = _merged_entry("person", display="Maximilian Negulici", label="1005 Exec Max Negulici", location="Goshen - Exec")

    assert directory_identity.person_name(entry) == "Max Negulici"  # what the label alone would have said
    assert directory_identity.compact_lookup(_matched(entry))["proposed_name"] == "Maximilian Negulici"


async def test_the_stored_lookup_of_a_merged_entry_carries_the_clean_identity_fields():
    entry = DirectoryEntry(
        name="1005 Exec Max Negulici", email="1005@example.test", extension="1005", teams=("Service", "IT-Helpdesk"),
        location="Goshen - Exec", active=True, source_row=7, account_type="person", display_name="Maximilian Negulici",
        phone="+18455550105", mailbox_email="max.negulici@example.test", department="IT", site="Goshen",
        job_title="IT Helpdesk", credentials=None, is_provider=False, status="Active", match_confidence="high",
    )

    stored = directory_identity.compact_lookup(_matched(entry))

    assert (stored["status"], stored["proposed_name"]) == ("matched", "Maximilian Negulici")
    assert stored["entry"] == {
        "directory_name": "1005 Exec Max Negulici", "email": "max.negulici@example.test",  # the real mailbox, not 1005@
        "extension": "1005", "teams": ["Service", "IT-Helpdesk"], "location": "Goshen - Exec", "active": True,
        "source_row": 7, "account_type": "person", "display_name": "Maximilian Negulici", "phone": "+18455550105",
        "department": "IT", "site": "Goshen", "job_title": "IT Helpdesk", "credentials": None, "is_provider": False,
        "status": "Active", "match_confidence": "high",
    }


async def test_a_plain_entrys_stored_context_is_exactly_what_it_was():
    stored = directory_identity.compact_lookup(_matched(_entry("Maria Example")))

    assert set(stored["entry"]) == {"directory_name", "email", "extension", "teams", "location", "active", "source_row"}


@pytest.mark.parametrize("kind, active, reason", [
    ("shared_line", True, "not_a_person_name"),
    ("call_park", True, "not_a_person_name"),
    ("shared_or_service", True, "not_a_person_name"),
    ("system_account", True, "not_a_person_name"),
    ("test", True, "not_a_person_name"),
    ("person", False, "inactive_entry"),  # deactivated, or a mailbox with no phone-system account
])
async def test_a_merged_entry_that_is_not_a_live_person_is_never_proposed(kind, active, reason):
    stored = directory_identity.compact_lookup(_matched(_merged_entry(kind, active=active)))  # it has a Display Name

    assert stored["proposed_name"] is None and stored["reason"] == reason

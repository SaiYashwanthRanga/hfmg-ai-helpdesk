"""The employee directory belongs to the employee flow only (CALL_FLOW.md 2.2).

The directory is the merged export (data/directory_merged.csv, made by backend/build_directory.py): the phone
system's users plus the mailbox export, with a clean Display Name and an Account Type for every record. Here it
is a synthetic file with the same columns (example.test, 555 numbers), loaded by the real service, and calls run
through the real /start and /turn routes with the language model stubbed, as in test_directory_identity.py.

What is checked: nothing is looked up when a call starts; a call is looked up by its caller ID once it is routed
to the employee flow, and named by the clean Display Name; an extension or address the caller gives is looked up
the same way; shared lines, services, tests, call parks, deactivated users and mailbox-only records are never
named; and patient and provider calls never touch the directory, and run exactly as they do for a caller who is
not in it.
"""

import re
from collections import Counter

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, VoiceCallSession, VoiceCallState
from app.main import app
from app.services.user_directory import UserDirectoryService
from app.voice import nlu, orchestrator, scripts
from tests.test_directory_identity import PROPOSAL, DirCall, _said
from tests.test_multi_issue_calls import AUTH, SIP, _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_provider_intake import _description, _file, _text, mailbox  # noqa: F401
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import MERGED_HEADER, merged_row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

MARIA, SAM, LEE, LIKELY = "+18455550201", "+18455550206", "+18455550207", "+18455550210"
SHARED_LINE, SERVICE_LINE, TEST_LINE, FORMER, AMBIGUOUS, UNKNOWN = (
    "+18455550203", "+18455550208", "+18455550209", "+18455550205", "+18455550204", "+18455550299",
)


def _rows():
    return [
        # A person whose phone label no rule can turn into a name: the clean name comes from the mailbox, and so does the address
        # (the phone system's own is a placeholder).
        merged_row("2001 Billing Mari E", display="Maria Example", phone="(845) 555-0201", ext="2001", email="2001@example.test",
                   mailbox="maria.example@example.org", team="Service, Billing Team", department="Billing", site="Goshen",
                   title="Biller", method="phone_and_name", confidence="medium"),
        # A person whose label is a shorter name than the one on the mailbox.
        merged_row("Sam Second", display="Samuel Second", phone="(845) 555-0206", ext="2002", email="sam.second@example.org",
                   mailbox="sam.second@example.org", department="Billing", site="Goshen", method="email_exact",
                   confidence="high"),
        # A provider, as far as the directory goes: that never decides which flow a call is in.
        merged_row("Lee Third MD", display="Lee Third", phone="(845) 555-0207", ext="2010", email="lee.third@example.org",
                   mailbox="lee.third@example.org", credentials="MD", title="Physician", provider="Y",
                   method="email_exact", confidence="high"),
        merged_row("Front Desk Test Clinic", kind="shared_line", phone="(845) 555-0203", ext="2003"),
        merged_row("Helpdesk Line", kind="shared_or_service", display="IT Helpdesk Team", phone="(845) 555-0208", ext="2008",
                   mailbox="helpdesk@example.test"),
        merged_row("Test Phone Four", kind="test", display="Test Four", phone="(845) 555-0209", ext="2009"),
        merged_row("Call Park 1", kind="call_park", ext="2090"),
        merged_row("Quinn Former", display="Quinn Former", status="Inactive", phone="(845) 555-0205", ext="2006"),
        merged_row("Pat One", display="Pat One", phone="(845) 555-0204", ext="2004"),
        merged_row("Pat Two", display="Pat Two", phone="(845) 555-0204", ext="2004"),  # shares both the phone and the extension
        merged_row("Pat Likely", kind="likely_person", phone="(845) 555-0210", ext="2011"),  # no mailbox backs the label up
        # A mailbox with no phone-system account: a person, but not a live one.
        merged_row("Mailbox Person", display="Mailbox Person", status="Unknown", email="mailbox.person@example.test",
                   mailbox="mailbox.person@example.test"),
    ]


@pytest.fixture
async def merged_directory(tmp_path, monkeypatch):
    """The real service loaded from a merged-layout file, counting every way a call can consult it."""
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, _rows(), header=MERGED_HEADER, name="directory_merged.csv"))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)
    calls = Counter()

    def counting(name, real):
        def wrapper(key):
            calls[name] += 1
            return real(key)

        return wrapper

    for name in ("find_by_phone", "find_by_extension", "find_by_email", "find_by_identifier"):
        monkeypatch.setattr(service, name, counting(name, getattr(service, name)))
    service.calls = calls
    return service


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture, merged_directory):  # noqa: F811
    def make(call_id, from_number=MARIA):
        return DirCall(client, monkeypatch, call_id, from_number)

    return make


async def _seed(db_session, *names):
    for name in names:
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


@pytest.fixture
def caller_type_asked(monkeypatch):
    """The production opening question: "a patient, an employee, or a provider?" """
    monkeypatch.setattr(settings, "voice_ask_caller_type", True)
    monkeypatch.setattr(settings, "voice_provider_flow", True)


@pytest.fixture
async def provider_flow(monkeypatch, db_session, caller_type_asked):
    """The provider flow as test_provider_intake.py runs it."""
    for name in ("voice_diagnostic_questions", "voice_ask_extension", "voice_confirm_summary", "voice_confirm_callback"):
        monkeypatch.setattr(settings, name, True)
    await _seed(db_session, "Providers", "eClinicalWorks", "Microsoft 365", "Network", "Printer", "Password", "Other")


def _agent_lines(session) -> list[str]:
    """What the agent said, with every digit masked: the callback number it reads back is the caller's own."""
    return [re.sub(r"\d", "#", t["text"]) for t in session.turns if t["role"] == "agent"]


# --- 1. nothing is looked up when a call starts ------------------------------------------------------------------


@pytest.mark.parametrize("ask_caller_type", [False, True])
async def test_a_call_that_has_only_started_is_looked_up_by_nobody(
    make_call, db_session, merged_directory, monkeypatch, ask_caller_type
):
    monkeypatch.setattr(settings, "voice_ask_caller_type", ask_caller_type)
    call = make_call(f"emp-start-{ask_caller_type}")

    await call.start()
    await call.start()  # a retried start

    session = await _session(db_session, call.call_id)
    assert sum(merged_directory.calls.values()) == 0
    assert "directory" not in session.collected
    assert session.collected["phone_number"] == MARIA  # the caller ID is kept for the callback number, as ever


# --- 2. an employee call is looked up once it is routed there ---------------------------------------------------


async def test_the_caller_id_is_looked_up_when_the_caller_says_they_are_an_employee(
    make_call, db_session, merged_directory, caller_type_asked
):
    call = make_call("emp-routed")
    await call.start()
    assert merged_directory.calls["find_by_phone"] == 0

    await call.turn("I'm an employee.")

    session = await _session(db_session, "emp-routed")
    assert session.collected["caller_type"] == "INTERNAL_IT"
    assert merged_directory.calls["find_by_phone"] == 1
    assert session.collected["directory"]["status"] == "matched"
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION  # still the problem question: nothing is proposed yet

    await call.report()
    assert merged_directory.calls["find_by_phone"] == 1  # once for the whole call


async def test_an_employee_call_without_the_opening_question_is_looked_up_when_it_is_classified(
    make_call, db_session, merged_directory
):
    call = make_call("emp-classified")
    await call.start()
    assert merged_directory.calls["find_by_phone"] == 0

    await call.report()  # the description is classified as an employee IT problem

    assert merged_directory.calls["find_by_phone"] == 1
    assert (await _session(db_session, "emp-classified")).collected["directory"]["status"] == "matched"


async def test_a_replayed_turn_does_not_look_the_caller_up_again(make_call, client, merged_directory, caller_type_asked):
    call = make_call("emp-replay")
    await call.start()
    body = {"call_id": "emp-replay", "utterance": "I'm an employee.", "turn_id": "turn-route"}

    first = await client.post(f"{SIP}/turn", json=body, headers=AUTH)
    again = await client.post(f"{SIP}/turn", json=body, headers=AUTH)

    assert again.headers["Idempotent-Replayed"] == "true" and again.json() == first.json()
    assert merged_directory.calls["find_by_phone"] == 1


async def test_a_caller_who_switches_flows_and_back_is_looked_up_once(
    make_call, db_session, merged_directory, caller_type_asked
):
    call = make_call("emp-switch")
    await call.start()
    await call.turn("I'm an employee.")
    await call.turn("Actually, I'm a patient.")
    assert merged_directory.calls["find_by_phone"] == 1  # the patient flow does not look anyone up

    await call.turn("Actually, I'm an employee.")

    assert (await _session(db_session, "emp-switch")).collected["caller_type"] == "INTERNAL_IT"
    assert merged_directory.calls["find_by_phone"] == 1


async def test_a_directory_that_fails_never_ends_an_employee_call(
    make_call, db_session, merged_directory, monkeypatch, caplog
):
    def broken(_key):
        raise RuntimeError("secret detail that must not be logged")

    monkeypatch.setattr(merged_directory, "find_by_phone", broken)
    call = make_call("emp-broken", MARIA)
    await call.start()

    with caplog.at_level("INFO"):
        asked = await call.report()

    session = await _session(db_session, "emp-broken")
    assert session.collected["directory"] == {"status": "unavailable", "reason": "lookup_error"}
    assert session.state == VoiceCallState.COLLECT_NAME and "directory entry" not in _said(asked)  # the normal name question
    assert session.misunderstanding_count == 0 and session.state != VoiceCallState.ESCALATED
    assert "RuntimeError" in caplog.text and "secret detail" not in caplog.text  # the kind of failure, never what it held


# --- 3. the employee is named by the clean Display Name, and the details come from the merged file ---------------


async def test_the_employee_is_named_by_the_clean_display_name_and_the_call_keeps_the_directory_details(
    make_call, client, db_session, sent_emails
):
    call = make_call("emp-sam", SAM)
    await call.start()

    asked = await call.report()

    assert PROPOSAL.format(name="Samuel Second") in _said(asked)  # the mailbox name, not the label "Sam Second"
    assert "Sam Second" not in _said(asked)
    await call.turn("yes")
    await call.department("Samuel Second")
    filed = await call.skip_email()

    session = await _session(db_session, "emp-sam")
    assert (session.collected["caller_name"], session.collected["identity_confirmed"]) == ("Samuel Second", True)
    who = session.collected["directory_identity"]
    assert (who["display_name"], who["account_type"], who["status"], who["active"]) == ("Samuel Second", "person", "Active", True)
    assert (who["phone"], who["extension"], who["email"]) == ("+18455550206", "2002", "sam.second@example.org")
    assert (who["department"], who["site"], who["location"], who["teams"]) == ("Billing", "Goshen", "Test Clinic", ["Service"])
    assert who["is_provider"] is False and who["match_confidence"] == "high"
    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["caller_name"], ticket["caller_type"]) == ("Samuel Second", "INTERNAL_IT")
    assert "Extension: 2002" in ticket["description"] and "Location: Test Clinic" in ticket["description"]


async def test_a_person_whose_phone_label_is_not_a_name_is_still_named_and_their_mailbox_address_is_offered(
    make_call, db_session
):
    call = make_call("emp-maria", MARIA)  # the label is "2001 Billing Mari E": the old rule could not name her
    await call.start()

    asked = await call.report()

    assert PROPOSAL.format(name="Maria Example") in _said(asked)
    await call.turn("yes")
    offered = await call.department("Maria Example")
    who = (await _session(db_session, "emp-maria")).collected["directory_identity"]
    assert who["email"] == "maria.example@example.org"  # the real mailbox, not the phone system's "2001@"
    assert scripts.EMAIL_DIRECTORY_CONFIRM.format(email=scripts.spoken_email("maria.example@example.org")) in _said(offered)


@pytest.mark.parametrize("from_number, name", [(LIKELY, "Pat Likely"), (LEE, "Lee Third")])
async def test_a_likely_person_and_a_directory_provider_are_proposed_and_the_call_stays_an_employee_call(
    make_call, db_session, from_number, name
):
    call = make_call(f"emp-named-{from_number[-2:]}", from_number)
    await call.start()

    asked = await call.report()

    assert PROPOSAL.format(name=name) in _said(asked)
    session = await _session(db_session, call.call_id)
    assert session.collected["caller_type"] == "INTERNAL_IT"  # directory membership never classifies a call


# --- 4. lookup by extension and by address (the caller gives one instead of saying yes) -----------------------------


async def test_an_extension_the_caller_gives_is_looked_up_in_the_directory(make_call, db_session, merged_directory):
    call = make_call("emp-ext", MARIA)
    await call.start()
    await call.report()

    asked = await call.turn("No, my extension is two zero zero two")

    assert PROPOSAL.format(name="Samuel Second") in _said(asked)
    assert merged_directory.calls["find_by_identifier"] == 1
    session = await _session(db_session, "emp-ext")
    assert session.state == VoiceCallState.CONFIRM_IDENTITY and not session.collected.get("caller_name")
    await call.turn("yes")
    session = await _session(db_session, "emp-ext")
    assert (session.collected["caller_name"], session.collected["directory_identity"]["extension"]) == ("Samuel Second", "2002")


@pytest.mark.parametrize("said, name", [
    ("no it's sam.second@example.org", "Samuel Second"),  # the mailbox address and the phone system's are the same
    ("no it's maria.example@example.org", "Maria Example"),  # the mailbox address; the phone system has "2001@"
])
async def test_an_address_the_caller_gives_is_looked_up_by_the_mailbox_address_too(make_call, said, name):
    call = make_call(f"emp-mail-{name[:3]}", LEE)
    await call.start()
    await call.report()

    asked = await call.turn(said)

    assert PROPOSAL.format(name=name) in _said(asked)


@pytest.mark.parametrize("said_name", ["Samuel Second", "Sam Second"])
async def test_the_extension_question_compares_the_callers_name_with_the_directory(
    make_call, client, monkeypatch, sent_emails, said_name
):
    monkeypatch.setattr(settings, "voice_ask_extension", True)
    call = make_call(f"emp-extq-{said_name[:3]}", UNKNOWN)  # a number the directory does not have: the caller says who they are
    await call.start()
    await call.report()
    await call.name_and_department(said_name)

    await call.turn("2002")  # Samuel Second's extension
    filed = await call.skip_email()

    ticket = await _ticket(client, filed["ticket_id"])
    assert "Extension: 2002 (given by the caller; matches the directory entry for this caller's name)" in ticket["description"]


# --- 5. only a live person is named --------------------------------------------------------------------------------


@pytest.mark.parametrize("from_number, status, reason", [
    (SHARED_LINE, "matched", "not_a_person_name"),  # a front desk
    (SERVICE_LINE, "matched", "not_a_person_name"),  # a service mailbox, though it has a display name
    (TEST_LINE, "matched", "not_a_person_name"),  # a test account, though it has a display name
    (FORMER, "matched", "inactive_entry"),  # a deactivated user
    (AMBIGUOUS, "ambiguous", "duplicate_active"),  # two people share the number
    (UNKNOWN, "not_found", "no_match"),
])
async def test_a_record_that_is_not_a_live_person_is_never_named(make_call, db_session, from_number, status, reason):
    call = make_call(f"emp-none-{from_number[-3:]}", from_number)
    await call.start()

    asked = await call.report()

    session = await _session(db_session, call.call_id)
    stored = session.collected["directory"]
    assert (stored["status"], stored["reason"]) == (status, reason) and not stored.get("proposed_name")
    assert session.state == VoiceCallState.COLLECT_NAME and "directory entry" not in _said(asked)  # the normal name question


@pytest.mark.parametrize("identifier", [
    "No, it's mailbox.person@example.test",  # a mailbox with no phone-system account
    "No, extension two zero zero three",  # a front desk
    "No, extension two zero zero eight",  # a service line
    "No, extension two zero zero nine",  # a test account
    "No, extension two zero nine zero",  # a call park
    "No, extension two zero zero six",  # a deactivated user
])
async def test_an_identifier_that_belongs_to_no_live_person_falls_back_to_asking(make_call, db_session, identifier):
    call = make_call("emp-id-none", MARIA)
    await call.start()
    await call.report()

    asked = await call.turn(identifier)

    session = await _session(db_session, "emp-id-none")
    assert session.state == VoiceCallState.COLLECT_NAME and "directory entry" not in _said(asked)
    assert session.collected["directory"] == {"status": "matched", "rejected": True}
    assert not session.collected.get("caller_name")


# --- 6. patient and provider calls never touch the directory ----------------------------------------------------


async def test_a_patient_call_never_touches_the_directory_and_is_the_same_for_every_caller_id(
    make_call, client, db_session, merged_directory, caller_type_asked, sent_emails
):
    await _seed(db_session, "Other Patient Support", "Appointment Booking")
    said = {}
    for call_id, number in (("emp-patient-listed", MARIA), ("emp-patient-unlisted", UNKNOWN)):  # Maria is in the directory
        call = make_call(call_id, number)
        await call.start()
        await call.turn("I'm a patient, I need to cancel my appointment.")
        call._say(interpret_name=nlu.TurnResult(value="John Smith", confidence="high", unable_to_determine=False))
        filed = await call.turn("John Smith")
        if not filed["ticket_id"]:
            filed = await call.answer(True)
        ticket = await _ticket(client, filed["ticket_id"])
        assert (ticket["caller_name"], ticket["caller_type"]) == ("John Smith", "PATIENT_SUPPORT")
        assert "Maria" not in ticket["description"].split("--- Call transcript ---")[0]
        session = await _session(db_session, call_id)
        assert "directory" not in session.collected and "directory_identity" not in session.collected
        said[call_id] = _agent_lines(session)[:-1]  # all but the closing line, which has the ticket number in it

    assert sum(merged_directory.calls.values()) == 0
    assert said["emp-patient-listed"] == said["emp-patient-unlisted"]


async def test_a_patient_whose_description_is_classified_is_not_looked_up_either(
    make_call, db_session, merged_directory, sent_emails
):
    await _seed(db_session, "Other Patient Support", "Appointment Booking")
    call = make_call("emp-patient-classified", MARIA)
    await call.start()

    await call.describe_patient_issue()

    session = await _session(db_session, "emp-patient-classified")
    assert session.collected["caller_type"] == "PATIENT_SUPPORT" and session.state == VoiceCallState.COLLECT_NAME
    assert sum(merged_directory.calls.values()) == 0 and "directory" not in session.collected


async def test_a_provider_call_never_touches_the_directory_and_is_the_same_for_every_caller_id(
    make_call, client, db_session, merged_directory, provider_flow, mailbox
):
    said = {}
    for call_id, number in (("emp-provider-listed", MARIA), ("emp-provider-unlisted", UNKNOWN)):  # Maria is in the directory
        call = make_call(call_id, number)
        done = await _file(call)  # the provider's own steps; it fails if the name question is not the plain one
        ticket = await _ticket(client, done["ticket_id"])
        assert (ticket["caller_type"], ticket["category"]["name"], ticket["priority"]) == ("PROVIDER", "Providers", "HIGH")
        assert ticket["caller_name"] == "Ana Rivera"
        session = await _session(db_session, call_id)
        assert "directory" not in session.collected and "directory_identity" not in session.collected
        said[call_id] = _agent_lines(session)[:-1]  # all but the closing line, which has the ticket number in it

    assert sum(merged_directory.calls.values()) == 0
    assert said["emp-provider-listed"] == said["emp-provider-unlisted"]


async def test_an_employee_who_becomes_a_provider_is_not_offered_a_directory_name(
    make_call, db_session, merged_directory, provider_flow
):
    call = make_call("emp-to-provider", MARIA)
    await call.start()
    await call.turn("I'm an employee.")
    assert merged_directory.calls["find_by_phone"] == 1  # an employee call at that point

    corrected = await call.turn("Actually, I'm a provider.")
    assert _text(corrected) == f"{scripts.TYPE_CORRECTED} {scripts.PROVIDER_ASK}"
    said = "eCW keeps freezing when I open the orders tab."
    _description(call, said)
    asked = await call.turn(said)

    assert _text(asked).endswith(scripts.PROVIDER_NAME_ASK) and "directory entry" not in _text(asked)
    session = await _session(db_session, "emp-to-provider")
    assert session.collected["caller_type"] == "PROVIDER" and not session.collected.get("caller_name")
    assert merged_directory.calls["find_by_phone"] == 1


async def test_a_provider_who_becomes_an_employee_is_looked_up_then(make_call, db_session, merged_directory, provider_flow):
    call = make_call("emp-from-provider", MARIA)
    await call.start()
    await call.turn("This is Dr. Rivera, I'm a provider.")
    assert sum(merged_directory.calls.values()) == 0

    await call.turn("Actually, I'm an employee.")

    session = await _session(db_session, "emp-from-provider")
    assert session.collected["caller_type"] == "INTERNAL_IT"
    assert merged_directory.calls["find_by_phone"] == 1 and session.collected["directory"]["status"] == "matched"


@pytest.mark.parametrize("caller_type, consulted, note", [
    ("INTERNAL_IT", 1, "unverified: the directory lists this extension for a different entry"),
    ("PROVIDER", 0, None),  # a provider can still say "my extension is 2002" late in the call
    ("PATIENT_SUPPORT", 0, None),
    (None, 0, None),  # not routed yet
])
async def test_only_an_employee_call_checks_an_extension_against_the_directory(
    merged_directory, caller_type, consulted, note
):
    collected = {} if caller_type is None else {"caller_type": caller_type}
    session = VoiceCallSession(call_id="emp-late-ext", state=VoiceCallState.CONFIRM_CALLBACK_NUMBER, collected=collected, turns=[])

    orchestrator._record_extension(session, "2002", "caller", merged_directory)

    assert merged_directory.calls["find_by_extension"] == consulted
    assert (session.collected["extension"], session.collected["extension_source"]) == ("2002", "caller")  # kept as said
    assert session.collected["extension_note"] == note


# --- 7. the app's configured file is the one an employee call uses -------------------------------------------------------


async def test_an_employee_call_uses_the_directory_the_app_loaded_from_its_configured_file(
    tmp_path, client, monkeypatch, _call_fixture  # noqa: F811
):
    path = write_csv(tmp_path, _rows(), header=MERGED_HEADER, name="directory_merged.csv")
    monkeypatch.setattr(settings, "user_directory_csv_path", str(path))
    monkeypatch.setattr(app.state, "user_directory", None, raising=False)  # put back as it was when the test ends

    async with app.router.lifespan_context(app):
        call = DirCall(client, monkeypatch, "emp-configured", SAM)
        await call.start()
        asked = await call.report()

    assert PROPOSAL.format(name="Samuel Second") in _said(asked)

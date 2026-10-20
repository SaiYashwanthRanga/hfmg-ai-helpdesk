"""The helpdesk notification shows how to reach the caller, including their email address.

Only an address the caller (or the directory, with the caller's yes) actually confirmed is ever shown; anything
else reads "Not provided". Real /start and /turn routes, the real notification builder and the real ticket
pipeline; only the language model and the outgoing mail are stubbed. Synthetic callers (example.org, 555 numbers).
"""

import pytest
from sqlalchemy import select

from app.ai import summarizer
from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket
from app.main import app
from app.notifications.email import _build_body
from app.services.user_directory import UserDirectoryService
from app.voice import nlu
from tests.test_directory_identity import AUTH, SIP, DirCall
from tests.test_multi_issue_calls import PerIssueProvider, _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
DANA, STRANGER = "+18455550301", "+18455550399"
NO_EMAIL_LINE = "Email:<br>\nNot provided"


# --- the notification text --------------------------------------------------------------------------------


async def test_the_body_lists_name_phone_email_issue_summary_and_ticket_number_in_that_order():
    body = _build_body(
        "Maria Lopez", "+18455550142", "Printer jammed.", "The printer is jammed.",
        email="maria.lopez@example.org", ticket_number="HFMG-2026-000123",
    )

    assert body == (
        "Caller:\nMaria Lopez\n\nCallback Number:\n+18455550142\n\nEmail:\nmaria.lopez@example.org\n\n"
        "Issue:\nPrinter jammed.\n\nSummary:\nThe printer is jammed.\n\nTicket Number:\nHFMG-2026-000123"
    )


@pytest.mark.parametrize("email", [None, "", "   "])
async def test_a_missing_email_reads_not_provided_rather_than_being_left_out_or_filled_in(email):
    body = _build_body("Maria Lopez", "+18455550142", "Printer jammed.", None, email=email)

    assert "Email:\nNot provided" in body
    assert "@" not in body


async def test_the_other_fields_are_still_left_out_when_there_is_nothing_to_show():
    assert _build_body(None, None, "Printer jammed.", None) == "Email:\nNot provided\n\nIssue:\nPrinter jammed."


# --- the pipeline ----------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _patient_categories(db_session):
    for name in ("Appointment Booking", "Other Patient Support", "Other"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


@pytest.fixture
async def directory(tmp_path, monkeypatch):
    rows = [
        row("Dana Sample", phone="(845) 555-0301", email="dana.sample@example.org", location="Test Clinic"),
        row("Hal Mismatch", phone="(845) 555-0302", email="someone.else@example.org", location="Test Clinic"),
    ]
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, rows))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)
    return service


@pytest.fixture
def summaries(monkeypatch):
    provider = PerIssueProvider()
    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)
    return provider


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture, directory):
    def make(call_id, number=STRANGER):
        return DirCall(client, monkeypatch, call_id, number)

    return make


async def _to_email_question(call, name="Pat Real", issue="My Bluetooth is not working."):
    """An employee nobody proposed a name for: start, report the problem, give name and department."""
    await call.start()
    await call.report(issue)
    return await call.name_and_department(name)


def _body(sent_emails) -> str:
    assert len(sent_emails) == 1, "exactly one helpdesk notification per ticket"
    return sent_emails[0]["body_html"]


async def test_a_confirmed_caller_provided_address_is_in_the_notification_with_everything_else(
    make_call, client, db_session, sent_emails, summaries
):
    call = make_call("mail-provided")
    await _to_email_question(call)
    await call.turn("pat dot real at h f m g dot net")

    filed = await call.turn("yes")

    ticket = await _ticket(client, filed["ticket_id"])
    body = _body(sent_emails)
    assert ticket["email"] == "pat.real@hfmg.net"
    assert "Caller:<br>\nPat Real" in body and f"Callback Number:<br>\n{STRANGER}" in body
    assert "Email:<br>\npat.real@hfmg.net" in body
    assert "Issue:<br>\nMy Bluetooth is not working." in body and "Summary:<br>\nSummary of issue A." in body
    assert f"Ticket Number:<br>\n{ticket['ticket_number']}" in body
    assert len(summaries.calls) == 1  # the one summary the ticket always had: the email adds no model call
    assert "Intake details" not in body and "Call transcript" not in body


async def test_the_email_follows_the_callback_number_so_it_is_next_to_the_other_ways_to_reach_the_caller(
    make_call, client, db_session, sent_emails, summaries
):
    call = make_call("mail-order")
    await _to_email_question(call)
    await call.turn("pat dot real at h f m g dot net")
    await call.turn("yes")

    body = _body(sent_emails)

    assert body.index("Callback Number") < body.index("Email:") < body.index("Issue:") < body.index("Summary:") < body.index("Ticket Number:")


async def test_a_confirmed_directory_address_is_in_the_notification(make_call, client, db_session, sent_emails, summaries):
    call = make_call("mail-directory", DANA)
    await call.start()
    await call.report()
    await call.turn("yes")  # "Am I speaking with Dana Sample?"
    offered = await call.department("Dana Sample")
    assert "on file" in " ".join(offered["lines"])

    filed = await call.turn("yes")  # "Is that still the best email?"

    assert (await _ticket(client, filed["ticket_id"]))["email"] == "dana.sample@example.org"
    body = _body(sent_emails)
    assert "Caller:<br>\nDana Sample" in body and "Email:<br>\ndana.sample@example.org" in body


async def test_a_directory_address_that_is_not_the_callers_is_never_used_and_the_caller_is_asked(
    make_call, client, db_session, sent_emails, summaries
):
    call = make_call("mail-directory-wrong", "+18455550302")
    await call.start()
    await call.report()
    await call.turn("yes")  # "Am I speaking with Hal Mismatch?"
    asked = await call.department("Hal Mismatch")
    assert "on file" not in " ".join(asked["lines"]) and "say it naturally, spell it, or say skip" in " ".join(asked["lines"])

    await call.turn("hal at h f m g dot net")
    await call.turn("yes")

    body = _body(sent_emails)
    assert "someone.else@example.org" not in body and "Email:<br>\nhal@hfmg.net" in body


async def test_a_declined_directory_address_is_replaced_by_the_one_the_caller_gives(make_call, client, db_session, sent_emails, summaries):
    call = make_call("mail-directory-decline", DANA)
    await call.start()
    await call.report()
    await call.turn("yes")
    await call.department("Dana Sample")
    await call.turn("no")  # not the address on file

    await call.turn("d sample at h f m g dot net")
    await call.turn("yes")

    body = _body(sent_emails)
    assert "Email:<br>\ndsample@hfmg.net" in body and "dana.sample@example.org" not in body


async def test_a_caller_who_skips_email_gets_not_provided_never_an_invented_address(make_call, client, db_session, sent_emails, summaries):
    call = make_call("mail-skipped")
    await _to_email_question(call)

    filed = await call.turn("skip")

    assert (await _ticket(client, filed["ticket_id"]))["email"] is None
    body = _body(sent_emails)
    assert NO_EMAIL_LINE in body and "@" not in body.split("Issue:")[0].replace(STRANGER, "")


async def test_a_corrected_address_is_what_is_sent_never_the_one_that_was_read_back_wrong(
    make_call, client, db_session, sent_emails, summaries
):
    call = make_call("mail-corrected")
    await _to_email_question(call)
    await call.turn("m lopes at h f m g dot net")  # heard "lopes"
    await call.turn("no")
    await call.turn("the part before the at sign")
    await call.turn("m l o p e z")

    await call.turn("yes")

    body = _body(sent_emails)
    assert "Email:<br>\nmlopez@hfmg.net" in body and "mlopes" not in body


# --- an address that was not confirmed is never shown -------------------------------------------------------


async def test_an_address_still_being_confirmed_when_the_caller_says_goodbye_is_not_shown(
    make_call, client, db_session, sent_emails, summaries
):
    call = make_call("mail-unconfirmed-bye")
    await _to_email_question(call)
    await call.turn("pat dot real at h f m g dot net")  # read back, waiting for a yes

    ended = await call.turn("goodbye")

    assert ended["ticket_id"]
    ticket = await _ticket(client, ended["ticket_id"])
    assert ticket["email"] is None
    body = _body(sent_emails)
    assert NO_EMAIL_LINE in body and "pat.real@hfmg.net" not in body


async def test_an_address_still_being_confirmed_when_the_caller_hangs_up_is_not_shown(
    make_call, client, db_session, sent_emails, summaries
):
    call = make_call("mail-unconfirmed-hangup")
    await _to_email_question(call)
    await call.turn("pat dot real at h f m g dot net")

    await client.post(f"{SIP}/status", json={"call_id": "mail-unconfirmed-hangup", "reason": "hangup"}, headers=AUTH)

    tickets = (await db_session.execute(select(Ticket))).scalars().all()
    assert len(tickets) == 1 and tickets[0].email is None
    body = _body(sent_emails)
    assert NO_EMAIL_LINE in body and "pat.real@hfmg.net" not in body


async def test_an_address_still_being_confirmed_when_the_caller_asks_for_a_person_is_not_shown(
    make_call, client, db_session, sent_emails, summaries
):
    call = make_call("mail-unconfirmed-human")
    await _to_email_question(call)
    await call.turn("pat dot real at h f m g dot net")

    handed_off = await call.turn("I want to talk to a human")

    assert handed_off["ticket_id"]
    assert (await _ticket(client, handed_off["ticket_id"]))["email"] is None
    assert "pat.real@hfmg.net" not in _body(sent_emails)


async def test_an_address_the_caller_rejected_and_never_replaced_is_not_shown(make_call, client, db_session, sent_emails, summaries):
    call = make_call("mail-rejected")
    await _to_email_question(call)
    await call.turn("pat dot real at h f m g dot net")
    await call.turn("no")
    await call.turn("both")  # start over

    filed = await call.turn("skip")

    assert (await _ticket(client, filed["ticket_id"]))["email"] is None
    assert NO_EMAIL_LINE in _body(sent_emails) and "pat.real@hfmg.net" not in _body(sent_emails)


async def test_a_confirmed_address_is_kept_for_a_second_issue_on_the_same_call(make_call, client, db_session, sent_emails, summaries):
    call = make_call("mail-two-issues")
    await _to_email_question(call)
    await call.turn("pat dot real at h f m g dot net")
    first = await call.turn("yes")

    await call.turn("yes")  # anything else?
    second = await call.report("My printer is jammed.")

    assert (await _ticket(client, first["ticket_id"]))["email"] == "pat.real@hfmg.net"
    assert (await _ticket(client, second["ticket_id"]))["email"] == "pat.real@hfmg.net"
    assert len(sent_emails) == 2 and all("Email:<br>\npat.real@hfmg.net" in e["body_html"] for e in sent_emails)


# --- tickets that did not come from an employee call ---------------------------------------------------------------


async def test_a_patient_request_says_not_provided_because_no_address_is_asked_of_a_patient(
    make_call, client, db_session, sent_emails, monkeypatch
):
    monkeypatch.setattr(settings, "voice_ask_caller_type", True)
    call = make_call("mail-patient")
    await call.start()
    await call.turn("I'm a patient")
    await call.turn("I need to cancel my appointment")
    call._say(interpret_name=nlu.TurnResult(value="Ann Patient", confidence="high", unable_to_determine=False))
    await call.turn("Ann Patient")

    await call.turn("yes")  # the callback number

    body = _body(sent_emails)
    assert "Caller:<br>\nAnn Patient" in body and NO_EMAIL_LINE in body


async def test_a_web_ticket_shows_the_address_from_the_form_and_not_provided_without_one(client, category_id, sent_emails):
    with_email = await client.post("/api/v1/tickets", json={
        "caller_name": "Web Person", "phone_number": "+15550001111", "email": "web.person@example.org",
        "category_id": str(category_id), "description": "Back office has no wifi.",
    })
    without = await client.post("/api/v1/tickets", json={
        "caller_name": "Other Person", "phone_number": "+15550002222",
        "category_id": str(category_id), "description": "The printer is jammed.",
    })

    assert with_email.status_code == without.status_code == 201
    first, second = (e["body_html"] for e in sent_emails)
    assert "Email:<br>\nweb.person@example.org" in first and NO_EMAIL_LINE not in first
    assert NO_EMAIL_LINE in second

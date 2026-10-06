"""Symptom details survive on the ticket, so nothing depends on the transcript.

The caller's answer to "when did this start?" often carries the symptom itself
("I can't connect my AirPods"). That sentence is kept as a Details section;
the summary is written from it, never from the transcript. The email carries only
the issue and that summary; the details stay on the ticket.
"""

import html

import pytest
from sqlalchemy import select

from app.ai import summarizer
from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket
from app.notifications import email as email_module
from app.notifications import factory
from app.services import hfmg_mail_service, ticket_service
from app.services.ticket_text import (
    TRANSCRIPT_MARKER,
    extract_details,
    extract_issue,
    without_transcript,
)
from app.voice import nlu, orchestrator
from app.voice.session import get_or_create_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

CALLER_ID = "+18455550142"

BLUETOOTH = {
    "issue": "The Bluetooth in the caller's laptop is not working.",
    "said": "It started this morning and I can't connect my AirPods to my laptop.",
    "started": "this morning",
    "details": "Cannot connect AirPods to the laptop.",
}
HOTSPOT = {
    "issue": "The caller's laptop hotspot is not turning on.",
    "said": "Yesterday it was working fine, but today I can't turn it on.",
    "started": "today",
    "details": "Worked yesterday, not today.",
}


async def _async(value):
    return value


async def _seed_categories(db):
    for name in ("Other",):
        if (await db.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db.commit()


async def _call(db, monkeypatch, case, *, details_result, sid):
    """A whole intake call; the caller answers the details question with `case["said"]`."""
    await _seed_categories(db)
    session = await get_or_create_session(db, call_sid=sid, from_number=CALLER_ID, to_number="+18455559999")
    await orchestrator.start_call(session)
    await db.commit()

    # No start time or work status volunteered, so the details question is asked.
    first = nlu.TurnResult(
        value=case["issue"], confidence="high", unable_to_determine=False, category="Other",
        priority=Priority.MEDIUM, extras={"short_issue": "your laptop"},
    )
    monkeypatch.setattr(nlu, "interpret_description", lambda u: _async(first))
    await orchestrator.handle_turn(db, session, utterance=case["issue"])

    monkeypatch.setattr(nlu, "interpret_details", lambda u, **k: _async(details_result))
    await orchestrator.handle_turn(db, session, utterance=case["said"])

    name = nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False)
    monkeypatch.setattr(nlu, "interpret_name", lambda u, **k: _async(name))
    await orchestrator.handle_turn(db, session, utterance="Maria Lopez")

    skip = nlu.TurnResult(value="mlopez@hfmg.net", confidence="high", unable_to_determine=False)
    monkeypatch.setattr(nlu, "interpret_email", lambda u: _async(skip))
    await orchestrator.handle_turn(db, session, utterance="m lopez at hfmg dot net")
    monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(nlu.TurnResult(yes_no=True, unable_to_determine=False)))
    await orchestrator.handle_turn(db, session, utterance="yes")
    await db.commit()

    assert session.ticket_id is not None
    return await db.get(Ticket, session.ticket_id)


def _details_reply(case):
    return nlu.TurnResult(
        value=case["started"], unable_to_determine=False,
        extras={"started": case["started"], "work_blocked": True, "affected_scope": None, "details": case["details"]},
    )


# --- the ticket keeps the details ------------------------------------------------


@pytest.mark.parametrize("case", [BLUETOOTH, HOTSPOT], ids=["bluetooth", "hotspot"])
async def test_details_are_stored_in_their_own_section(db_session, monkeypatch, case):
    ticket = await _call(db_session, monkeypatch, case, details_result=_details_reply(case), sid=f"CA-det-{case['started']}")

    description = ticket.description
    assert description.index(case["issue"]) < description.index("--- Details ---") \
        < description.index("--- Intake details ---") < description.index(TRANSCRIPT_MARKER)
    assert extract_issue(description) == case["issue"]
    assert extract_details(description) == case["details"]


@pytest.mark.parametrize("case", [BLUETOOTH, HOTSPOT], ids=["bluetooth", "hotspot"])
async def test_details_survive_without_the_transcript(db_session, monkeypatch, case):
    ticket = await _call(db_session, monkeypatch, case, details_result=_details_reply(case), sid=f"CA-wo-{case['started']}")

    # The transcript is still stored, unchanged, with the caller's own words...
    assert case["said"] in ticket.description.split(TRANSCRIPT_MARKER)[1]
    # ...but everything above it already carries the symptom.
    kept = without_transcript(ticket.description)
    assert case["details"] in kept and TRANSCRIPT_MARKER not in kept and case["said"] not in kept


async def test_summarizer_input_has_the_details_and_no_transcript(db_session, monkeypatch):
    ticket = await _call(db_session, monkeypatch, BLUETOOTH, details_result=_details_reply(BLUETOOTH), sid="CA-sum-1")
    seen = []

    class Provider:
        is_configured = True

        async def structured(self, **kwargs):
            seen.append(kwargs["user"])
            return {"summary": "Bluetooth is down; AirPods will not pair."}

    monkeypatch.setattr(summarizer, "get_provider", lambda: Provider())
    ticket = await ticket_service.get_ticket(db_session, ticket.id)
    assert await summarizer.summarize_ticket(ticket) == "Bluetooth is down; AirPods will not pair."

    prompt = seen[0]
    assert "Cannot connect AirPods to the laptop." in prompt  # Details
    assert "Department:" in prompt or "Started: this morning" in prompt  # Intake details
    assert TRANSCRIPT_MARKER not in prompt
    assert "Agent:" not in prompt and "Caller:" not in prompt
    assert "Maria Lopez" not in prompt and CALLER_ID not in prompt


async def test_email_has_only_issue_and_summary_not_details(db_session, monkeypatch):
    ticket = await _call(db_session, monkeypatch, HOTSPOT, details_result=_details_reply(HOTSPOT), sid="CA-mail-1")
    await ticket_service.set_ai_summary(db_session, ticket.id, summary="Hotspot stopped working today.", failed=False)
    sent = []

    async def fake_send_email(**kwargs):
        sent.append(kwargs)
        return True

    monkeypatch.setattr(settings, "org_base", "http://mail.test")
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    monkeypatch.setattr(settings, "enable_email_notifications", True)
    monkeypatch.setattr(settings, "helpdesk_email", "helpdesk@hfmg.net")
    monkeypatch.setattr(hfmg_mail_service, "send_email", fake_send_email)
    factory.get_email_provider.cache_clear()

    await email_module.send_ticket_notification(await ticket_service.get_ticket(db_session, ticket.id), "created")

    assert sent[0]["body_html"] == (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">'
        f"Issue:<br>\n{html.escape(HOTSPOT['issue'])}<br>\n<br>\n"
        "Summary:<br>\nHotspot stopped working today.</div>"
    )
    assert "Details" not in sent[0]["body_html"] and HOTSPOT["details"] not in sent[0]["body_html"]
    # The ticket itself still has them.
    stored = await ticket_service.get_ticket(db_session, ticket.id)
    assert extract_details(stored.description) == HOTSPOT["details"]


async def test_ticket_is_searchable_by_a_detail(db_session, monkeypatch):
    await _call(db_session, monkeypatch, BLUETOOTH, details_result=_details_reply(BLUETOOTH), sid="CA-search-1")
    items, total = await ticket_service.list_tickets(
        db_session, page=1, page_size=25, ticket_status=None, category_id=None, priority=None, source=None, q="AirPods"
    )
    assert total >= 1


# --- when there is nothing (or the model failed) ----------------------------------


async def test_no_details_means_no_section(db_session, monkeypatch):
    reply = _details_reply({**BLUETOOTH, "details": None})
    ticket = await _call(db_session, monkeypatch, BLUETOOTH, details_result=reply, sid="CA-none-1")

    assert "--- Details ---" not in ticket.description
    assert extract_details(ticket.description) == ""


async def test_failed_model_call_keeps_a_substantive_answer(db_session, monkeypatch):
    # The model call failed outright: no extras at all. Don't lose the sentence.
    ticket = await _call(db_session, monkeypatch, BLUETOOTH, details_result=nlu.TurnResult(), sid="CA-fail-1")

    assert extract_details(ticket.description) == BLUETOOTH["said"]


async def test_failed_model_call_ignores_a_trivial_answer(db_session, monkeypatch):
    case = {**BLUETOOTH, "said": "This morning."}
    ticket = await _call(db_session, monkeypatch, case, details_result=nlu.TurnResult(), sid="CA-fail-2")

    assert "--- Details ---" not in ticket.description


async def test_a_second_issue_does_not_inherit_the_first_issues_details(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await get_or_create_session(db_session, call_sid="CA-two-1", from_number=CALLER_ID, to_number="+1")
    orchestrator.update_collected(session, details="Cannot connect AirPods to the laptop.")

    second = nlu.TurnResult(value="The printer is jammed.", confidence="high", unable_to_determine=False, category="Other")
    orchestrator._record_description(session, second)

    assert session.collected["details"] is None


# --- the model call and text helpers -----------------------------------------------


async def test_interpret_details_asks_for_and_returns_the_details(monkeypatch):
    asked = {}

    async def fake(system, user, name, properties):
        asked.update(name=name, properties=properties)
        return {"started": "today", "work_blocked": None, "affected_scope": None,
                "details": "  Worked yesterday, not today. ", "unable_to_determine": False}

    monkeypatch.setattr(nlu, "_call_structured", fake)
    result = await nlu.interpret_details("it worked yesterday but not today", asked="When did this start?")

    assert "details" in asked["properties"]
    assert result.extras["details"] == "Worked yesterday, not today."


async def test_text_helpers_on_a_full_voice_description():
    description = (
        "NEEDS TRIAGE REVIEW - work impact not confirmed.\n\nThe printer is jammed.\n\n"
        "--- Details ---\nPaper tray 2 only.\n\n--- Intake details ---\nDepartment: Billing\n\n"
        f"{TRANSCRIPT_MARKER}\nAgent: hi\nCaller: printer"
    )
    assert extract_issue(description) == "The printer is jammed."
    assert extract_details(description) == "Paper tray 2 only."
    assert "Paper tray 2 only." in without_transcript(description)
    assert "Agent: hi" not in without_transcript(description)


async def test_text_helpers_leave_a_web_form_ticket_alone():
    description = "Cannot connect to the clinic wifi since this morning."
    assert extract_issue(description) == description
    assert extract_details(description) == ""
    assert without_transcript(description) == description

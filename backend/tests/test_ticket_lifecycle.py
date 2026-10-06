"""Ticket lifecycle regression matrix: every way a ticket is created or updated.

For each path it pins what the summary is made from (one LLM request at most),
whether the email goes out, whether `ai_summary` is stored and what the dashboard
(GET /tickets/{id}) returns. See test_ticket_followup.py for the failure details.
"""

import pytest

from app.ai import summarizer
from app.core.config import get_settings
from app.db.base import async_session_factory
from app.db.models import Priority
from app.services.ticket_text import TRANSCRIPT_MARKER, extract_issue
from app.voice import nlu
from tests.test_ticket_details import BLUETOOTH, _details_reply, _seed_categories
from tests.test_ticket_followup import CountingProvider, sent_emails  # noqa: F401
from tests.test_voice_simulator import _full_intake, _simulator_on, categories  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
SUMMARY = "Summary generated once."
SIP = "/api/v1/voice/sip"
AUTH = {"Authorization": "Bearer lifecycle-token"}


async def _async(value):
    return value


def _summaries_on(monkeypatch, provider):
    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)


async def _dashboard(client, ticket_id) -> dict:
    return (await client.get(f"/api/v1/tickets/{ticket_id}")).json()


def _web_payload(category_id) -> dict:
    return {
        "caller_name": "Ann",
        "phone_number": "+15550001111",
        "category_id": str(category_id),
        "description": "Cannot connect to the clinic wifi since this morning.",
    }


async def _sip_call_to_ticket(client, monkeypatch, call_id) -> str:
    """A whole SIP call, answered by stubs, up to the turn that files the ticket."""
    monkeypatch.setattr(settings, "voice_sip_gateway_token", "lifecycle-token")
    async with async_session_factory() as db:
        await _seed_categories(db)

    async def turn(utterance):
        return (await client.post(f"{SIP}/turn", json={"call_id": call_id, "utterance": utterance}, headers=AUTH)).json()

    await client.post(f"{SIP}/start", json={"call_id": call_id, "from_number": "+18455550142"}, headers=AUTH)
    first = nlu.TurnResult(
        value=BLUETOOTH["issue"], confidence="high", unable_to_determine=False, category="Other",
        priority=Priority.MEDIUM, extras={"short_issue": "your laptop"},
    )
    monkeypatch.setattr(nlu, "interpret_description", lambda u: _async(first))
    await turn(BLUETOOTH["issue"])
    monkeypatch.setattr(nlu, "interpret_details", lambda u, **k: _async(_details_reply(BLUETOOTH)))
    await turn(BLUETOOTH["said"])
    name = nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False)
    monkeypatch.setattr(nlu, "interpret_name", lambda u, **k: _async(name))
    await turn("Maria Lopez")
    email = nlu.TurnResult(value="m@hfmg.net", confidence="high", unable_to_determine=False)
    monkeypatch.setattr(nlu, "interpret_email", lambda u: _async(email))
    await turn("m at hfmg dot net")
    monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(nlu.TurnResult(yes_no=True, unable_to_determine=False)))
    return (await turn("yes"))["ticket_id"]


# --- 1. voice --------------------------------------------------------------------


async def test_voice_call_summarizes_once_emails_once_and_keeps_the_transcript(client, monkeypatch, sent_emails):
    provider = CountingProvider({"summary": SUMMARY})
    _summaries_on(monkeypatch, provider)

    ticket = await _dashboard(client, await _sip_call_to_ticket(client, monkeypatch, "life-voice-1"))

    assert len(provider.calls) == 1 and len(sent_emails) == 1
    assert ticket["ai_summary"] == SUMMARY and SUMMARY in sent_emails[0]["body_html"]
    assert TRANSCRIPT_MARKER in ticket["description"] and "--- Details ---" in ticket["description"]
    # The summary was written from the ticket text above the transcript, never the transcript.
    assert TRANSCRIPT_MARKER not in provider.calls[0]["user"] and "Cannot connect AirPods" in provider.calls[0]["user"]

    # The closing turn ("no, nothing else") does no further work.
    monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(nlu.TurnResult(yes_no=False, unable_to_determine=False)))
    await client.post(f"{SIP}/turn", json={"call_id": "life-voice-1", "utterance": "no"}, headers=AUTH)
    assert len(provider.calls) == 1 and len(sent_emails) == 1


async def test_asking_for_a_person_after_the_ticket_exists_does_not_repeat_the_work(client, monkeypatch, sent_emails):
    provider = CountingProvider({"summary": SUMMARY})
    _summaries_on(monkeypatch, provider)
    await _sip_call_to_ticket(client, monkeypatch, "life-voice-2")

    wants_person = nlu.TurnResult(unable_to_determine=True, escalation_requested=True)
    monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(wants_person))
    response = await client.post(f"{SIP}/turn", json={"call_id": "life-voice-2", "utterance": "a person please"}, headers=AUTH)

    assert response.json()["ticket_id"]  # the gateway is still told which ticket
    assert len(provider.calls) == 1 and len(sent_emails) == 1  # but no second summary or email


# --- 2. simulator ------------------------------------------------------------------


async def test_simulator_ticket_follows_the_same_path_and_email_is_opt_in(client, categories, monkeypatch, sent_emails):
    provider = CountingProvider({"summary": SUMMARY})
    _summaries_on(monkeypatch, provider)
    monkeypatch.setattr(settings, "simulator_allow_notifications", True)

    _, body = await _full_intake(client)  # default: notifications off
    quiet = await _dashboard(client, body["session"]["ticket"]["id"])
    assert (len(provider.calls), len(sent_emails)) == (1, 0)
    assert quiet["ai_summary"] == SUMMARY and quiet["source"] == "SIMULATOR" and TRANSCRIPT_MARKER in quiet["description"]

    _, body = await _full_intake(client, send_notifications=True)
    loud = await _dashboard(client, body["session"]["ticket"]["id"])
    assert (len(provider.calls), len(sent_emails)) == (2, 1)  # one more request, one email
    assert loud["ai_summary"] == SUMMARY and SUMMARY in sent_emails[0]["body_html"]


# --- 3/6/7. web, summary on and off --------------------------------------------------


async def test_web_ticket_with_summary_enabled(client, category_id, monkeypatch, sent_emails):
    provider = CountingProvider({"summary": SUMMARY})
    _summaries_on(monkeypatch, provider)

    created = (await client.post("/api/v1/tickets", json=_web_payload(category_id))).json()

    ticket = await _dashboard(client, created["id"])
    assert (len(provider.calls), len(sent_emails)) == (1, 1)
    assert ticket["ai_summary"] == SUMMARY and ticket["ai_summary_status"] == "COMPLETED"
    assert SUMMARY in sent_emails[0]["body_html"] and "Cannot connect to the clinic wifi" in sent_emails[0]["body_html"]


async def test_web_ticket_with_summary_disabled_still_emails_the_issue(client, category_id, monkeypatch, sent_emails):
    provider = CountingProvider({"summary": SUMMARY})
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)  # enable_ai_summary stays off

    created = (await client.post("/api/v1/tickets", json=_web_payload(category_id))).json()

    ticket = await _dashboard(client, created["id"])
    assert (len(provider.calls), len(sent_emails)) == (0, 1)
    assert ticket["ai_summary"] is None and ticket["ai_summary_status"] == "DISABLED"
    assert "Summary" not in sent_emails[0]["body_html"]


# --- 4/5. status change and regeneration ---------------------------------------------


async def test_status_change_and_regeneration(client, category_id, monkeypatch, sent_emails):
    provider = CountingProvider({"summary": SUMMARY})
    _summaries_on(monkeypatch, provider)
    ticket_id = (await client.post("/api/v1/tickets", json=_web_payload(category_id))).json()["id"]

    await client.post(f"/api/v1/tickets/{ticket_id}/status", json={"status": "IN_PROGRESS"})
    assert len(provider.calls) == 1 and len(sent_emails) == 2  # emailed, from the stored summary
    assert SUMMARY in sent_emails[1]["body_html"]

    provider.result = {"summary": "Regenerated summary."}
    response = await client.post(f"/api/v1/tickets/{ticket_id}/regenerate-summary")
    assert response.status_code == 202
    assert len(provider.calls) == 2 and len(sent_emails) == 2  # one request, no email
    assert (await _dashboard(client, ticket_id))["ai_summary"] == "Regenerated summary."

    # A later status email carries the regenerated text, so email and dashboard stay in step.
    await client.post(f"/api/v1/tickets/{ticket_id}/status", json={"status": "RESOLVED"})
    assert "Regenerated summary." in sent_emails[2]["body_html"]


# --- 8. failure paths -----------------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    [CountingProvider(None), CountingProvider(error=RuntimeError("down"))],
    ids=["no-result", "raises"],
)
async def test_a_failed_summary_never_blocks_the_ticket_or_email(client, category_id, monkeypatch, sent_emails, provider):
    _summaries_on(monkeypatch, provider)

    created = (await client.post("/api/v1/tickets", json=_web_payload(category_id))).json()

    ticket = await _dashboard(client, created["id"])
    assert len(provider.calls) == 1 and len(sent_emails) == 1  # one try, no fallback
    assert ticket["ai_summary"] is None and ticket["ai_summary_status"] == "FAILED"
    assert "Summary" not in sent_emails[0]["body_html"]


async def test_unconfigured_llm_makes_no_request_but_still_emails(client, category_id, monkeypatch, sent_emails):
    provider = CountingProvider({"summary": SUMMARY})
    provider.is_configured = False
    _summaries_on(monkeypatch, provider)

    created = (await client.post("/api/v1/tickets", json=_web_payload(category_id))).json()

    assert provider.calls == [] and len(sent_emails) == 1
    assert (await _dashboard(client, created["id"]))["ai_summary_status"] == "FAILED"


# --- the issue shown in the email is never blank ----------------------------------------


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("VPN DOWN - cannot connect from home", "VPN DOWN - cannot connect from home"),  # web ticket, not an agent note
        ("OUTLOOK ERROR - 0x800CCC0E when sending", "OUTLOOK ERROR - 0x800CCC0E when sending"),
        (
            "CALLBACK REQUESTED - caller asked to speak with a person.\n\n--- Call transcript ---\nCaller: person",
            "CALLBACK REQUESTED - caller asked to speak with a person.",  # nothing else was said
        ),
        (
            "NEEDS TRIAGE REVIEW - work impact not confirmed.\n\nCALLBACK REQUESTED - caller asked to speak with a person."
            "\n\nPrinter jammed.\n\n--- Details ---\nTray 2\n\n--- Call transcript ---\nx",
            "Printer jammed.",
        ),
    ],
)
async def test_issue_is_never_blank_and_only_real_agent_notes_are_dropped(description, expected):
    assert extract_issue(description) == expected

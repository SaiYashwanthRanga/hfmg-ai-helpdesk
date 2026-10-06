"""Summarize once, then notify: the summary is generated a single time, saved on
the ticket, and the email and dashboard both use that saved text."""

import uuid

import pytest

from app.ai import summarizer, ticket_followup
from app.core.config import get_settings
from app.db.models import AISummaryStatus
from app.notifications import factory
from app.services import hfmg_mail_service, ticket_service
from app.voice import tasks as voice_tasks

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

SUMMARY = "Back office lost wifi; staff cannot reach the scheduling system."
DESCRIPTION = (
    "NEEDS TRIAGE REVIEW - work impact not confirmed.\n\n"
    "The back office has no wifi.\n\n"
    "--- Intake details ---\nDepartment: Billing\nPriority: High: no network\n\n"
    "--- Call transcript ---\nAgent: How can I help?\nCaller: the wifi is down"
)


class CountingProvider:
    """Stands in for the LLM and counts every request made to it."""

    is_configured = True

    def __init__(self, result=None, *, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = []

    async def structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def sent_emails(monkeypatch):
    """Route notifications through the real email builder and capture what is sent."""
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
    return sent


def _enable_summary(monkeypatch, provider):
    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)


async def _create(client, category_id) -> dict:
    response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Ann",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": DESCRIPTION,
        },
    )
    assert response.status_code == 201
    return response.json()


async def test_summary_is_generated_once_and_shared_by_email_and_dashboard(
    client, category_id, monkeypatch, sent_emails
):
    provider = CountingProvider({"summary": SUMMARY})
    _enable_summary(monkeypatch, provider)

    created = await _create(client, category_id)  # background work has run when this returns

    assert len(provider.calls) == 1  # one LLM request for dashboard + email together
    stored = (await client.get(f"/api/v1/tickets/{created['id']}")).json()
    assert stored["ai_summary"] == SUMMARY and stored["ai_summary_status"] == "COMPLETED"

    assert len(sent_emails) == 1
    body = sent_emails[0]["body_html"]
    assert body == (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">'
        f"Issue:<br>\nThe back office has no wifi.<br>\n<br>\nSummary:<br>\n{SUMMARY}</div>"
    )
    assert stored["ai_summary"] in body  # the emailed text is the stored text
    # The ticket itself keeps everything the email leaves out.
    assert "--- Call transcript ---" in stored["description"]
    assert "Department: Billing" in stored["description"]


async def test_email_is_sent_after_the_summary_is_saved(db_session, category_id, monkeypatch):
    provider = CountingProvider({"summary": SUMMARY})
    _enable_summary(monkeypatch, provider)
    seen = []

    async def spy(ticket, event):
        seen.append((event, ticket.ai_summary, ticket.ai_summary_status))

    monkeypatch.setattr(ticket_followup, "send_ticket_notification", spy)
    ticket = await _make_ticket(db_session, category_id)

    await ticket_followup.summarize_then_notify(ticket.id)

    assert seen == [("created", SUMMARY, AISummaryStatus.COMPLETED)]
    assert len(provider.calls) == 1


async def test_failed_summary_still_sends_issue_only_without_a_second_request(
    client, category_id, monkeypatch, sent_emails
):
    provider = CountingProvider(None)  # the provider gave nothing usable
    _enable_summary(monkeypatch, provider)

    created = await _create(client, category_id)

    assert len(provider.calls) == 1  # no fallback request
    stored = (await client.get(f"/api/v1/tickets/{created['id']}")).json()
    assert stored["ai_summary"] is None and stored["ai_summary_status"] == "FAILED"
    assert len(sent_emails) == 1
    assert sent_emails[0]["body_html"] == (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">'
        "Issue:<br>\nThe back office has no wifi.</div>"
    )


async def test_crashing_summary_still_creates_the_ticket_and_sends_the_email(
    client, category_id, monkeypatch, sent_emails
):
    provider = CountingProvider(error=RuntimeError("boom"))
    _enable_summary(monkeypatch, provider)

    created = await _create(client, category_id)

    assert len(provider.calls) == 1
    stored = (await client.get(f"/api/v1/tickets/{created['id']}")).json()
    assert stored["ai_summary_status"] == "FAILED"
    assert len(sent_emails) == 1 and "Summary" not in sent_emails[0]["body_html"]


async def test_summaries_disabled_makes_no_llm_request_and_still_emails(
    client, category_id, monkeypatch, sent_emails
):
    provider = CountingProvider({"summary": SUMMARY})
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)  # enable_ai_summary stays off

    created = await _create(client, category_id)

    assert provider.calls == []
    assert created["ai_summary_status"] == "DISABLED"
    assert len(sent_emails) == 1 and "Summary" not in sent_emails[0]["body_html"]


async def test_email_failure_does_not_lose_the_summary(db_session, category_id, monkeypatch):
    provider = CountingProvider({"summary": SUMMARY})
    _enable_summary(monkeypatch, provider)

    async def broken(ticket, event):
        raise RuntimeError("mail down")

    monkeypatch.setattr(ticket_followup, "send_ticket_notification", broken)
    ticket = await _make_ticket(db_session, category_id)

    await ticket_followup.summarize_then_notify(ticket.id)  # must not raise

    await db_session.refresh(ticket)
    assert ticket.ai_summary == SUMMARY


async def test_send_email_false_summarizes_but_does_not_email(db_session, category_id, monkeypatch):
    provider = CountingProvider({"summary": SUMMARY})
    _enable_summary(monkeypatch, provider)
    seen = []

    async def spy(ticket, event):
        seen.append(event)

    monkeypatch.setattr(ticket_followup, "send_ticket_notification", spy)
    ticket = await _make_ticket(db_session, category_id)

    await ticket_followup.summarize_then_notify(ticket.id, send_email=False)

    assert seen == [] and len(provider.calls) == 1
    await db_session.refresh(ticket)
    assert ticket.ai_summary == SUMMARY


async def test_voice_dispatch_queues_a_single_task(db_session):
    from fastapi import BackgroundTasks

    background = BackgroundTasks()
    ticket_id = uuid.uuid4()
    await voice_tasks.dispatch_ticket_tasks(db_session, background, ticket_id)

    assert [(t.func, t.args) for t in background.tasks] == [(ticket_followup.summarize_then_notify, (ticket_id,))]


async def test_status_change_email_reuses_the_saved_summary_without_the_llm(
    client, category_id, monkeypatch, sent_emails
):
    provider = CountingProvider({"summary": SUMMARY})
    _enable_summary(monkeypatch, provider)
    created = await _create(client, category_id)
    assert len(provider.calls) == 1

    response = await client.post(f"/api/v1/tickets/{created['id']}/status", json={"status": "IN_PROGRESS"})
    assert response.status_code == 200

    assert len(provider.calls) == 1  # still one
    assert len(sent_emails) == 2 and SUMMARY in sent_emails[1]["body_html"]


async def _make_ticket(db_session, category_id):
    from app.schemas.ticket import TicketCreate

    return await ticket_service.create_ticket(
        db_session,
        TicketCreate(
            caller_name="Ann", phone_number="+15550001111", category_id=category_id, description=DESCRIPTION
        ),
    )

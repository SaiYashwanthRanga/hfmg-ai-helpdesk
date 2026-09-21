"""AI summary generation tests.

Covers the paths that matter operationally: the feature being off, the
provider being unconfigured, and a failure leaving the ticket usable.
"""

import pytest

from app.ai import summarizer
from app.core.config import get_settings
from app.db.models import AISummaryStatus, Category, Priority, Ticket
from app.schemas.ticket import TicketCreate
from app.services import ticket_service

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()


class FakeProvider:
    def __init__(self, result, *, configured=True):
        self.result = result
        self.configured = configured
        self.calls = []

    @property
    def is_configured(self):
        return self.configured

    async def structured(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


async def _make_ticket(db) -> Ticket:
    category = Category(name="Network", default_priority=Priority.HIGH)
    db.add(category)
    await db.commit()
    await db.refresh(category)

    return await ticket_service.create_ticket(
        db,
        TicketCreate(
            caller_name="Maria Lopez",
            phone_number="+18455550142",
            category_id=category.id,
            description="No internet in the back office since this morning.",
        ),
    )


async def test_summary_is_stored_on_success(db_session, monkeypatch):
    monkeypatch.setattr(settings, "enable_ai_summary", True)
    provider = FakeProvider({"summary": "Back office has lost network connectivity."})
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)

    ticket = await _make_ticket(db_session)
    await summarizer.generate_summary_for_ticket(ticket.id)

    await db_session.refresh(ticket)
    assert ticket.ai_summary == "Back office has lost network connectivity."
    assert ticket.ai_summary_status == AISummaryStatus.COMPLETED
    assert ticket.ai_summary_generated_at is not None

    # Structured output, not free text.
    call = provider.calls[0]
    assert call["schema_name"] == "ticket_summary"
    assert call["schema"]["required"] == ["summary"]
    assert call["schema"]["additionalProperties"] is False


async def test_disabled_feature_makes_no_call(db_session, monkeypatch):
    monkeypatch.setattr(settings, "enable_ai_summary", False)
    provider = FakeProvider({"summary": "should not be used"})
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)

    ticket = await _make_ticket(db_session)
    await summarizer.generate_summary_for_ticket(ticket.id)

    assert provider.calls == []
    await db_session.refresh(ticket)
    assert ticket.ai_summary is None


async def test_unconfigured_provider_makes_no_call(db_session, monkeypatch):
    monkeypatch.setattr(settings, "enable_ai_summary", True)
    provider = FakeProvider(None, configured=False)
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)

    ticket = await _make_ticket(db_session)
    await summarizer.generate_summary_for_ticket(ticket.id)

    assert provider.calls == []
    # Must not be left PENDING forever with nothing able to resolve it.
    await db_session.refresh(ticket)
    assert ticket.ai_summary_status == AISummaryStatus.FAILED


async def test_unexpected_exception_marks_failed_and_is_logged(db_session, monkeypatch, caplog):
    """A crash inside generation (DB, provider bug) must not strand the ticket at PENDING."""

    class ExplodingProvider(FakeProvider):
        async def structured(self, **kwargs):
            raise RuntimeError("boom-from-provider")

    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: ExplodingProvider(None))

    ticket = await _make_ticket(db_session)
    with caplog.at_level("ERROR", logger="hfmg.ai.summarizer"):
        await summarizer.generate_summary_for_ticket(ticket.id)

    await db_session.refresh(ticket)
    assert ticket.ai_summary_status == AISummaryStatus.FAILED
    assert "boom-from-provider" in caplog.text  # real exception is captured, not swallowed


async def test_provider_failure_marks_summary_failed(db_session, monkeypatch):
    """The ticket stays fully usable when summarization fails."""
    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: FakeProvider(None))

    ticket = await _make_ticket(db_session)
    await summarizer.generate_summary_for_ticket(ticket.id)

    await db_session.refresh(ticket)
    assert ticket.ai_summary is None
    assert ticket.ai_summary_status == AISummaryStatus.FAILED
    assert ticket.description  # ticket itself untouched


async def test_empty_summary_is_treated_as_failure(db_session, monkeypatch):
    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: FakeProvider({"summary": "   "}))

    ticket = await _make_ticket(db_session)
    await summarizer.generate_summary_for_ticket(ticket.id)

    await db_session.refresh(ticket)
    assert ticket.ai_summary_status == AISummaryStatus.FAILED

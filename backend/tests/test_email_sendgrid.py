"""SendGrid provider tests.

The httpx client is mocked throughout, so these run offline with no API key
and assert our integration contract -- request shape, retry policy, and
that failures never leak recipient/subject/body into logs.
"""

import logging

import httpx
import pytest

from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket, TicketStatus
from app.notifications import email as email_module
from app.notifications.sendgrid_provider import SendGridProvider

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient, recording calls and replaying outcomes."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def post(self, url, *, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        outcome = self.outcomes.pop(0) if self.outcomes else self.outcomes
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(status_code, message_id="msg-1"):
    return httpx.Response(
        status_code=status_code,
        headers={"X-Message-Id": message_id} if status_code == 202 else {},
        request=httpx.Request("POST", "https://api.sendgrid.com/v3/mail/send"),
    )


def make_provider(monkeypatch, outcomes, *, api_key="SG.test"):
    monkeypatch.setattr(settings, "sendgrid_api_key", api_key)
    provider = SendGridProvider()
    client = FakeAsyncClient(outcomes)
    monkeypatch.setattr(provider, "_get_client", lambda: client)
    return provider, client


# --- request shape --------------------------------------------------------


async def test_send_success_posts_expected_payload(monkeypatch):
    provider, client = make_provider(monkeypatch, [_response(202)])

    result = await provider.send(
        to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="Subj", body="Body text"
    )

    assert result is True
    call = client.calls[0]
    assert call["url"] == "https://api.sendgrid.com/v3/mail/send"
    assert call["json"]["personalizations"] == [{"to": [{"email": "helpdesk@hfmg.net"}]}]
    assert call["json"]["from"] == {"email": "reminder@hfmg.net"}
    assert call["json"]["subject"] == "Subj"
    assert call["json"]["content"] == [{"type": "text/plain", "value": "Body text"}]
    assert call["headers"]["Authorization"] == "Bearer SG.test"


async def test_unconfigured_provider_returns_false(monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "")
    provider = SendGridProvider()
    assert provider.is_configured is False
    assert await provider.send(to="a@b.com", from_email="c@d.com", subject="s", body="b") is False


# --- retry policy -----------------------------------------------------


async def test_retries_on_429_then_succeeds(monkeypatch):
    provider, client = make_provider(monkeypatch, [_response(429), _response(202)])

    result = await provider.send(
        to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="s", body="b", max_retries=1
    )

    assert result is True
    assert len(client.calls) == 2


async def test_retries_on_5xx(monkeypatch):
    provider, client = make_provider(monkeypatch, [_response(503), _response(202)])

    result = await provider.send(
        to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="s", body="b", max_retries=1
    )

    assert result is True
    assert len(client.calls) == 2


async def test_does_not_retry_on_401(monkeypatch):
    """A bad key fails identically every time; retrying wastes nothing but time."""
    provider, client = make_provider(monkeypatch, [_response(401), _response(202)])

    result = await provider.send(
        to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="s", body="b", max_retries=3
    )

    assert result is False
    assert len(client.calls) == 1


async def test_does_not_retry_on_400(monkeypatch):
    provider, client = make_provider(monkeypatch, [_response(400), _response(202)])

    result = await provider.send(
        to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="s", body="b", max_retries=3
    )

    assert result is False
    assert len(client.calls) == 1


async def test_gives_up_after_max_retries(monkeypatch):
    provider, client = make_provider(monkeypatch, [_response(503), _response(503), _response(503)])

    result = await provider.send(
        to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="s", body="b", max_retries=2
    )

    assert result is False
    assert len(client.calls) == 3  # initial + 2 retries


async def test_retries_connection_error(monkeypatch):
    provider, client = make_provider(
        monkeypatch, [httpx.ConnectError("boom"), _response(202)]
    )

    result = await provider.send(
        to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="s", body="b", max_retries=1
    )

    assert result is True
    assert len(client.calls) == 2


async def test_no_retries_means_single_attempt(monkeypatch):
    provider, client = make_provider(monkeypatch, [_response(503)])

    result = await provider.send(
        to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="s", body="b", max_retries=0
    )

    assert result is False
    assert len(client.calls) == 1


# --- no PHI in logs on failure ---------------------------------------------


async def test_failure_does_not_log_body_or_recipient(monkeypatch, caplog):
    """Core requirement: a failed send must never leak ticket content to logs."""
    provider, _ = make_provider(monkeypatch, [_response(401)])

    secret_body = "Caller: Maria Lopez\nDescription: patient scheduling issue for John Doe"
    with caplog.at_level(logging.WARNING, logger="hfmg.notifications.sendgrid"):
        result = await provider.send(
            to="helpdesk@hfmg.net",
            from_email="reminder@hfmg.net",
            subject="New ticket HFMG-2026-000123",
            body=secret_body,
        )

    assert result is False
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Maria Lopez" not in log_text
    assert "John Doe" not in log_text
    assert "patient" not in log_text
    assert "helpdesk@hfmg.net" not in log_text
    assert "HFMG-2026-000123" not in log_text  # subject also excluded


async def test_connection_error_failure_does_not_log_body(monkeypatch, caplog):
    provider, _ = make_provider(monkeypatch, [httpx.ConnectError("boom")])

    secret_body = "Description: sensitive patient detail"
    with caplog.at_level(logging.WARNING, logger="hfmg.notifications.sendgrid"):
        result = await provider.send(
            to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="s", body=secret_body,
            max_retries=0,
        )

    assert result is False
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "sensitive patient detail" not in log_text


# --- email.py integration: fixed from/to, no content on skip/failure -------


def _make_ticket() -> Ticket:
    category = Category(name="Network", default_priority=Priority.HIGH)
    ticket = Ticket(
        ticket_number="HFMG-2026-000999",
        caller_name="Jane Caller",
        phone_number="+18455550142",
        email=None,
        category=category,
        priority=Priority.HIGH,
        description="Confidential description mentioning a patient name.",
        status=TicketStatus.NEW,
    )
    return ticket


async def test_notification_uses_fixed_from_and_to(monkeypatch):
    monkeypatch.setattr(settings, "enable_email_notifications", True)
    monkeypatch.setattr(settings, "email_from", "reminder@hfmg.net")
    monkeypatch.setattr(settings, "helpdesk_email", "helpdesk@hfmg.net")

    captured = {}

    class FakeProvider:
        is_configured = True

        async def send(self, **kwargs):
            captured.update(kwargs)
            return True

    monkeypatch.setattr(email_module, "get_email_provider", lambda: FakeProvider())

    await email_module.send_ticket_notification(_make_ticket(), "created")

    assert captured["to"] == "helpdesk@hfmg.net"
    assert captured["from_email"] == "reminder@hfmg.net"


async def test_disabled_notifications_make_no_call(monkeypatch):
    monkeypatch.setattr(settings, "enable_email_notifications", False)
    calls = []

    class FakeProvider:
        is_configured = True

        async def send(self, **kwargs):
            calls.append(kwargs)
            return True

    monkeypatch.setattr(email_module, "get_email_provider", lambda: FakeProvider())

    await email_module.send_ticket_notification(_make_ticket(), "created")
    assert calls == []


async def test_unconfigured_provider_skips_without_logging_body(monkeypatch, caplog):
    monkeypatch.setattr(settings, "enable_email_notifications", True)

    class FakeProvider:
        is_configured = False

        async def send(self, **kwargs):
            raise AssertionError("should not be called")

    monkeypatch.setattr(email_module, "get_email_provider", lambda: FakeProvider())

    ticket = _make_ticket()
    with caplog.at_level(logging.WARNING, logger="hfmg.notifications.email"):
        await email_module.send_ticket_notification(ticket, "created")

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "patient name" not in log_text
    assert "Jane Caller" not in log_text


async def test_send_failure_does_not_log_body(monkeypatch, caplog):
    monkeypatch.setattr(settings, "enable_email_notifications", True)

    class FakeProvider:
        is_configured = True

        async def send(self, **kwargs):
            return False

    monkeypatch.setattr(email_module, "get_email_provider", lambda: FakeProvider())

    ticket = _make_ticket()
    with caplog.at_level(logging.WARNING, logger="hfmg.notifications.email"):
        await email_module.send_ticket_notification(ticket, "created")

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Confidential description" not in log_text
    assert "Jane Caller" not in log_text

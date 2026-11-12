"""The caller is copied (Cc) on the new-ticket email to the help desk, so they can see it was sent.

The requests go through the real notification builder and the real providers; only the HTTP transport is replaced.
Addresses are synthetic.
"""

import pytest

from app.core.config import get_settings
from app.notifications import email as email_module
from app.notifications import factory
from app.notifications.sendgrid_provider import SendGridProvider
from app.schemas.ticket import TicketCreate
from app.services import ticket_service
from tests.test_email_sendgrid import FakeAsyncClient, _response
from tests.test_hfmg_mail import mail, parse_parts, values  # noqa: F401  (mail is a fixture)

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

HELPDESK = "helpdesk@example.org"


async def _create_and_notify(db_session, category_id, monkeypatch, provider, *, email, event="created", helpdesk=HELPDESK):
    monkeypatch.setattr(settings, "email_provider", provider)
    monkeypatch.setattr(settings, "enable_email_notifications", True)
    monkeypatch.setattr(settings, "helpdesk_email", helpdesk)
    factory.get_email_provider.cache_clear()
    ticket = await ticket_service.create_ticket(
        db_session,
        TicketCreate(
            caller_name="Ann", phone_number="+15550001111", email=email, category_id=category_id, description="Printer jammed."
        ),
    )
    await email_module.send_ticket_notification(await ticket_service.get_ticket(db_session, ticket.id), event)


async def test_the_caller_is_copied_on_the_internal_mail_api(db_session, category_id, mail, monkeypatch):
    recorder = mail()

    await _create_and_notify(db_session, category_id, monkeypatch, "hfmg_internal", email="ann.caller@example.org")

    assert len(recorder.requests) == 1  # one email: the caller is on it, not sent a second one
    parts = parse_parts(recorder.requests[0])
    assert values(parts, "To") == [HELPDESK]
    assert values(parts, "Cc") == ["ann.caller@example.org"]


async def test_no_email_means_no_copy(db_session, category_id, mail, monkeypatch):
    recorder = mail()

    await _create_and_notify(db_session, category_id, monkeypatch, "hfmg_internal", email=None)

    assert values(parse_parts(recorder.requests[0]), "Cc") == []


async def test_a_caller_already_on_the_help_desk_list_is_not_repeated(db_session, category_id, mail, monkeypatch):
    recorder = mail()

    await _create_and_notify(db_session, category_id, monkeypatch, "hfmg_internal", email="HELPDESK@example.org")

    parts = parse_parts(recorder.requests[0])
    assert values(parts, "To") == [HELPDESK]
    assert values(parts, "Cc") == []


async def test_a_status_change_copies_the_caller_too(db_session, category_id, mail, monkeypatch):
    recorder = mail()

    await _create_and_notify(
        db_session, category_id, monkeypatch, "hfmg_internal", email="ann.caller@example.org", event="status_changed"
    )

    assert values(parse_parts(recorder.requests[0]), "Cc") == ["ann.caller@example.org"]


@pytest.mark.parametrize("value", ["", "not-an-address", "a@b", "x@example.org, y@example.org", "x@example.org\r\nBcc: z@example.org"])
async def test_only_one_plain_address_is_ever_copied(value):
    assert email_module._caller_copy(value, [HELPDESK]) == []


async def test_a_plain_address_is_copied_and_trimmed():
    assert email_module._caller_copy("  ann.caller@example.org ", [HELPDESK]) == ["ann.caller@example.org"]


async def test_the_caller_is_copied_on_sendgrid(db_session, category_id, monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "SG.test")
    provider = SendGridProvider()
    client = FakeAsyncClient([_response(202)])
    monkeypatch.setattr(provider, "_get_client", lambda: client)
    monkeypatch.setattr(email_module, "get_email_provider", lambda: provider)

    await _create_and_notify(db_session, category_id, monkeypatch, "sendgrid", email="ann.caller@example.org")

    assert client.calls[0]["json"]["personalizations"] == [
        {"to": [{"email": HELPDESK}], "cc": [{"email": "ann.caller@example.org"}]}
    ]


async def test_sendgrid_drops_a_copy_to_an_address_already_in_to(monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "SG.test")
    provider = SendGridProvider()
    client = FakeAsyncClient([_response(202)])
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    assert await provider.send(to=HELPDESK, from_email="r@example.org", subject="s", body="b", cc=["Helpdesk@Example.org"])
    assert client.calls[0]["json"]["personalizations"] == [{"to": [{"email": HELPDESK}]}]

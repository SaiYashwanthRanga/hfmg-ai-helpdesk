"""HELPDESK_EMAIL may list several addresses; every one of them receives the ticket notification.

Before this, the whole value was sent as one recipient ("a@x,b@y,..."), which the mail API cannot deliver,
so a list of addresses meant nobody got the email. The requests here go through the real notification
builder and the real providers; only the HTTP transport is replaced. Addresses are synthetic.
"""

import logging

import pytest

from app.core.config import Settings, get_settings
from app.notifications import email as email_module
from app.notifications import factory
from app.notifications.sendgrid_provider import SendGridProvider
from app.schemas.ticket import TicketCreate
from app.services import ticket_service
from tests.test_email_sendgrid import FakeAsyncClient, _response
from tests.test_hfmg_mail import mail, parse_parts, values  # noqa: F401  (mail is a fixture)

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

#: The shape of the value on the server: four addresses, comma-separated, one a Freshservice mailbox.
FOUR = "It@example.freshservice.com,Helpdesk@example.org,First.Person@example.org,Second.Person@example.org"
FOUR_LIST = ["It@example.freshservice.com", "Helpdesk@example.org", "First.Person@example.org", "Second.Person@example.org"]


@pytest.mark.parametrize("value, expected", [
    (FOUR, FOUR_LIST),
    ("helpdesk@example.org", ["helpdesk@example.org"]),
    (" a@example.org , b@example.org ", ["a@example.org", "b@example.org"]),  # spaces around addresses
    ("a@example.org; b@example.org", ["a@example.org", "b@example.org"]),  # semicolons, as Outlook writes them
    ("a@example.org,,b@example.org,", ["a@example.org", "b@example.org"]),  # empty entries
    ("a@example.org,A@Example.org,b@example.org", ["a@example.org", "b@example.org"]),  # a repeat, any case
    ("", []),
    (" , ;", []),
])
async def test_helpdesk_email_is_read_as_a_list_of_recipients(value, expected):
    assert Settings(helpdesk_email=value).helpdesk_recipients == expected


async def _notify(db_session, category_id, monkeypatch, helpdesk_email, provider):
    monkeypatch.setattr(settings, "email_provider", provider)
    monkeypatch.setattr(settings, "enable_email_notifications", True)
    monkeypatch.setattr(settings, "helpdesk_email", helpdesk_email)
    factory.get_email_provider.cache_clear()
    ticket = await ticket_service.create_ticket(
        db_session,
        TicketCreate(caller_name="Ann", phone_number="+15550001111", category_id=category_id, description="Printer jammed."),
    )
    await email_module.send_ticket_notification(await ticket_service.get_ticket(db_session, ticket.id), "created")
    return ticket


async def test_every_address_is_its_own_recipient_on_the_internal_mail_api(db_session, category_id, mail, monkeypatch, caplog):
    recorder = mail()

    with caplog.at_level(logging.INFO, logger="hfmg.notifications.email"):
        ticket = await _notify(db_session, category_id, monkeypatch, FOUR, "hfmg_internal")

    assert len(recorder.requests) == 1  # one email to all four, not four emails
    to = values(parse_parts(recorder.requests[0]), "To")
    assert to == FOUR_LIST
    assert not any("," in address for address in to)
    assert f"Sent created notification for {ticket.ticket_number} to 4 recipient(s)" in caplog.text
    assert "example.org" not in caplog.text  # addresses are never logged


async def test_a_single_address_is_sent_exactly_as_before(db_session, category_id, mail, monkeypatch):
    recorder = mail()

    await _notify(db_session, category_id, monkeypatch, "helpdesk@example.org", "hfmg_internal")

    assert values(parse_parts(recorder.requests[0]), "To") == ["helpdesk@example.org"]


async def test_every_address_is_a_recipient_on_sendgrid(db_session, category_id, monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "SG.test")
    provider = SendGridProvider()
    client = FakeAsyncClient([_response(202)])
    monkeypatch.setattr(provider, "_get_client", lambda: client)
    monkeypatch.setattr(email_module, "get_email_provider", lambda: provider)

    await _notify(db_session, category_id, monkeypatch, FOUR, "sendgrid")

    assert len(client.calls) == 1
    personalizations = client.calls[0]["json"]["personalizations"]
    assert personalizations == [{"to": [{"email": address} for address in FOUR_LIST]}]


async def test_sendgrid_still_takes_a_single_address_string(monkeypatch):
    monkeypatch.setattr(settings, "sendgrid_api_key", "SG.test")
    provider = SendGridProvider()
    client = FakeAsyncClient([_response(202)])
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    assert await provider.send(to="helpdesk@example.org", from_email="r@example.org", subject="s", body="b") is True
    assert client.calls[0]["json"]["personalizations"] == [{"to": [{"email": "helpdesk@example.org"}]}]


async def test_an_empty_helpdesk_email_sends_nothing_and_says_why(db_session, category_id, mail, monkeypatch, caplog):
    recorder = mail()

    with caplog.at_level(logging.WARNING, logger="hfmg.notifications.email"):
        await _notify(db_session, category_id, monkeypatch, " , ", "hfmg_internal")

    assert recorder.requests == []
    assert "HELPDESK_EMAIL is empty; skipping created notification" in caplog.text

"""HFMG internal mail API: service, provider, factory, health check, settings.

The network is mocked with httpx.MockTransport, so nothing here contacts the
real mail API or sends any email.
"""

import email
import email.policy
import logging

import httpx
import pytest

from app.core.config import get_settings
from app.notifications import factory
from app.notifications.base import IEmailProvider
from app.notifications.hfmg_provider import HfmgInternalMailProvider
from app.notifications.sendgrid_provider import SendGridProvider
from app.services import dependency_health, hfmg_mail_service

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
BASE = "http://mail.test:177"


class Recorder:
    """A MockTransport handler that records requests and replays outcomes."""

    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes) if outcomes else [httpx.Response(200)]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def mail(monkeypatch):
    """Configure ORG_BASE and route the service's client through a Recorder."""
    monkeypatch.setattr(settings, "org_base", BASE)
    monkeypatch.setattr(settings, "hfmg_mail_max_retries", 2)
    state = {}

    def install(*outcomes):
        recorder = Recorder(outcomes or None)
        client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
        monkeypatch.setattr(hfmg_mail_service, "_get_client", lambda: client)
        state["recorder"] = recorder
        return recorder

    async def no_sleep(_):
        return None

    monkeypatch.setattr(hfmg_mail_service, "_backoff", no_sleep)
    return install


def parse_parts(request: httpx.Request) -> list[dict]:
    """Decode a multipart body into [{name, filename, value}] in wire order."""
    content_type = request.headers["content-type"]
    assert content_type.startswith("multipart/form-data")
    message = email.message_from_bytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + request.content,
        policy=email.policy.default,
    )
    parts = []
    for part in message.iter_parts():
        parts.append(
            {
                "name": part.get_param("name", header="content-disposition"),
                "filename": part.get_filename(),
                "value": part.get_payload(decode=True),
            }
        )
    return parts


def values(parts, name) -> list[str]:
    return [p["value"].decode() for p in parts if p["name"] == name]


# --- request shape ---------------------------------------------------------


async def test_send_email_posts_multipart_with_repeated_to_and_cc(mail):
    recorder = mail()

    ok = await hfmg_mail_service.send_email(
        to=["a@hfmg.net", "b@hfmg.net"],
        cc=["c@hfmg.net", "d@hfmg.net"],
        subject="Subject line",
        body_html="<p>Hi</p>",
        from_email="sender@hfmg.net",
    )

    assert ok is True
    request = recorder.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE}/api/Values/mail/send"
    parts = parse_parts(request)
    assert values(parts, "To") == ["a@hfmg.net", "b@hfmg.net"]  # one part per address
    assert values(parts, "Cc") == ["c@hfmg.net", "d@hfmg.net"]
    assert values(parts, "Subject") == ["Subject line"]
    assert values(parts, "BodyHtml") == ["<p>Hi</p>"]
    assert values(parts, "FromEmail") == ["sender@hfmg.net"]


async def test_attachments_part_is_present_even_without_files(mail):
    recorder = mail()

    await hfmg_mail_service.send_email(to="a@hfmg.net", subject="s", body_html="b")

    attachments = [p for p in parse_parts(recorder.requests[0]) if p["name"] == "Attachments"]
    assert len(attachments) == 1
    assert attachments[0]["value"] == b""
    # Must be a *file* part (filename present but empty), as a browser sends an
    # empty file input. A part with no filename at all is a plain form field.
    assert b'name="Attachments"; filename=""' in recorder.requests[0].content


async def test_real_attachments_replace_the_empty_placeholder(mail):
    recorder = mail()

    await hfmg_mail_service.send_email(
        to="a@hfmg.net",
        subject="s",
        body_html="b",
        attachments=[("a.txt", b"one", "text/plain"), ("b.pdf", b"two", "application/pdf")],
    )

    attachments = [p for p in parse_parts(recorder.requests[0]) if p["name"] == "Attachments"]
    assert [(p["filename"], p["value"]) for p in attachments] == [("a.txt", b"one"), ("b.pdf", b"two")]


async def test_from_email_falls_back_to_default_from_email(mail, monkeypatch):
    recorder = mail()
    monkeypatch.setattr(settings, "default_from_email", "default@hfmg.net")

    await hfmg_mail_service.send_email(to="a@hfmg.net", subject="s", body_html="b")

    assert values(parse_parts(recorder.requests[0]), "FromEmail") == ["default@hfmg.net"]


async def test_from_email_is_never_omitted_the_api_rejects_it(mail, monkeypatch):
    """Live finding: without FromEmail the API returns 400 'At least one recipient is required'."""
    recorder = mail()
    monkeypatch.setattr(settings, "default_from_email", "")
    monkeypatch.setattr(settings, "email_from", "reminder@hfmg.net")

    await hfmg_mail_service.send_email(to="a@hfmg.net", subject="s", body_html="b")

    assert values(parse_parts(recorder.requests[0]), "FromEmail") == ["reminder@hfmg.net"]


async def test_send_email_with_no_sender_at_all_makes_no_request(mail, monkeypatch):
    recorder = mail()
    monkeypatch.setattr(settings, "default_from_email", "")
    monkeypatch.setattr(settings, "email_from", "")

    assert await hfmg_mail_service.send_email(to="a@hfmg.net", subject="s", body_html="b") is False
    assert recorder.requests == []


async def test_send_reminder_uses_reminder_endpoint_and_no_from_email(mail):
    recorder = mail()

    ok = await hfmg_mail_service.send_reminder(
        to=["a@hfmg.net"], cc=["c@hfmg.net"], subject="Reminder", body_html="<b>r</b>"
    )

    assert ok is True
    request = recorder.requests[0]
    assert str(request.url) == f"{BASE}/api/Values/mail/SentAsRemainder"
    parts = parse_parts(request)
    assert values(parts, "To") == ["a@hfmg.net"]
    assert values(parts, "Cc") == ["c@hfmg.net"]
    assert values(parts, "BodyHtml") == ["<b>r</b>"]
    assert "FromEmail" not in [p["name"] for p in parts]
    assert "Attachments" in [p["name"] for p in parts]


async def test_reply_email_fields(mail):
    recorder = mail()

    ok = await hfmg_mail_service.reply_email(
        message_item_id="AAMk-123",
        reply_body_html="<p>thanks</p>",
        target_user_email="box@hfmg.net",
        to=["a@hfmg.net", "b@hfmg.net"],
        cc=["c@hfmg.net"],
        is_reply_all=True,
    )

    assert ok is True
    request = recorder.requests[0]
    assert str(request.url) == f"{BASE}/api/Values/mail/reply"
    parts = parse_parts(request)
    assert values(parts, "MessageItemId") == ["AAMk-123"]
    assert values(parts, "ReplyBodyHtml") == ["<p>thanks</p>"]
    assert values(parts, "TargetUserEmail") == ["box@hfmg.net"]
    assert values(parts, "IsReplyAll") == ["true"]
    assert values(parts, "To") == ["a@hfmg.net", "b@hfmg.net"]
    assert values(parts, "Cc") == ["c@hfmg.net"]
    assert "Attachments" in [p["name"] for p in parts]


async def test_reply_all_defaults_to_false(mail):
    recorder = mail()

    await hfmg_mail_service.reply_email(
        message_item_id="id", reply_body_html="x", target_user_email="box@hfmg.net"
    )

    assert values(parse_parts(recorder.requests[0]), "IsReplyAll") == ["false"]


async def test_forward_email_fields_use_the_apis_own_names(mail):
    recorder = mail()

    ok = await hfmg_mail_service.forward_email(
        message_item_id="AAMk-9",
        to_list=["a@hfmg.net", "b@hfmg.net"],
        cc_list=["c@hfmg.net", "d@hfmg.net"],
        subject="FW: hello",
        body="<p>see below</p>",
        target_user_email="box@hfmg.net",
    )

    assert ok is True
    request = recorder.requests[0]
    assert str(request.url) == f"{BASE}/api/Values/forwardmail"
    parts = parse_parts(request)
    assert values(parts, "ToList") == ["a@hfmg.net", "b@hfmg.net"]
    assert values(parts, "CcList") == ["c@hfmg.net", "d@hfmg.net"]
    assert values(parts, "Subject") == ["FW: hello"]
    assert values(parts, "Body") == ["<p>see below</p>"]
    assert values(parts, "MessageItemId") == ["AAMk-9"]
    assert values(parts, "targetUserEmail") == ["box@hfmg.net"]  # lowercase t, per the API
    assert "Attachments" in [p["name"] for p in parts]


@pytest.mark.parametrize(
    "call",
    [
        lambda: hfmg_mail_service.send_email(to=[], subject="s", body_html="b"),
        lambda: hfmg_mail_service.send_email(to=["  "], subject="s", body_html="b"),
        lambda: hfmg_mail_service.send_reminder(to=[], subject="s", body_html="b"),
        lambda: hfmg_mail_service.reply_email(message_item_id="", reply_body_html="b", target_user_email="x@y.z"),
        lambda: hfmg_mail_service.forward_email(message_item_id="id", to_list=[], body="b", target_user_email="x@y.z"),
    ],
)
async def test_missing_required_inputs_make_no_request(mail, call):
    recorder = mail()

    assert await call() is False
    assert recorder.requests == []


# --- failure handling ------------------------------------------------------


async def test_not_configured_makes_no_request_and_returns_false(mail, monkeypatch, caplog):
    recorder = mail()
    monkeypatch.setattr(settings, "org_base", "")

    with caplog.at_level(logging.WARNING, logger="hfmg.mail"):
        ok = await hfmg_mail_service.send_email(to="a@hfmg.net", subject="s", body_html="b")

    assert ok is False
    assert recorder.requests == []
    assert "ORG_BASE" in caplog.text


async def test_unreachable_api_fails_gracefully_logs_and_retries(mail, caplog):
    error = httpx.ConnectError("All connection attempts failed")
    recorder = mail(error)

    with caplog.at_level(logging.ERROR, logger="hfmg.mail"):
        ok = await hfmg_mail_service.send_email(
            to="secret-recipient@hfmg.net", subject="secret subject", body_html="secret body"
        )

    assert ok is False  # no exception escapes
    assert len(recorder.requests) == 3  # 1 attempt + 2 retries, connection-level only
    assert "unreachable" in caplog.text
    for secret in ("secret-recipient", "secret subject", "secret body"):
        assert secret not in caplog.text


async def test_connect_error_then_success_is_retried(mail):
    recorder = mail(httpx.ConnectError("boom"), httpx.Response(200))

    assert await hfmg_mail_service.send_email(to="a@hfmg.net", subject="s", body_html="b") is True
    assert len(recorder.requests) == 2


async def test_read_timeout_is_not_retried(mail, caplog):
    """The message may already have been sent, so retrying could duplicate it."""
    recorder = mail(httpx.ReadTimeout("slow"))

    with caplog.at_level(logging.ERROR, logger="hfmg.mail"):
        ok = await hfmg_mail_service.send_email(to="a@hfmg.net", subject="s", body_html="b")

    assert ok is False
    assert len(recorder.requests) == 1
    assert "not retrying" in caplog.text


@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_http_errors_return_false_without_retry_and_log_status_only(mail, caplog, status):
    recorder = mail(httpx.Response(status, text="Graph said: bob@hfmg.net is invalid"))

    with caplog.at_level(logging.ERROR, logger="hfmg.mail"):
        ok = await hfmg_mail_service.send_email(to="a@hfmg.net", subject="s", body_html="b")

    assert ok is False
    assert len(recorder.requests) == 1
    assert f"HTTP {status}" in caplog.text
    assert "bob@hfmg.net" not in caplog.text  # response bodies are never logged


async def test_unexpected_exception_is_contained(mail, caplog):
    mail(RuntimeError("kaboom"))

    with caplog.at_level(logging.ERROR, logger="hfmg.mail"):
        ok = await hfmg_mail_service.send_email(to="a@hfmg.net", subject="s", body_html="b")

    assert ok is False
    assert "unexpected error" in caplog.text


# --- health check ----------------------------------------------------------


@pytest.mark.parametrize("status,expected", [(200, True), (404, True), (401, True), (500, False), (503, False)])
async def test_check_health_status_mapping(mail, status, expected):
    recorder = mail(httpx.Response(status))

    assert await hfmg_mail_service.check_health() is expected
    assert recorder.requests[0].method == "GET"
    assert str(recorder.requests[0].url) == f"{BASE}/"


async def test_check_health_false_when_unreachable_or_unconfigured(mail, monkeypatch):
    mail(httpx.ConnectError("down"))
    assert await hfmg_mail_service.check_health() is False

    monkeypatch.setattr(settings, "org_base", "")
    assert await hfmg_mail_service.check_health() is False


async def test_dependency_health_email_uses_internal_check(mail, monkeypatch):
    mail(httpx.Response(404))
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    dependency_health.reset_cache()

    assert (await dependency_health.check_email()).status == "operational"

    mail(httpx.ConnectError("down"))
    assert (await dependency_health.check_email()).status == "down"
    dependency_health.reset_cache()


async def test_health_endpoint_reports_internal_mail_status(client, mail, monkeypatch):
    mail(httpx.Response(200))
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    dependency_health.reset_cache()

    body = (await client.get("/api/v1/health/dependencies")).json()

    assert body["email"]["status"] == "operational"
    dependency_health.reset_cache()


async def test_health_endpoint_operational_response_shape(client, mail, monkeypatch):
    """The exact contract the dashboard reads: email.status == "operational"."""
    recorder = mail(httpx.Response(404))  # the API's root answers 404, which still proves it is up
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    monkeypatch.setattr(settings, "sendgrid_api_key", "")  # SendGrid logic must not matter
    dependency_health.reset_cache()

    response = await client.get("/api/v1/health/dependencies")

    assert response.status_code == 200
    assert response.json()["email"]["status"] == "operational"
    # Health must never send mail: only a GET, never a POST to a mail endpoint.
    assert [r.method for r in recorder.requests] == ["GET"]
    dependency_health.reset_cache()


async def test_health_endpoint_down_when_org_base_missing(client, mail, monkeypatch):
    recorder = mail(httpx.Response(200))
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    monkeypatch.setattr(settings, "org_base", "")
    dependency_health.reset_cache()

    body = (await client.get("/api/v1/health/dependencies")).json()

    assert body["email"]["status"] == "down"
    assert recorder.requests == []  # nothing to probe when not configured
    dependency_health.reset_cache()


@pytest.mark.parametrize("outcome", [httpx.ConnectError("refused"), httpx.ConnectTimeout("t"), httpx.Response(503)])
async def test_health_endpoint_down_when_internal_api_unreachable(client, mail, monkeypatch, outcome):
    mail(outcome)
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    dependency_health.reset_cache()

    body = (await client.get("/api/v1/health/dependencies")).json()

    assert body["email"]["status"] == "down"
    dependency_health.reset_cache()


async def test_health_failure_is_logged_with_a_reason(mail, caplog):
    mail(httpx.ConnectError("refused"))

    with caplog.at_level(logging.WARNING, logger="hfmg.mail"):
        assert await hfmg_mail_service.check_health() is False

    assert "health check failed: ConnectError" in caplog.text


async def test_sendgrid_provider_ignores_internal_api_settings(client, mail, monkeypatch):
    """EMAIL_PROVIDER decides which check runs; a reachable ORG_BASE must not make SendGrid look healthy."""
    recorder = mail(httpx.Response(200))
    monkeypatch.setattr(settings, "email_provider", "sendgrid")
    monkeypatch.setattr(settings, "sendgrid_api_key", "")
    dependency_health.reset_cache()

    body = (await client.get("/api/v1/health/dependencies")).json()

    assert body["email"]["status"] == "down"
    assert recorder.requests == []
    dependency_health.reset_cache()


async def test_settings_status_for_internal_provider_exposes_no_url_or_secret(client, mail, monkeypatch):
    mail(httpx.Response(200))
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    monkeypatch.setattr(settings, "default_from_email", "box@hfmg.net")
    dependency_health.reset_cache()

    response = await client.get("/api/v1/settings/status")
    email_status = response.json()["email"]

    assert email_status["configured"] is True
    assert email_status["masked_key"] is None
    assert "box@hfmg.net" in email_status["detail"]
    assert BASE not in response.text  # internal address is not exposed
    dependency_health.reset_cache()


# --- provider + factory ----------------------------------------------------


async def test_provider_escapes_html_and_preserves_line_breaks(mail, monkeypatch):
    recorder = mail()
    monkeypatch.setattr(settings, "default_from_email", "")

    ok = await HfmgInternalMailProvider().send(
        to="helpdesk@hfmg.net",
        from_email="reminder@hfmg.net",
        subject="New ticket\r\nBcc: evil@x.y",
        body='Line one\nLine <b>two</b> & <script>alert(1)</script>',
    )

    assert ok is True
    parts = parse_parts(recorder.requests[0])
    body_html = values(parts, "BodyHtml")[0]
    assert "&lt;script&gt;" in body_html and "<script>" not in body_html
    assert "&lt;b&gt;two&lt;/b&gt; &amp;" in body_html
    assert "Line one<br>\nLine" in body_html
    assert values(parts, "Subject") == ["New ticket Bcc: evil@x.y"]  # CR/LF cannot inject headers
    assert values(parts, "To") == ["helpdesk@hfmg.net"]
    assert values(parts, "FromEmail") == ["reminder@hfmg.net"]


async def test_provider_prefers_default_from_email(mail, monkeypatch):
    recorder = mail()
    monkeypatch.setattr(settings, "default_from_email", "box@hfmg.net")

    await HfmgInternalMailProvider().send(
        to="helpdesk@hfmg.net", from_email="reminder@hfmg.net", subject="s", body="b"
    )

    assert values(parse_parts(recorder.requests[0]), "FromEmail") == ["box@hfmg.net"]


async def test_provider_is_configured_follows_org_base(monkeypatch):
    provider = HfmgInternalMailProvider()
    monkeypatch.setattr(settings, "org_base", "")
    assert provider.is_configured is False
    monkeypatch.setattr(settings, "org_base", BASE)
    assert provider.is_configured is True


async def test_both_providers_satisfy_the_interface():
    assert isinstance(SendGridProvider(), IEmailProvider)
    assert isinstance(HfmgInternalMailProvider(), IEmailProvider)


async def test_factory_switches_provider_by_configuration(monkeypatch):
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    factory.get_email_provider.cache_clear()
    assert isinstance(factory.get_email_provider(), HfmgInternalMailProvider)

    monkeypatch.setattr(settings, "email_provider", "sendgrid")
    factory.get_email_provider.cache_clear()
    assert isinstance(factory.get_email_provider(), SendGridProvider)

    monkeypatch.setattr(settings, "email_provider", "nonsense")
    factory.get_email_provider.cache_clear()
    assert isinstance(factory.get_email_provider(), SendGridProvider)  # existing fallback


async def test_ticket_notification_flows_through_internal_provider(db_session, category_id, mail, monkeypatch):
    """End to end: a new ticket produces one internal-API send, never SendGrid."""
    from app.notifications import email as email_module
    from app.schemas.ticket import TicketCreate
    from app.services import ticket_service

    recorder = mail()
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    monkeypatch.setattr(settings, "enable_email_notifications", True)
    monkeypatch.setattr(settings, "helpdesk_email", "helpdesk@hfmg.net")
    factory.get_email_provider.cache_clear()

    ticket = await ticket_service.create_ticket(
        db_session,
        TicketCreate(
            caller_name="Ann", phone_number="+15550001111", category_id=category_id, description="No wifi <b>now</b>"
        ),
    )
    ticket = await ticket_service.get_ticket(db_session, ticket.id)
    await email_module.send_ticket_notification(ticket, "created")

    assert len(recorder.requests) == 1
    parts = parse_parts(recorder.requests[0])
    assert values(parts, "To") == ["helpdesk@hfmg.net"]
    assert ticket.ticket_number in values(parts, "Subject")[0]
    assert "&lt;b&gt;now&lt;/b&gt;" in values(parts, "BodyHtml")[0]

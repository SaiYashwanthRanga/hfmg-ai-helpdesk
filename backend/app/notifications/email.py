import logging

from app.core.config import get_settings
from app.db.models import Ticket
from app.notifications.factory import get_email_provider
from app.services.ticket_text import extract_issue

logger = logging.getLogger("hfmg.notifications.email")

settings = get_settings()

_EVENT_SUBJECTS = {
    "created": "New ticket {ticket_number} — {category}",
    "status_changed": "Ticket {ticket_number} updated: {status}",
}


# What the voice flow stores when it could not capture a name or number (orchestrator._create_ticket).
_NO_NAME = {"", "unknown caller (voice)"}
_NO_PHONE = {"", "unknown"}


def _known(value: str | None, placeholders: set[str]) -> str | None:
    """The value, or None when it is blank or one of the stored "not captured" placeholders."""
    text = (value or "").strip()
    return None if text.lower() in placeholders else text


def _build_body(
    caller: str | None, callback: str | None, issue: str, summary: str | None
) -> str:
    """Who to call back and on what number, the issue, and its summary. The details,
    transcript, intake details, department, priority and other ticket fields stay on
    the ticket (dashboard / Freshworks); the subject names the ticket. The summary is
    written from the details, so repeating them here would only duplicate it.

    The summary is the one already saved on the ticket; this module never calls
    the LLM. A caller, number or summary that is not available is left out, not blank."""
    lines: list[str] = []
    for label, value in (("Caller", caller), ("Callback Number", callback), ("Issue", issue), ("Summary", summary)):
        if value:
            lines += [f"{label}:", value, ""]
    return "\n".join(lines).rstrip()


async def send_ticket_notification(ticket: Ticket, event: str) -> None:
    """Send a notification email for a ticket lifecycle event, via SendGrid.

    Called from a FastAPI BackgroundTask -- must not raise, since an
    unhandled exception in a background task is only logged by the ASGI
    server and would otherwise be silently swallowed.

    Every notification goes from EMAIL_FROM to HELPDESK_EMAIL; this is not
    configurable per-call, by design (the notification target is fixed
    organizational policy, not a per-ticket choice).

    Never logs the ticket description, AI summary, or email body -- only the
    ticket number and event, which are not PHI-adjacent on their own.
    """
    if not settings.enable_email_notifications:
        logger.info("Email notifications disabled; skipping %s for %s", event, ticket.ticket_number)
        return

    try:
        provider = get_email_provider()
        if not provider.is_configured:
            logger.warning(
                "Email provider not configured; skipping %s notification for %s",
                event,
                ticket.ticket_number,
            )
            return

        subject_template = _EVENT_SUBJECTS.get(event, "Ticket {ticket_number} update")
        subject = subject_template.format(
            ticket_number=ticket.ticket_number, category=ticket.category.name, status=ticket.status.value
        )
        body = _build_body(
            _known(ticket.caller_name, _NO_NAME),
            # The number the caller confirmed; a web ticket, or a call that never confirmed
            # one, has only its phone number.
            _known(ticket.callback_number or ticket.phone_number, _NO_PHONE),
            extract_issue(ticket.description),
            ticket.ai_summary,
        )

        sent = await provider.send(
            to=settings.helpdesk_email,
            from_email=settings.email_from,
            subject=subject,
            body=body,
        )

        if sent:
            logger.info("Sent %s notification for %s", event, ticket.ticket_number)
        else:
            # No exception detail or body here by design -- the provider already
            # logged the failure class (status code / error type) without content.
            logger.warning("Failed to send %s notification for %s", event, ticket.ticket_number)
    except Exception:
        # This runs as a FastAPI BackgroundTask: an exception here would
        # otherwise propagate past the (already-sent) response with no
        # app-level log line, and a raised exception is never allowed to
        # surface as a failed ticket-creation request. Never include the
        # ticket description/summary/body -- only identifiers, matching the
        # rest of this module's no-content-in-logs discipline.
        logger.exception(
            "Unexpected error sending %s notification for %s", event, ticket.ticket_number
        )

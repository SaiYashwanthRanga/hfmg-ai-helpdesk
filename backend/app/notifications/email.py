import logging

from app.core.config import get_settings
from app.db.models import Ticket
from app.notifications.factory import get_email_provider

logger = logging.getLogger("hfmg.notifications.email")

settings = get_settings()

_EVENT_SUBJECTS = {
    "created": "New ticket {ticket_number} — {category}",
    "status_changed": "Ticket {ticket_number} updated: {status}",
}


def _build_body(ticket: Ticket, event: str) -> str:
    lines = [
        f"Ticket: {ticket.ticket_number}",
        f"Status: {ticket.status.value}",
        f"Priority: {ticket.priority.value}",
        f"Category: {ticket.category.name}",
        f"Caller: {ticket.caller_name}",
        f"Phone: {ticket.phone_number}",
        f"Email: {ticket.email or '(not provided)'}",
        "",
        "Description:",
        ticket.description,
    ]
    if ticket.ai_summary:
        lines += ["", "AI Summary:", ticket.ai_summary]
    return "\n".join(lines)


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
    body = _build_body(ticket, event)

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

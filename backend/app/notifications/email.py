import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings
from app.db.models import Ticket

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


def send_ticket_notification(ticket: Ticket, event: str) -> None:
    """Send (or log) a notification email for a ticket lifecycle event.

    Called from a FastAPI BackgroundTask — must not raise, since an
    unhandled exception in a background task is only logged by the
    ASGI server and would otherwise be silently swallowed.
    """
    if not settings.enable_email_notifications:
        logger.info("Email notifications disabled; skipping %s for %s", event, ticket.ticket_number)
        return

    subject_template = _EVENT_SUBJECTS.get(event, "Ticket {ticket_number} update")
    subject = subject_template.format(
        ticket_number=ticket.ticket_number, category=ticket.category.name, status=ticket.status.value
    )
    body = _build_body(ticket, event)

    if not settings.smtp_host:
        logger.info(
            "SMTP not configured (log-only mode). Would send to %s\nSubject: %s\n%s",
            settings.helpdesk_notification_email,
            subject,
            body,
        )
        return

    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = settings.helpdesk_notification_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
        logger.info("Sent %s notification for %s to %s", event, ticket.ticket_number, message["To"])
    except (smtplib.SMTPException, OSError):
        logger.exception("Failed to send %s notification for %s", event, ticket.ticket_number)

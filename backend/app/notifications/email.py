import asyncio
import logging

from app.core.config import get_settings
from app.db.models import CallerType, Ticket
from app.notifications.factory import get_email_provider
from app.services.ticket_text import extract_intake_value, extract_issue

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


NOT_PROVIDED = "Not provided"

#: Shown first, in the subject and the body, on a provider's callback request: it is the one email that asks the IT
#: team to phone someone back, and the provider reached the help desk because no one was free to answer.
PROVIDER_BANNER = "HIGH PRIORITY — PROVIDER CALLBACK REQUIRED"


def _build_body(
    caller: str | None,
    callback: str | None,
    issue: str,
    summary: str | None,
    *,
    email: str | None = None,
    extension: str | None = None,
    with_extension: bool = False,
    ticket_number: str | None = None,
    banner: str | None = None,
    priority: str | None = None,
    system: str | None = None,
    required_action: str | None = None,
) -> str:
    """Who to call back, the number and email to reach them on, the issue, its summary and the
    ticket number. The details, transcript, intake details, department, priority and other ticket
    fields stay on the ticket (dashboard); the subject names the ticket. The summary is written
    from the details, so repeating them here would only duplicate it.

    The summary is the one already saved on the ticket; this module never calls the LLM. A
    caller, number, summary or ticket number that is not available is left out, not blank. The
    email address is always shown, so the helpdesk can see at a glance whether it has one to fall
    back on: "Not provided" when there is none (never a guess, and never an address that was not
    confirmed: see `ticket.email`).

    `with_extension`: the office extension is shown the same way, right after the email -- "Not provided" when
    the caller gave none. It is shown exactly as the ticket records it, with any note ("unverified: not found in
    the directory"), so an extension nobody checked never reads as checked.

    A provider's callback request passes `banner`, `priority`, `system` and `required_action`: it leads with the
    banner, the priority and the ticket number, and ends with what the IT team must do. Every other ticket passes
    none of them and reads exactly as before."""
    lines: list[str] = [banner, ""] if banner else []
    for label, value in (
        ("Priority", priority),
        ("Ticket Number", ticket_number if banner else None),
        ("Provider" if banner else "Caller", caller),
        ("Callback Number", callback),
        ("Email", (email or "").strip() or NOT_PROVIDED),
        ("Extension", ((extension or "").strip() or NOT_PROVIDED) if with_extension else None),
        ("System", system),
        ("Issue", issue),
        ("Summary", summary),
        ("Ticket Number", None if banner else ticket_number),
        ("Required Action", required_action),
    ):
        if value:
            lines += [f"{label}:", value, ""]
    return "\n".join(lines).rstrip()


def _provider_fields(ticket: Ticket, callback: str | None) -> dict[str, str | None]:
    """What a provider's callback request adds to the email, read from the saved ticket."""
    confirmed = bool(ticket.callback_number)
    if callback and confirmed:
        action = f"Contact the provider at the confirmed callback number: {callback}"
    elif callback:
        action = f"Contact the provider at {callback}. The provider did not confirm this number."
    else:
        action = "Contact the provider. No callback number was captured: check the call record."
    return {
        "banner": PROVIDER_BANNER,
        "priority": ticket.priority.value,
        "system": extract_intake_value(ticket.description, "System"),
        "required_action": action,
    }


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
        logger.info("Sending %s notification for %s (caller type %s)", event, ticket.ticket_number, ticket.caller_type)
        provider = get_email_provider()
        if not provider.is_configured:
            logger.warning(
                "Email provider not configured; skipping %s notification for %s",
                event,
                ticket.ticket_number,
            )
            return

        recipients = settings.helpdesk_recipients
        if not recipients:
            logger.warning("HELPDESK_EMAIL is empty; skipping %s notification for %s", event, ticket.ticket_number)
            return

        subject_template = _EVENT_SUBJECTS.get(event, "Ticket {ticket_number} update")
        subject = subject_template.format(
            ticket_number=ticket.ticket_number, category=ticket.category.name, status=ticket.status.value
        )
        callback = _known(ticket.callback_number or ticket.phone_number, _NO_PHONE)
        provider_callback = event == "created" and ticket.caller_type is CallerType.PROVIDER
        if provider_callback:
            subject = f"{PROVIDER_BANNER} — {subject}"
        body = _build_body(
            _known(ticket.caller_name, _NO_NAME),
            # The number the caller confirmed; a web ticket, or a call that never confirmed
            # one, has only its phone number.
            callback,
            extract_issue(ticket.description),
            ticket.ai_summary,
            email=ticket.email,
            # An employee's office extension, from the ticket's intake details. Not for a patient request:
            # patients have no extension, so the line would only be noise. A provider is not asked for one.
            extension=extract_intake_value(ticket.description, "Extension"),
            with_extension=ticket.caller_type not in (CallerType.PATIENT_SUPPORT, CallerType.PROVIDER),
            ticket_number=ticket.ticket_number,
            # The priority printed is the one saved on the ticket, never a separate string.
            **(_provider_fields(ticket, callback) if provider_callback else {}),
        )

        max_attempts = settings.notification_max_attempts  # 0 = until sent
        attempt = 0
        while True:
            attempt += 1
            sent = await provider.send(
                to=recipients,
                from_email=settings.email_from,
                subject=subject,
                body=body,
            )
            if sent:
                # The count, never the addresses: a quick check that every configured recipient was included.
                logger.info(
                    "Sent %s notification for %s to %d recipient(s) (attempt %d)",
                    event,
                    ticket.ticket_number,
                    len(recipients),
                    attempt,
                )
                break

            # No exception detail or body here by design -- the provider already
            # logged the failure class (status code / error type) without content.
            logger.warning("Failed to send %s notification for %s (attempt %d)", event, ticket.ticket_number, attempt)
            if max_attempts and attempt >= max_attempts:
                logger.error(
                    "Giving up on %s notification for %s after %d attempts", event, ticket.ticket_number, attempt
                )
                break
            # The mail API fails in bursts (e.g. HTTP 400 "Mail failed" when its Graph call hiccups), so back off
            # and try again: 5s, 10s, 20s ... capped.
            delay = min(5.0 * 2 ** (attempt - 1), settings.notification_retry_max_delay_seconds)
            logger.info("Retrying %s notification for %s in %.0fs", event, ticket.ticket_number, delay)
            await asyncio.sleep(delay)
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

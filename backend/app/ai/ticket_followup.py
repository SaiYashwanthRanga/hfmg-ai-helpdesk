"""Post-creation work for a new ticket: summarize once, then notify.

One background task, in this order, so the email can never go out before the
summary exists and the summary is generated exactly once. The email and the
dashboard both read the `ai_summary` saved on the ticket.
"""

import logging
import uuid

from app.ai.summarizer import generate_summary_for_ticket
from app.db.base import async_session_factory
from app.notifications.email import send_ticket_notification
from app.services.ticket_service import get_ticket

logger = logging.getLogger("hfmg.ai.ticket_followup")


async def summarize_then_notify(ticket_id: uuid.UUID, *, send_email: bool = True) -> None:
    """Generate and save the AI summary, then email the helpdesk from the saved ticket.

    Never raises (runs as a FastAPI BackgroundTask). A failed or disabled summary
    leaves `ai_summary` empty, and the email goes out with the Issue only; the
    summary is not retried here.
    """
    await generate_summary_for_ticket(ticket_id)  # best effort; marks the ticket FAILED itself
    if not send_email:
        return
    try:
        # A fresh read: the summary was saved by another session.
        async with async_session_factory() as db:
            ticket = await get_ticket(db, ticket_id)
            await send_ticket_notification(ticket, "created")
    except Exception:
        logger.exception("Could not send the notification for ticket %s", ticket_id)

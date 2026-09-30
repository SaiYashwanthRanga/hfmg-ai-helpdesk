"""Background work fired after a voice call creates a ticket."""

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.summarizer import generate_summary_for_ticket
from app.notifications.email import send_ticket_notification
from app.services import ticket_service


async def dispatch_ticket_tasks(
    db: AsyncSession, background_tasks: BackgroundTasks, ticket_id
) -> None:
    """Fire the same background work the web form fires.

    The AI summary is deliberately not awaited -- the caller hears their ticket
    number immediately and the summary lands seconds later.
    """
    ticket = await ticket_service.get_ticket(db, ticket_id)
    background_tasks.add_task(send_ticket_notification, ticket, "created")
    background_tasks.add_task(generate_summary_for_ticket, ticket.id)

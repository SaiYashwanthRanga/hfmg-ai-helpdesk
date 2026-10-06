"""Background work fired after a voice call creates a ticket."""

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.ticket_followup import summarize_then_notify


async def dispatch_ticket_tasks(
    db: AsyncSession, background_tasks: BackgroundTasks, ticket_id
) -> None:
    """Fire the same background work the web form fires.

    One task, not two: the AI summary is generated and saved first, then the
    helpdesk email is sent from it. Neither is awaited here -- the caller hears
    their ticket number immediately.
    """
    background_tasks.add_task(summarize_then_notify, ticket_id)

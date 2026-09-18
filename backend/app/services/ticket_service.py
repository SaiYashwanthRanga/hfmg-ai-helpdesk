import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.models import (
    VALID_STATUS_TRANSITIONS,
    AISummaryStatus,
    Category,
    Priority,
    Ticket,
    TicketStatus,
)
from app.schemas.ticket import TicketCreate

settings = get_settings()


async def _next_ticket_number(db: AsyncSession) -> str:
    """Generate a human-facing ticket number like HFMG-2026-000482.

    Counts existing tickets for the current year rather than a DB sequence,
    which keeps the MVP schema to just two tables. Revisit with a real
    Postgres sequence (see DATABASE_DESIGN.md 3.1.1) once concurrent ticket
    creation volume makes the count-based approach a contention risk.
    """
    year = datetime.now(timezone.utc).year
    prefix = f"HFMG-{year}-"
    count_stmt = select(func.count()).select_from(Ticket).where(Ticket.ticket_number.startswith(prefix))
    count = (await db.execute(count_stmt)).scalar_one()
    return f"{prefix}{count + 1:06d}"


async def create_ticket(db: AsyncSession, payload: TicketCreate) -> Ticket:
    category = await db.get(Category, payload.category_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown or inactive category")

    priority = payload.priority or category.default_priority or Priority.MEDIUM

    ticket = Ticket(
        ticket_number=await _next_ticket_number(db),
        caller_name=payload.caller_name,
        phone_number=payload.phone_number,
        email=payload.email,
        category_id=payload.category_id,
        priority=priority,
        description=payload.description,
        status=TicketStatus.NEW,
        ai_summary_status=AISummaryStatus.PENDING if settings.enable_ai_summary else AISummaryStatus.DISABLED,
    )
    db.add(ticket)
    await db.commit()
    # Re-fetch rather than a partial refresh() so every column (including
    # server-generated ones like updated_at) is loaded before this leaves
    # the async session — a lazy load triggered later from sync Pydantic
    # code would fail with a MissingGreenlet error.
    return await get_ticket(db, ticket.id)


async def get_ticket(db: AsyncSession, ticket_id: uuid.UUID) -> Ticket:
    stmt = select(Ticket).options(selectinload(Ticket.category)).where(Ticket.id == ticket_id)
    ticket = (await db.execute(stmt)).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return ticket


async def list_tickets(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    ticket_status: TicketStatus | None = None,
    category_id: uuid.UUID | None = None,
) -> tuple[list[Ticket], int]:
    filters = []
    if ticket_status is not None:
        filters.append(Ticket.status == ticket_status)
    if category_id is not None:
        filters.append(Ticket.category_id == category_id)

    base_stmt = select(Ticket).options(selectinload(Ticket.category))
    count_stmt = select(func.count()).select_from(Ticket)
    for f in filters:
        base_stmt = base_stmt.where(f)
        count_stmt = count_stmt.where(f)

    total = (await db.execute(count_stmt)).scalar_one()

    stmt = base_stmt.order_by(Ticket.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    return list(items), total


async def update_status(db: AsyncSession, ticket_id: uuid.UUID, new_status: TicketStatus) -> Ticket:
    ticket = await get_ticket(db, ticket_id)
    allowed = VALID_STATUS_TRANSITIONS.get(ticket.status, set())
    if new_status != ticket.status and new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot transition ticket from {ticket.status.value} to {new_status.value}",
        )
    ticket.status = new_status
    await db.commit()
    return await get_ticket(db, ticket_id)


async def set_ai_summary(db: AsyncSession, ticket_id: uuid.UUID, *, summary: str | None, failed: bool) -> None:
    ticket = await db.get(Ticket, ticket_id)
    if ticket is None:
        return
    if failed:
        ticket.ai_summary_status = AISummaryStatus.FAILED
    else:
        ticket.ai_summary = summary
        ticket.ai_summary_status = AISummaryStatus.COMPLETED
        ticket.ai_summary_generated_at = datetime.now(timezone.utc)
    await db.commit()

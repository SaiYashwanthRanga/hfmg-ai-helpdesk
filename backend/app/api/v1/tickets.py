import math
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.summarizer import generate_summary_for_ticket
from app.db.base import get_db
from app.db.models import Priority, TicketSource, TicketStatus
from app.notifications.email import send_ticket_notification
from app.schemas.ticket import (
    TicketCreate,
    TicketListItem,
    TicketPage,
    TicketRead,
    TicketStatusUpdate,
    TicketSummaryStatusResponse,
)
from app.services import ticket_service

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.post("", response_model=TicketRead, status_code=201)
async def create_ticket(
    payload: TicketCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> TicketRead:
    ticket = await ticket_service.create_ticket(db, payload)

    background_tasks.add_task(send_ticket_notification, ticket, "created")
    background_tasks.add_task(generate_summary_for_ticket, ticket.id)

    return TicketRead.model_validate(ticket)


@router.get("", response_model=TicketPage)
async def list_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: TicketStatus | None = None,
    category_id: uuid.UUID | None = None,
    priority: Priority | None = None,
    source: TicketSource | None = None,
    q: str | None = Query(None, min_length=1, max_length=200),
    db: AsyncSession = Depends(get_db),
) -> TicketPage:
    items, total = await ticket_service.list_tickets(
        db,
        page=page,
        page_size=page_size,
        ticket_status=status,
        category_id=category_id,
        priority=priority,
        source=source,
        q=q,
    )
    total_pages = math.ceil(total / page_size) if total else 0
    return TicketPage(
        items=[TicketListItem.model_validate(t) for t in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@router.get("/{ticket_id}", response_model=TicketRead)
async def get_ticket(ticket_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> TicketRead:
    ticket = await ticket_service.get_ticket(db, ticket_id)
    return TicketRead.model_validate(ticket)


@router.post("/{ticket_id}/regenerate-summary", response_model=TicketSummaryStatusResponse, status_code=202)
async def regenerate_summary(
    ticket_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> TicketSummaryStatusResponse:
    ticket = await ticket_service.mark_summary_pending(db, ticket_id)
    background_tasks.add_task(generate_summary_for_ticket, ticket.id)
    return TicketSummaryStatusResponse(ai_summary_status=ticket.ai_summary_status)


@router.post("/{ticket_id}/status", response_model=TicketRead)
async def update_ticket_status(
    ticket_id: uuid.UUID,
    payload: TicketStatusUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> TicketRead:
    ticket = await ticket_service.update_status(db, ticket_id, payload.status)
    background_tasks.add_task(send_ticket_notification, ticket, "status_changed")
    return TicketRead.model_validate(ticket)

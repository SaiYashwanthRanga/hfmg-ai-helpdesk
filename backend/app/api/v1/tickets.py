import math
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.summarizer import generate_summary_for_ticket
from app.db.base import get_db
from app.db.models import TicketStatus
from app.notifications.email import send_ticket_notification
from app.schemas.ticket import TicketCreate, TicketListItem, TicketPage, TicketRead, TicketStatusUpdate
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
    db: AsyncSession = Depends(get_db),
) -> TicketPage:
    items, total = await ticket_service.list_tickets(
        db, page=page, page_size=page_size, ticket_status=status, category_id=category_id
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

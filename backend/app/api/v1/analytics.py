from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.schemas.analytics import (
    ActivityItem,
    AiSummaryUsageResponse,
    CallsByDayResponse,
    CategoryBreakdownResponse,
    EscalationRateResponse,
    KpiReport,
    KpiValue,
    PriorityBreakdownResponse,
    RecentActivityResponse,
    SourceBreakdownResponse,
)
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Matches WIREFRAMES.md's Analytics page default range.
DaysWindow = Query(30, ge=1, le=365)


@router.get("/kpis", response_model=KpiReport)
async def get_kpis(db: AsyncSession = Depends(get_db)) -> KpiReport:
    data = await analytics_service.get_kpis(db)
    return KpiReport(**{key: KpiValue(**value) for key, value in data.items()})


@router.get("/recent-activity", response_model=RecentActivityResponse)
async def get_recent_activity(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> RecentActivityResponse:
    tickets = await analytics_service.get_recent_activity(db, limit=limit)
    items = [
        ActivityItem(
            type="ticket_created",
            occurred_at=ticket.created_at,
            ticket_id=ticket.id,
            ticket_number=ticket.ticket_number,
            caller_name=ticket.caller_name,
            category=ticket.category.name,
            priority=ticket.priority,
            status=ticket.status,
        )
        for ticket in tickets
    ]
    return RecentActivityResponse(items=items)


@router.get("/tickets-by-category", response_model=CategoryBreakdownResponse)
async def tickets_by_category(days: int = DaysWindow, db: AsyncSession = Depends(get_db)) -> CategoryBreakdownResponse:
    items = await analytics_service.get_tickets_by_category(db, days=days)
    return CategoryBreakdownResponse(items=items)


@router.get("/tickets-by-priority", response_model=PriorityBreakdownResponse)
async def tickets_by_priority(days: int = DaysWindow, db: AsyncSession = Depends(get_db)) -> PriorityBreakdownResponse:
    items = await analytics_service.get_tickets_by_priority(db, days=days)
    return PriorityBreakdownResponse(items=items)


@router.get("/tickets-by-source", response_model=SourceBreakdownResponse)
async def tickets_by_source(days: int = DaysWindow, db: AsyncSession = Depends(get_db)) -> SourceBreakdownResponse:
    items = await analytics_service.get_tickets_by_source(db, days=days)
    return SourceBreakdownResponse(items=items)


@router.get("/calls-by-day", response_model=CallsByDayResponse)
async def calls_by_day(days: int = DaysWindow, db: AsyncSession = Depends(get_db)) -> CallsByDayResponse:
    items = await analytics_service.get_calls_by_day(db, days=days)
    return CallsByDayResponse(items=items)


@router.get("/escalation-rate", response_model=EscalationRateResponse)
async def escalation_rate(days: int = DaysWindow, db: AsyncSession = Depends(get_db)) -> EscalationRateResponse:
    data = await analytics_service.get_escalation_rate(db, days=days)
    return EscalationRateResponse(**data)


@router.get("/ai-summary-usage", response_model=AiSummaryUsageResponse)
async def ai_summary_usage(days: int = DaysWindow, db: AsyncSession = Depends(get_db)) -> AiSummaryUsageResponse:
    items = await analytics_service.get_ai_summary_usage(db, days=days)
    return AiSummaryUsageResponse(items=items)

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.db.models import AISummaryStatus, Priority, TicketSource, TicketStatus


class KpiValue(BaseModel):
    """Every KPI reports a `status` alongside its `value` so the frontend
    can render an honest pending/blocked state instead of a fabricated
    number (BACKEND_GAP_ANALYSIS.md Tier 1, DESIGN.md §20)."""

    status: Literal["ready", "blocked"]
    value: int | None = None
    blocked_reason: str | None = None


class KpiReport(BaseModel):
    open_tickets: KpiValue
    tickets_today: KpiValue
    calls_today: KpiValue
    escalations: KpiValue
    ai_resolution_rate: KpiValue


class ActivityItem(BaseModel):
    """Only `ticket_created` exists today. Voice-call data is available now
    (Tier 2's /voice-calls endpoints), but merging call events into this
    feed is a separate, not-yet-built enhancement -- out of Tier 3's scope
    (six aggregation endpoints), tracked in WORK_LOG.md instead of bundled
    in here silently. A finer-grained ticket event (e.g. "status changed")
    still needs an audit_log table, which remains Phase 3 backend/auth scope."""

    type: Literal["ticket_created"]
    occurred_at: datetime
    ticket_id: uuid.UUID
    ticket_number: str
    caller_name: str
    category: str
    priority: Priority
    status: TicketStatus


class RecentActivityResponse(BaseModel):
    items: list[ActivityItem]


# --- Tier 3: chart-ready aggregations (BACKEND_GAP_ANALYSIS.md) ---
# Every endpoint pre-aggregates server-side (DESIGN.md §10's hard rule: never
# ship a chart that fetches raw rows and aggregates client-side) and takes an
# optional `days` window (default 30, matching WIREFRAMES.md's Analytics
# page default range).


class CategoryBreakdownItem(BaseModel):
    category: str
    count: int


class CategoryBreakdownResponse(BaseModel):
    items: list[CategoryBreakdownItem]


class PriorityBreakdownItem(BaseModel):
    priority: Priority
    count: int


class PriorityBreakdownResponse(BaseModel):
    items: list[PriorityBreakdownItem]


class SourceBreakdownItem(BaseModel):
    source: TicketSource
    count: int


class SourceBreakdownResponse(BaseModel):
    items: list[SourceBreakdownItem]


class CallsByDayItem(BaseModel):
    date: date
    count: int


class CallsByDayResponse(BaseModel):
    items: list[CallsByDayItem]


class EscalationRateResponse(BaseModel):
    """Definition (not yet product-confirmed -- see REMAINING_PRODUCT_DECISIONS.md):
    escalated_calls / calls that reached a terminal state (COMPLETED,
    ESCALATED, ABANDONED) in the window. In-progress calls are excluded
    since they haven't reached an outcome yet."""

    total_terminal_calls: int
    escalated_calls: int
    rate_percent: float


class AiSummaryUsageItem(BaseModel):
    status: AISummaryStatus
    count: int
    percentage: float


class AiSummaryUsageResponse(BaseModel):
    items: list[AiSummaryUsageItem]

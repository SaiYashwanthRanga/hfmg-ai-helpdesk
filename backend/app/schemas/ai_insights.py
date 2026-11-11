import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.db.models import Priority
from app.schemas.analytics import CategoryBreakdownItem


class CategoryBreakdownInsight(BaseModel):
    status: Literal["ready"] = "ready"
    items: list[CategoryBreakdownItem]


class BlockedInsightSection(BaseModel):
    """Every AI Insights section WIREFRAMES.md names but this change doesn't
    build renders this shape -- `status` is always "blocked" and there is no
    `items` field, so a client can't accidentally render an empty list as
    "no results" when the truth is "not implemented, see blocked_reason"
    (same discipline as KpiValue in schemas/analytics.py)."""

    status: Literal["blocked"] = "blocked"
    blocked_reason: str


class AIInsightsResponse(BaseModel):
    category_breakdown: CategoryBreakdownInsight
    trending_issues: BlockedInsightSection
    repeated_problems: BlockedInsightSection
    high_risk_alerts: BlockedInsightSection
    recommendations: BlockedInsightSection


class InsightTicketRef(BaseModel):
    """A ticket an AI statement is based on, so the dashboard can open it."""

    id: uuid.UUID
    ticket_number: str


class InsightTheme(BaseModel):
    """A recurring problem the model found. Only `title` and `note` are the model's words; the counts, priority and
    categories are computed from the tickets it cited."""

    title: str
    note: str
    ticket_count: int
    open_count: int
    top_priority: Priority
    categories: list[str]
    tickets: list[InsightTicketRef]


class InsightAttention(BaseModel):
    title: str
    reason: str
    severity: Literal["high", "medium"]
    tickets: list[InsightTicketRef]


class InsightRecommendation(BaseModel):
    action: str
    rationale: str
    tickets: list[InsightTicketRef]


class CategoryMovementItem(BaseModel):
    """Tickets in this window and in the same-length window before it. Both numbers, no verdict."""

    category: str
    count: int
    previous_count: int


class AIAnalysisResponse(BaseModel):
    """`status` says whether the AI part is there: "ready", "empty" (no tickets in the window) or "unavailable"
    (`reason`: "not_configured", "disabled" or "failed"). The counts and `category_movement` are always real."""

    status: Literal["ready", "empty", "unavailable"]
    reason: Literal["not_configured", "disabled", "failed"] | None = None
    generated_at: datetime | None = None
    days: int
    tickets_in_period: int
    open_in_period: int
    tickets_in_previous_period: int
    tickets_analyzed: int
    headline: str
    briefing: str
    themes: list[InsightTheme]
    attention: list[InsightAttention]
    recommendations: list[InsightRecommendation]
    category_movement: list[CategoryMovementItem]

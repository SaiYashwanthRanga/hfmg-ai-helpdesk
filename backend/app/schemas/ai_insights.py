from typing import Literal

from pydantic import BaseModel

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

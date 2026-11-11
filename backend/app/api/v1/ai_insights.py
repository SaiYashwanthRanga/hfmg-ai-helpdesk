from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.schemas.ai_insights import (
    AIAnalysisResponse,
    AIInsightsResponse,
    BlockedInsightSection,
    CategoryBreakdownInsight,
)
from app.services import ai_insights_analysis, ai_insights_service

router = APIRouter(prefix="/ai-insights", tags=["ai-insights"])


@router.get("", response_model=AIInsightsResponse)
async def get_ai_insights(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> AIInsightsResponse:
    data = await ai_insights_service.get_ai_insights(db, days=days)
    return AIInsightsResponse(
        category_breakdown=CategoryBreakdownInsight(**data["category_breakdown"]),
        trending_issues=BlockedInsightSection(**data["trending_issues"]),
        repeated_problems=BlockedInsightSection(**data["repeated_problems"]),
        high_risk_alerts=BlockedInsightSection(**data["high_risk_alerts"]),
        recommendations=BlockedInsightSection(**data["recommendations"]),
    )


@router.get("/analysis", response_model=AIAnalysisResponse)
async def get_ai_analysis(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> AIAnalysisResponse:
    """The model's reading of the window's tickets (cached), with the real counts beside it."""
    return AIAnalysisResponse(**await ai_insights_analysis.get_analysis(db, days=days))


@router.post("/analysis/refresh", response_model=AIAnalysisResponse)
async def refresh_ai_analysis(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> AIAnalysisResponse:
    """Ask the model again now, replacing the cached reading. POST because it spends model credit."""
    return AIAnalysisResponse(**await ai_insights_analysis.get_analysis(db, days=days, refresh=True))

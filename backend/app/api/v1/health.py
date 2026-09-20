from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.schemas.health import DependencyHealthReport, DependencyStatus
from app.services.dependency_health import get_dependency_health

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Readiness check (API_SPEC.md §9) -- verifies DB connectivity.

    There is no worker queue to check (BackgroundTasks only, per the MVP
    scope decision in IMPLEMENTATION_PLAN.md) -- DB reachability is the only
    real dependency this process has at request time.
    """
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not reachable",
        ) from exc
    return {"status": "ok"}


@router.get("/health/dependencies", response_model=DependencyHealthReport)
async def health_dependencies(db: AsyncSession = Depends(get_db)) -> DependencyHealthReport:
    """OpenAI/Twilio/Database/Email status strip (DESIGN.md §6.1). Each
    check is cached for a short interval -- see app/services/dependency_health.py.
    """
    checks = await get_dependency_health(db)
    return DependencyHealthReport(
        openai=DependencyStatus(status=checks["openai"].status, checked_at=checks["openai"].checked_at),
        twilio=DependencyStatus(status=checks["twilio"].status, checked_at=checks["twilio"].checked_at),
        database=DependencyStatus(status=checks["database"].status, checked_at=checks["database"].checked_at),
        email=DependencyStatus(status=checks["email"].status, checked_at=checks["email"].checked_at),
    )

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.masking import mask_secret
from app.db.base import get_db
from app.schemas.settings import EnvironmentStatus, ProviderStatus, SettingsStatusResponse
from app.services.dependency_health import get_dependency_health

router = APIRouter(prefix="/settings", tags=["settings"])
settings = get_settings()


@router.get("/status", response_model=SettingsStatusResponse)
async def settings_status(db: AsyncSession = Depends(get_db)) -> SettingsStatusResponse:
    """Read-only configuration visibility (DESIGN.md §12).

    No write path exists here, and none should be added until Phase 3
    authentication ships -- editing OPENAI_API_KEY/TWILIO_AUTH_TOKEN/
    SENDGRID_API_KEY with no auth in front of this API is a live
    vulnerability, not a rough edge (IMPLEMENTATION_PLAN.md MVP Scope
    Decision). Every secret below is masked; the raw values never leave
    app/core/config.py.
    """
    checks = await get_dependency_health(db)

    return SettingsStatusResponse(
        openai=ProviderStatus(
            configured=bool(settings.openai_api_key),
            status=checks["openai"].status,
            masked_key=mask_secret(settings.openai_api_key),
            detail=f"Model: {settings.openai_model}" if settings.openai_api_key else None,
            last_verified=checks["openai"].checked_at,
        ),
        twilio=ProviderStatus(
            configured=bool(settings.twilio_auth_token),
            status=checks["twilio"].status,
            masked_key=mask_secret(settings.twilio_auth_token),
            detail=None,
            last_verified=checks["twilio"].checked_at,
        ),
        email=ProviderStatus(
            configured=bool(settings.sendgrid_api_key),
            status=checks["email"].status,
            masked_key=mask_secret(settings.sendgrid_api_key),
            detail=(
                f"From {settings.email_from} to {settings.helpdesk_email}"
                if settings.sendgrid_api_key
                else None
            ),
            last_verified=checks["email"].checked_at,
        ),
        database=ProviderStatus(
            configured=True,
            status=checks["database"].status,
            masked_key=None,
            detail="PostgreSQL",
            last_verified=checks["database"].checked_at,
        ),
        environment=EnvironmentStatus(
            environment=settings.environment,
            enable_ai_summary=settings.enable_ai_summary,
            enable_email_notifications=settings.enable_email_notifications,
        ),
    )

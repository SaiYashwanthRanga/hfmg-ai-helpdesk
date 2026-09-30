from datetime import datetime

from pydantic import BaseModel

from app.services.dependency_health import DependencyStatusValue


class ProviderStatus(BaseModel):
    """Read-only provider status (DESIGN.md §12). `masked_key` is either a
    masked secret (see app/core/masking.py) or None -- this schema must
    never carry a field capable of holding a raw secret."""

    configured: bool
    status: DependencyStatusValue
    masked_key: str | None = None
    detail: str | None = None
    last_verified: datetime


class EnvironmentStatus(BaseModel):
    environment: str
    enable_ai_summary: bool
    enable_email_notifications: bool
    # True only when ENABLE_VOICE_SIMULATOR=true outside production; the
    # dashboard hides the AI Call Simulator nav entry otherwise.
    voice_simulator_enabled: bool = False


class SettingsStatusResponse(BaseModel):
    openai: ProviderStatus
    sip: ProviderStatus
    email: ProviderStatus
    database: ProviderStatus
    environment: EnvironmentStatus

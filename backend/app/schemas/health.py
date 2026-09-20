from datetime import datetime

from pydantic import BaseModel

from app.services.dependency_health import DependencyStatusValue


class DependencyStatus(BaseModel):
    status: DependencyStatusValue
    checked_at: datetime


class DependencyHealthReport(BaseModel):
    openai: DependencyStatus
    twilio: DependencyStatus
    database: DependencyStatus
    email: DependencyStatus

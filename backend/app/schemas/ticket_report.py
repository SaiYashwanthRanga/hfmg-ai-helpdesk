from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ReportEmailConfig(BaseModel):
    configured: bool
    recipient: str | None = None
    problem: str | None = None


class ReportConfig(BaseModel):
    """What the dashboard needs to know before an upload: whether the feature is on, the limits, and whether
    the report could be sent (the recipient is shown, never chosen, in the browser)."""

    enabled: bool
    accepted_extensions: list[str] = []
    max_upload_bytes: int = 0
    max_rows: int = 0
    ai_configured: bool = False
    email: ReportEmailConfig = ReportEmailConfig(configured=False)


class ValidationIssue(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] | None = None


class ReportValidation(BaseModel):
    valid: bool
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    fields_present: list[str] = []


class DetectedColumn(BaseModel):
    name: str
    field: str | None = None
    used: bool = False
    note: str | None = None


class ReportProgress(BaseModel):
    stage: str
    done: int = 0
    total: int = 0
    message: str = ""


class ReportEmailStatus(BaseModel):
    status: str  # NOT_SENT | SENDING | SENT | FAILED
    recipient: str | None = None
    attempts: int = 0
    sent_at: datetime | None = None
    last_attempt_at: datetime | None = None
    error: str | None = None
    can_send: bool = False
    blocked_reason: str | None = None


class ReportRead(BaseModel):
    id: str
    filename: str
    file_type: str
    status: str  # UPLOADED | ANALYZING | COMPLETED | FAILED
    created_at: datetime
    source_row_count: int
    row_count: int
    columns: list[DetectedColumn]
    validation: ReportValidation
    can_analyze: bool
    progress: ReportProgress | None = None
    error: str | None = None
    analysis: dict[str, Any] | None = None
    analysis_completed_at: datetime | None = None
    email: ReportEmailStatus


class SendResult(BaseModel):
    report: ReportRead
    already_sent: bool = False
    message: str

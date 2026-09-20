import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import EscalationReason, VoiceCallState


class VoiceCallTurn(BaseModel):
    """One entry of `voice_call_sessions.turns` (CALL_FLOW.md transcript)."""

    role: str
    text: str
    confidence: float | None = None
    at: str


class VoiceCallListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    twilio_call_sid: str
    from_number: str
    state: VoiceCallState
    escalated: bool
    escalation_reason: EscalationReason | None
    ticket_id: uuid.UUID | None
    created_at: datetime
    ended_at: datetime | None

    # Denormalized from `collected` for the list view -- avoids the frontend
    # having to parse JSONB shape just to render a table column.
    caller_name: str | None = None
    category: str | None = None
    priority: str | None = None


class VoiceCallDetail(VoiceCallListItem):
    to_number: str
    misunderstanding_count: int
    email_attempt_count: int
    collected: dict
    turns: list[VoiceCallTurn]
    updated_at: datetime


class VoiceCallPage(BaseModel):
    items: list[VoiceCallListItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class VoiceCallSummary(BaseModel):
    """Active/Completed/Escalated counts for the Voice Operations Center
    (WIREFRAMES.md §5). `active` = any non-terminal state."""

    active: int
    completed: int
    escalated: int
    abandoned: int

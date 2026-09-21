import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models import AISummaryStatus, Priority, TicketSource, TicketStatus


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    default_priority: Priority | None = None


class TicketCreate(BaseModel):
    caller_name: str = Field(min_length=1, max_length=200)
    phone_number: str = Field(min_length=1, max_length=32)
    email: EmailStr | None = None
    category_id: uuid.UUID
    priority: Priority | None = None
    description: str = Field(min_length=1, max_length=10_000)

    @field_validator("caller_name", "phone_number", "description", mode="after")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Reject whitespace-only values.

        `min_length=1` alone lets a string of pure whitespace (e.g. a single
        space) through, which would create a ticket with an effectively
        empty caller name or description. Stripped for storage too, so
        leading/trailing whitespace from a form or voice transcript doesn't
        linger in the record.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class TicketStatusUpdate(BaseModel):
    status: TicketStatus


class TicketListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_number: str
    caller_name: str
    category: CategoryRead
    priority: Priority
    status: TicketStatus
    ai_summary_status: AISummaryStatus
    source: TicketSource
    created_at: datetime


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticket_number: str
    caller_name: str
    phone_number: str
    email: str | None
    category: CategoryRead
    priority: Priority
    description: str
    ai_summary: str | None
    ai_summary_status: AISummaryStatus
    ai_summary_generated_at: datetime | None
    status: TicketStatus
    source: TicketSource
    created_at: datetime
    updated_at: datetime


class TicketPage(BaseModel):
    items: list[TicketListItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class TicketSummaryStatusResponse(BaseModel):
    """Response shape for POST /tickets/{id}/regenerate-summary (API_SPEC.md §3)."""

    ai_summary_status: AISummaryStatus

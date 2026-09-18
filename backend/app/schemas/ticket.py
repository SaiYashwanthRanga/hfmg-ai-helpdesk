import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.models import AISummaryStatus, Priority, TicketStatus


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
    created_at: datetime
    updated_at: datetime


class TicketPage(BaseModel):
    items: list[TicketListItem]
    page: int
    page_size: int
    total: int
    total_pages: int

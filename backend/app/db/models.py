import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, SmallInteger, String, Text, func, text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Enum as SQLEnum

from app.db.base import Base


class Priority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TicketStatus(str, enum.Enum):
    NEW = "NEW"
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    ON_HOLD = "ON_HOLD"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class AISummaryStatus(str, enum.Enum):
    DISABLED = "DISABLED"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TicketSource(str, enum.Enum):
    WEB = "WEB"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    WALK_IN = "WALK_IN"
    # Created by the AI Call Simulator (VOICE_SIMULATOR_DESIGN.md). Excluded
    # from the queue, dashboards and analytics -- see OPERATIONAL_SOURCES.
    SIMULATOR = "SIMULATOR"


# Every ticket source that represents real help desk work. Operational
# queries filter on this so simulator test tickets never reach a dashboard.
OPERATIONAL_SOURCES = tuple(s for s in TicketSource if s is not TicketSource.SIMULATOR)


class VoiceCallState(str, enum.Enum):
    GREETING = "GREETING"
    COLLECT_DESCRIPTION = "COLLECT_DESCRIPTION"
    # When it started / whether the caller can work (asked only if not volunteered).
    COLLECT_DETAILS = "COLLECT_DETAILS"
    COLLECT_NAME = "COLLECT_NAME"
    # Name read back spelled; on "no", the caller spells first and last name.
    CONFIRM_NAME = "CONFIRM_NAME"
    COLLECT_PHONE = "COLLECT_PHONE"
    # The callback number read back ("is this the best number?"); a "no" asks for another.
    CONFIRM_CALLBACK_NUMBER = "CONFIRM_CALLBACK_NUMBER"
    COLLECT_ALTERNATE_CALLBACK_NUMBER = "COLLECT_ALTERNATE_CALLBACK_NUMBER"
    COLLECT_EMAIL = "COLLECT_EMAIL"
    CONFIRM_EMAIL = "CONFIRM_EMAIL"
    CONFIRM_CATEGORY = "CONFIRM_CATEGORY"
    # The whole ticket read back (who, what, since when, priority and why)
    # before it is created; the caller can correct it.
    CONFIRM_SUMMARY = "CONFIRM_SUMMARY"
    ANYTHING_ELSE = "ANYTHING_ELSE"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


class EscalationReason(str, enum.Enum):
    CALLER_REQUESTED = "CALLER_REQUESTED"
    REPEATED_MISUNDERSTANDING = "REPEATED_MISUNDERSTANDING"
    SYSTEM_ERROR = "SYSTEM_ERROR"


# Valid forward transitions for a ticket's status. Used by TicketService to
# reject nonsensical transitions (e.g. CLOSED -> NEW) before hitting the DB.
VALID_STATUS_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.NEW: {TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.CANCELLED},
    TicketStatus.OPEN: {TicketStatus.IN_PROGRESS, TicketStatus.ON_HOLD, TicketStatus.RESOLVED, TicketStatus.CANCELLED},
    TicketStatus.IN_PROGRESS: {TicketStatus.ON_HOLD, TicketStatus.RESOLVED, TicketStatus.CANCELLED},
    TicketStatus.ON_HOLD: {TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED, TicketStatus.CANCELLED},
    TicketStatus.RESOLVED: {TicketStatus.CLOSED, TicketStatus.IN_PROGRESS},
    TicketStatus.CLOSED: set(),
    TicketStatus.CANCELLED: set(),
}


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_priority: Mapped[Priority | None] = mapped_column(
        SQLEnum(Priority, name="priority_enum"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="category")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)

    caller_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # The number to call: the confirmed callback number when there is one, else the caller's number.
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    # Voice tickets only: what the call came in from (audit/debugging), and the number the
    # caller confirmed for the IT team to use. NULL for web tickets and unconfirmed calls.
    caller_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    callback_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(CITEXT, nullable=True)

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    priority: Mapped[Priority] = mapped_column(
        SQLEnum(Priority, name="priority_enum"), nullable=False, default=Priority.MEDIUM
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)

    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary_status: Mapped[AISummaryStatus] = mapped_column(
        SQLEnum(AISummaryStatus, name="ai_summary_status_enum"),
        nullable=False,
        default=AISummaryStatus.DISABLED,
    )
    ai_summary_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[TicketStatus] = mapped_column(
        SQLEnum(TicketStatus, name="ticket_status_enum"), nullable=False, default=TicketStatus.NEW
    )
    source: Mapped[TicketSource] = mapped_column(
        SQLEnum(TicketSource, name="ticket_source_enum"), nullable=False, default=TicketSource.WEB
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    category: Mapped["Category"] = relationship(back_populates="tickets")


class VoiceCallSession(Base):
    """Per-call conversation state for the SIP voice agent.

    Lives in Postgres rather than memory because each webhook turn is an
    independent request that may land on any API replica.
    """

    __tablename__ = "voice_call_sessions"
    __table_args__ = (
        # Partial: simulated rows are the minority, and only simulator
        # queries look them up by this flag.
        Index("ix_voice_call_sessions_is_simulated", "is_simulated", postgresql_where=text("is_simulated")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    from_number: Mapped[str] = mapped_column(String(32), nullable=False)
    to_number: Mapped[str] = mapped_column(String(32), nullable=False)

    state: Mapped[VoiceCallState] = mapped_column(
        SQLEnum(VoiceCallState, name="voice_call_state_enum"),
        nullable=False,
        default=VoiceCallState.GREETING,
    )
    collected: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    turns: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    misunderstanding_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    email_attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    escalated: Mapped[bool] = mapped_column(nullable=False, default=False)
    escalation_reason: Mapped[EscalationReason | None] = mapped_column(
        SQLEnum(EscalationReason, name="escalation_reason_enum"), nullable=True
    )

    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # True for AI Call Simulator sessions. Operational queries (Calls page,
    # dashboards, analytics) exclude these rows.
    is_simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VoiceSimulatorSession(Base):
    """Simulator-only options for a simulated VoiceCallSession.

    Kept out of voice_call_sessions so the phone path's table carries no
    simulator columns beyond the is_simulated flag.
    """

    __tablename__ = "voice_simulator_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_call_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    send_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    end_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class VoiceSimulatorTurn(Base):
    """One simulated caller turn: transcript, timings and debug trace. Never audio."""

    __tablename__ = "voice_simulator_turns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_call_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    turn_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # "transcribed" after /audio, "completed" after /process.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_mode: Mapped[str] = mapped_column(String(8), nullable=False)

    utterance: Mapped[str | None] = mapped_column(Text, nullable=True)
    stt_raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stt_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    state_before: Mapped[VoiceCallState | None] = mapped_column(
        SQLEnum(VoiceCallState, name="voice_call_state_enum"), nullable=True
    )
    state_after: Mapped[VoiceCallState | None] = mapped_column(
        SQLEnum(VoiceCallState, name="voice_call_state_enum"), nullable=True
    )
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_ended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    collected_after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ticket_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    llm_trace: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Server-measured, milliseconds.
    stt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tts_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ticket_create_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    server_total_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Browser-reported, milliseconds (POST /client-metrics).
    utterance_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    capture_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_wait_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    playback_start_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    playback_duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    turn_total_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

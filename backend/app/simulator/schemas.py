"""Request/response models for /api/v1/voice-simulator.

Mirrored field for field by frontend/src/types/voiceSimulator.ts.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import AISummaryStatus, EscalationReason, Priority, TicketStatus, VoiceCallState

InputMode = Literal["voice", "text", "mock", "system"]
EndReason = Literal["user_hangup", "agent_hangup", "error", "cleared", "idle"]


class CollectedSlots(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str | None = None
    short_issue: str | None = None
    caller_name: str | None = None
    phone_number: str | None = None
    email: str | None = None
    category: str | None = None
    category_confidence: str | None = None
    priority: str | None = None
    impact: str | None = None
    department: str | None = None
    #: False when the department isn't on HFMG's list (read back, marked on the ticket).
    department_verified: bool | None = None
    started: str | None = None
    work_blocked: bool | None = None
    #: Why the priority is what it is ("you can't work"): the same text the agent speaks.
    priority_reason: str | None = None
    #: "low" = unfamiliar name, read back spelled before it is trusted.
    name_confidence: str | None = None


class SimulatorTicket(BaseModel):
    id: uuid.UUID
    ticket_number: str
    status: TicketStatus
    category: str
    priority: Priority
    ai_summary_status: AISummaryStatus
    ai_summary: str | None


class SimulatorSession(BaseModel):
    id: uuid.UUID
    label: str | None
    state: VoiceCallState
    collected: CollectedSlots
    misunderstanding_count: int
    escalated: bool
    escalation_reason: EscalationReason | None
    ticket: SimulatorTicket | None
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    tts_enabled: bool
    send_notifications: bool
    caller_id: str | None


class ReplyAudio(BaseModel):
    base64: str
    mime_type: str


class AgentReply(BaseModel):
    text: str
    call_ended: bool
    # Deprecated: always null. Speech is streamed from `speech_path`
    # (GET /api/v1 + speech_path) so playback starts on the first bytes.
    audio: ReplyAudio | None = None
    speech_path: str | None = None


class TurnTimings(BaseModel):
    turn_index: int
    stt_ms: float | None
    llm_ms: float | None
    tts_ms: float | None
    ticket_create_ms: float | None
    queue_wait_ms: float | None
    server_total_ms: float | None
    utterance_ms: float | None
    capture_ms: float | None
    playback_start_ms: float | None
    playback_duration_ms: float | None
    turn_total_ms: float | None


class TurnError(BaseModel):
    stage: Literal["stt", "llm", "tts", "agent", "db"]
    type: str
    message: str


class SimulatorTurn(BaseModel):
    id: uuid.UUID
    turn_client_id: uuid.UUID
    turn_index: int
    status: str
    input_mode: InputMode
    utterance: str | None
    stt_confidence: float | None
    stt_raw: dict[str, Any] | None
    state_before: VoiceCallState | None
    state_after: VoiceCallState | None
    intent: str | None
    agent_text: str | None
    call_ended: bool
    collected_after: CollectedSlots | None
    ticket_payload: dict[str, Any] | None
    llm_trace: list[dict[str, Any]]
    errors: list[TurnError]
    timings: TurnTimings
    created_at: datetime


# --- requests -------------------------------------------------------------


class StartRequest(BaseModel):
    caller_id: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"^\+?[0-9 ()\-]{7,31}$",
        description="Simulated caller ID. When usable, the agent skips the phone question.",
    )
    send_notifications: bool = False
    tts: bool = True
    label: str | None = Field(default=None, max_length=200)


class ProcessRequest(BaseModel):
    session_id: uuid.UUID
    turn_client_id: uuid.UUID
    # None = use the transcript stored by POST /audio for this turn_client_id.
    utterance: str | None = Field(default=None, max_length=2000)
    input_mode: Literal["voice", "text", "mock"] = "text"


class EndRequest(BaseModel):
    session_id: uuid.UUID
    reason: Literal["user_hangup", "agent_hangup", "error", "cleared"] = "user_hangup"


class ClientMetricsRequest(BaseModel):
    turn_client_id: uuid.UUID
    utterance_ms: float | None = Field(default=None, ge=0, le=600_000)
    capture_ms: float | None = Field(default=None, ge=0, le=600_000)
    playback_start_ms: float | None = Field(default=None, ge=0, le=600_000)
    playback_duration_ms: float | None = Field(default=None, ge=0, le=600_000)
    turn_total_ms: float | None = Field(default=None, ge=0, le=600_000)


# --- responses ------------------------------------------------------------


class StartResponse(BaseModel):
    session: SimulatorSession
    greeting: AgentReply
    turn: SimulatorTurn


class AudioTimings(BaseModel):
    stt_ms: float | None
    server_ms: float


class AudioResponse(BaseModel):
    turn_client_id: uuid.UUID
    transcript: str
    confidence: float | None
    empty: bool
    error: str | None
    raw: dict[str, Any] | None
    timings: AudioTimings


class ProcessResponse(BaseModel):
    turn: SimulatorTurn
    reply: AgentReply
    session: SimulatorSession


class SessionDetail(BaseModel):
    session: SimulatorSession
    turns: list[SimulatorTurn]


class StageStats(BaseModel):
    current: float | None
    avg: float | None
    max: float | None
    p95: float | None
    n: int


class MetricsResponse(BaseModel):
    session_id: uuid.UUID
    turns: list[TurnTimings]
    stats: dict[str, StageStats]


class SimulatorStatsResponse(BaseModel):
    """Observability roll-up across all simulated sessions in a window."""

    window_hours: int
    sessions_total: int
    sessions_active: int
    sessions_escalated: int
    tickets_created: int
    turns_total: int
    turns_failed: int
    failure_rate: float
    stages: dict[str, StageStats]
    generated_at: datetime


class SimulatorConfigResponse(BaseModel):
    enabled: bool
    speech_configured: bool
    llm_configured: bool
    stt_model: str
    tts_model: str
    tts_voice: str
    max_audio_bytes: int
    max_turns_per_session: int
    max_concurrent_sessions: int
    notifications_allowed: bool
    voice_categories: list[str]


class MockCaller(BaseModel):
    seed: int
    persona: str
    caller_name: str
    phone_number: str
    email_spoken: str
    issue: str
    # Answers keyed by the agent state being answered, so the caller stays in
    # step with the conversation however the agent branches.
    answers: dict[str, str]
    expect: dict[str, Any]


class RandomIssue(BaseModel):
    seed: int
    category: str
    priority: Priority
    utterance: str
    note: str | None

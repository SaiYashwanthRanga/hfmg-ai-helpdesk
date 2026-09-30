"""AI Call Simulator session lifecycle.

Drives the production conversation code -- app/voice/orchestrator.py -- with
typed or transcribed text instead of the SIP gateway. Nothing here decides
what the agent says, how speech is interpreted, or how tickets are built;
it only feeds turns in, times them, and records what came out.
See VOICE_SIMULATOR_DESIGN.md.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.summarizer import generate_summary_for_ticket
from app.core import trace
from app.core.config import get_settings
from app.db.base import async_session_factory
from app.llm.factory import get_provider
from app.db.models import (
    EscalationReason,
    Ticket,
    VoiceCallSession,
    VoiceCallState,
    VoiceSimulatorSession,
    VoiceSimulatorTurn,
)
from app.notifications.email import send_ticket_notification
from app.services import ticket_service
from app.simulator import schemas
from app.speech import context as speech_context
from app.speech.base import AUDIO_MIME, SUPPORTED_UPLOAD_TYPES, SpeechStreamError, base_mime_type
from app.speech.factory import get_speech_provider
from app.voice import orchestrator, reply, scripts
from app.voice.session import caller_id_is_usable, record_turn

logger = logging.getLogger("hfmg.simulator")

settings = get_settings()

TERMINAL_STATES = (VoiceCallState.COMPLETED, VoiceCallState.ESCALATED, VoiceCallState.ABANDONED)

SIMULATOR_FROM = "simulator"
SIMULATOR_TO = "simulator"


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- loading --------------------------------------------------------------


async def load_session(
    db: AsyncSession, session_id: uuid.UUID, *, lock: bool = False
) -> tuple[VoiceCallSession, VoiceSimulatorSession]:
    """Load a simulated session; 404 for unknown ids and for real calls.

    `lock` takes a row lock for the rest of the transaction so two requests
    for the same session (a double click, a client retry) run one at a time.
    """
    stmt = select(VoiceCallSession).where(
        VoiceCallSession.id == session_id, VoiceCallSession.is_simulated.is_(True)
    )
    if lock:
        stmt = stmt.with_for_update()
    call = (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()
    options = await db.get(VoiceSimulatorSession, session_id) if call is not None else None
    if call is None or options is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulator session not found")
    return call, options


async def _ticket(db: AsyncSession, ticket_id: uuid.UUID | None) -> Ticket | None:
    if ticket_id is None:
        return None
    try:
        return await ticket_service.get_ticket(db, ticket_id)
    except HTTPException:
        return None  # purged, or deleted by hand


# --- serialization --------------------------------------------------------


def _ticket_out(ticket: Ticket | None) -> schemas.SimulatorTicket | None:
    if ticket is None:
        return None
    return schemas.SimulatorTicket(
        id=ticket.id,
        ticket_number=ticket.ticket_number,
        status=ticket.status,
        category=ticket.category.name,
        priority=ticket.priority,
        ai_summary_status=ticket.ai_summary_status,
        ai_summary=ticket.ai_summary,
    )


def _session_out(
    call: VoiceCallSession, options: VoiceSimulatorSession, ticket: Ticket | None
) -> schemas.SimulatorSession:
    return schemas.SimulatorSession(
        id=call.id,
        label=options.label,
        state=call.state,
        collected=schemas.CollectedSlots(**(call.collected or {})),
        misunderstanding_count=call.misunderstanding_count,
        escalated=call.escalated,
        escalation_reason=call.escalation_reason,
        ticket=_ticket_out(ticket),
        started_at=call.created_at,
        ended_at=call.ended_at,
        end_reason=options.end_reason,
        tts_enabled=options.tts_enabled,
        send_notifications=options.send_notifications,
        caller_id=call.from_number if call.from_number != SIMULATOR_FROM else None,
    )


def timings_out(turn: VoiceSimulatorTurn) -> schemas.TurnTimings:
    return schemas.TurnTimings(
        turn_index=turn.turn_index,
        stt_ms=turn.stt_ms,
        llm_ms=turn.llm_ms,
        tts_ms=turn.tts_ms,
        ticket_create_ms=turn.ticket_create_ms,
        queue_wait_ms=turn.queue_wait_ms,
        server_total_ms=turn.server_total_ms,
        utterance_ms=turn.utterance_ms,
        capture_ms=turn.capture_ms,
        playback_start_ms=turn.playback_start_ms,
        playback_duration_ms=turn.playback_duration_ms,
        turn_total_ms=turn.turn_total_ms,
    )


def turn_out(turn: VoiceSimulatorTurn) -> schemas.SimulatorTurn:
    return schemas.SimulatorTurn(
        id=turn.id,
        turn_client_id=turn.turn_client_id,
        turn_index=turn.turn_index,
        status=turn.status,
        input_mode=turn.input_mode,
        utterance=turn.utterance,
        stt_confidence=turn.stt_confidence,
        stt_raw=turn.stt_raw,
        state_before=turn.state_before,
        state_after=turn.state_after,
        intent=turn.intent,
        agent_text=turn.agent_text,
        call_ended=turn.call_ended,
        collected_after=schemas.CollectedSlots(**turn.collected_after) if turn.collected_after else None,
        ticket_payload=turn.ticket_payload,
        llm_trace=turn.llm_trace or [],
        errors=[schemas.TurnError(**e) for e in (turn.errors or [])],
        timings=timings_out(turn),
        created_at=turn.created_at,
    )


# --- speech ---------------------------------------------------------------


def _reply(text: str, *, call_ended: bool, turn_id: uuid.UUID, tts: bool) -> schemas.AgentReply:
    """The agent's reply. Speech is not synthesized here: the browser streams
    it from `speech_path`, so playback starts on the provider's first bytes
    instead of after the whole reply is rendered (and /process returns as
    soon as the agent has decided what to say)."""
    speech_path = f"/voice-simulator/speech/{turn_id}" if tts and text and get_speech_provider().is_configured else None
    return schemas.AgentReply(text=text, call_ended=call_ended, audio=None, speech_path=speech_path)


async def stream_speech(turn_id: uuid.UUID) -> tuple[AsyncIterator[bytes], str]:
    """Open the reply's audio stream; record time-to-first-audio on the turn.

    Raises 404 for unknown / non-simulated turns and 502 if the provider
    produced no audio (the page then shows the reply as text only). The
    turn row is updated through its own session: this runs while the
    response streams, after the request's session has closed.
    """
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(VoiceSimulatorTurn, VoiceSimulatorSession.tts_enabled)
                .join(VoiceCallSession, VoiceCallSession.id == VoiceSimulatorTurn.session_id)
                .join(VoiceSimulatorSession, VoiceSimulatorSession.session_id == VoiceSimulatorTurn.session_id)
                .where(VoiceSimulatorTurn.id == turn_id, VoiceCallSession.is_simulated.is_(True))
            )
        ).first()
    if row is None or not row[0].agent_text or not row[1]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No speech for this turn")
    text = row[0].agent_text

    audio_format = settings.speech_tts_format
    provider = get_speech_provider()
    started = time.perf_counter()
    stream = provider.synthesize_stream(text=text, audio_format=audio_format)
    try:
        first = await stream.__anext__()
    except (SpeechStreamError, StopAsyncIteration) as exc:
        await _record_tts(turn_id, first_byte_ms=_ms(started), error=str(exc) or "no audio")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Speech synthesis failed: {exc}") from None
    first_byte_ms = _ms(started)

    async def body() -> AsyncIterator[bytes]:
        yield first
        async for chunk in stream:
            yield chunk
        await _record_tts(turn_id, first_byte_ms=first_byte_ms, error=None)

    return body(), AUDIO_MIME[audio_format]


async def _record_tts(turn_id: uuid.UUID, *, first_byte_ms: float, error: str | None) -> None:
    try:
        async with async_session_factory() as db:
            turn = await db.get(VoiceSimulatorTurn, turn_id)
            if turn is None:
                return
            turn.tts_ms = first_byte_ms
            if error:
                turn.errors = [*(turn.errors or []), {"stage": "tts", "type": "SpeechError", "message": error}]
            await db.commit()
    except Exception:
        logger.exception("Could not record TTS timing for turn %s", turn_id)


# --- intent ---------------------------------------------------------------


def _last_output(tr: trace.Trace) -> dict[str, Any]:
    for call in reversed(tr.llm_calls):
        if isinstance(call.output, dict):
            return call.output
    return {}


def derive_intent(
    *,
    state_before: VoiceCallState,
    state_after: VoiceCallState,
    misunderstandings_before: int,
    call: VoiceCallSession,
    escalated_now: bool,
    model_output: dict[str, Any],
) -> str:
    """Name what the caller did this turn, for the Conversation Inspector.

    Derived from the orchestrator's own transition and the model's structured
    output -- a label for humans reading the trace, not a second classifier.
    """
    if escalated_now:
        return {
            EscalationReason.CALLER_REQUESTED: "request_human",
            EscalationReason.REPEATED_MISUNDERSTANDING: "unclear",
            EscalationReason.SYSTEM_ERROR: "system_error",
        }.get(call.escalation_reason, "escalated")
    if state_before in TERMINAL_STATES:
        return "after_hangup"
    if call.collected.get("escalation_pending"):
        return "request_human"  # asked for a person; agent is getting a callback number
    if call.misunderstanding_count > misunderstandings_before:
        return "unclear"

    answer = model_output.get("answer")
    if state_before == VoiceCallState.COLLECT_DESCRIPTION:
        return "report_issue"
    if state_before == VoiceCallState.COLLECT_DETAILS:
        return "provide_details"
    if state_before == VoiceCallState.CONFIRM_NAME:
        # A plain yes confirms; anything else is a spelling or a correction.
        return "confirm_name" if answer is True else "spell_name"
    if state_before == VoiceCallState.COLLECT_NAME:
        return "provide_name"
    if state_before == VoiceCallState.COLLECT_PHONE:
        return "provide_phone"
    if state_before == VoiceCallState.COLLECT_EMAIL:
        if model_output.get("declined"):
            return "decline_email"
        return "provide_email" if state_after == VoiceCallState.CONFIRM_EMAIL else "unclear_email"
    if state_before in (VoiceCallState.CONFIRM_EMAIL, VoiceCallState.CONFIRM_CATEGORY):
        return "confirm" if answer is True else "deny" if answer is False else "unclear"
    if state_before == VoiceCallState.ANYTHING_ELSE:
        return "another_issue" if state_after == VoiceCallState.COLLECT_DESCRIPTION else "finish"
    return "unknown"


# --- background work ------------------------------------------------------


async def _dispatch_ticket_tasks(
    db: AsyncSession, background_tasks: BackgroundTasks, ticket_id: uuid.UUID, *, send_email: bool
) -> None:
    """Same post-creation work as voice/routes.py, with email opt-in (design D11)."""
    ticket = await ticket_service.get_ticket(db, ticket_id)
    if send_email:
        background_tasks.add_task(send_ticket_notification, ticket, "created")
    background_tasks.add_task(generate_summary_for_ticket, ticket.id)


# --- lifecycle ------------------------------------------------------------


async def sweep_idle_sessions(db: AsyncSession) -> int:
    """End simulated sessions nobody has touched within the idle timeout.

    Runs lazily on /start (there is no job runner). Idle sessions are closed
    without the abandoned-call salvage: a tester who walked away is not a
    caller whose problem needs a ticket.
    """
    cutoff = _now() - timedelta(seconds=settings.simulator_session_idle_timeout_seconds)
    idle_ids = (
        await db.execute(
            select(VoiceSimulatorSession.session_id)
            .join(VoiceCallSession, VoiceCallSession.id == VoiceSimulatorSession.session_id)
            .where(VoiceCallSession.ended_at.is_(None), VoiceSimulatorSession.last_activity_at < cutoff)
        )
    ).scalars().all()
    if not idle_ids:
        return 0

    now = _now()
    await db.execute(
        update(VoiceCallSession)
        .where(VoiceCallSession.id.in_(idle_ids), VoiceCallSession.state.notin_(TERMINAL_STATES))
        .values(state=VoiceCallState.ABANDONED)
    )
    await db.execute(update(VoiceCallSession).where(VoiceCallSession.id.in_(idle_ids)).values(ended_at=now))
    await db.execute(
        update(VoiceSimulatorSession)
        .where(VoiceSimulatorSession.session_id.in_(idle_ids))
        .values(end_reason="idle")
    )
    await db.commit()
    logger.info("Swept %s idle simulator session(s)", len(idle_ids))
    return len(idle_ids)


async def _enforce_capacity(db: AsyncSession) -> None:
    active = (
        await db.execute(
            select(func.count())
            .select_from(VoiceCallSession)
            .where(VoiceCallSession.is_simulated.is_(True), VoiceCallSession.ended_at.is_(None))
        )
    ).scalar_one()
    if active >= settings.simulator_max_concurrent_sessions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{active} simulator sessions are already active "
                f"(limit {settings.simulator_max_concurrent_sessions}). End one and try again."
            ),
        )

    started_last_hour = (
        await db.execute(
            select(func.count())
            .select_from(VoiceSimulatorSession)
            .where(VoiceSimulatorSession.created_at >= _now() - timedelta(hours=1))
        )
    ).scalar_one()
    if started_last_hour >= settings.simulator_max_sessions_per_hour:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Simulator limit of {settings.simulator_max_sessions_per_hour} sessions per hour reached.",
        )


async def _warm_providers() -> None:
    """While the greeting plays, open the model and speech connections so the
    caller's first answer doesn't also pay for connection setup."""
    for provider in (get_provider(), get_speech_provider()):
        warm = getattr(provider, "warm", None)
        if warm is not None:
            await warm()


_background: set[asyncio.Task] = set()


async def start_session(db: AsyncSession, request: schemas.StartRequest) -> schemas.StartResponse:
    started = time.perf_counter()
    task = asyncio.create_task(_warm_providers())
    _background.add(task)  # keep a reference until done
    task.add_done_callback(_background.discard)
    if request.send_notifications and not settings.simulator_allow_notifications:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ticket emails from the simulator are disabled (SIMULATOR_ALLOW_NOTIFICATIONS=false).",
        )
    await sweep_idle_sessions(db)
    await _enforce_capacity(db)

    caller_id = (request.caller_id or "").strip()
    usable = caller_id_is_usable(caller_id)
    call = VoiceCallSession(
        call_id=f"SIM-{uuid.uuid4().hex}",
        from_number=caller_id if usable else SIMULATOR_FROM,
        to_number=SIMULATOR_TO,
        state=VoiceCallState.GREETING,
        # Same seeding as voice/session.get_or_create_session for caller ID.
        collected={"phone_number": caller_id} if usable else {},
        turns=[],
        is_simulated=True,
    )
    db.add(call)
    await db.flush()
    options = VoiceSimulatorSession(
        session_id=call.id,
        label=request.label,
        tts_enabled=request.tts,
        send_notifications=request.send_notifications,
    )
    db.add(options)

    llm_started = time.perf_counter()
    outcome = await orchestrator.start_call(call)
    llm_ms = _ms(llm_started)
    text = outcome.text

    turn = VoiceSimulatorTurn(
        session_id=call.id,
        turn_client_id=uuid.uuid4(),
        turn_index=0,
        status="completed",
        input_mode="system",
        state_before=VoiceCallState.GREETING,
        state_after=call.state,
        intent="greeting",
        agent_text=text,
        call_ended=False,
        collected_after=dict(call.collected),
        llm_trace=[],
        errors=[],
        llm_ms=llm_ms,
    )
    db.add(turn)
    turn.server_total_ms = _ms(started)
    await db.commit()
    await db.refresh(call)
    await db.refresh(turn)

    logger.info("simulator session=%s started tts=%s caller_id=%s", call.id, request.tts, usable)
    return schemas.StartResponse(
        session=_session_out(call, options, None),
        greeting=_reply(text, call_ended=False, turn_id=turn.id, tts=request.tts),
        turn=turn_out(turn),
    )


async def _turn_by_client_id(db: AsyncSession, turn_client_id: uuid.UUID) -> VoiceSimulatorTurn | None:
    stmt = select(VoiceSimulatorTurn).where(VoiceSimulatorTurn.turn_client_id == turn_client_id)
    return (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()


async def _next_turn_index(db: AsyncSession, session_id: uuid.UUID) -> int:
    stmt = select(func.coalesce(func.max(VoiceSimulatorTurn.turn_index), -1)).where(
        VoiceSimulatorTurn.session_id == session_id
    )
    return (await db.execute(stmt)).scalar_one() + 1


def _check_open(call: VoiceCallSession) -> None:
    if call.ended_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This simulated call has ended")


async def transcribe(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    turn_client_id: uuid.UUID,
    audio: bytes,
    mime_type: str,
) -> schemas.AudioResponse:
    """Speech-to-text for one caller utterance. The audio is discarded after this call."""
    started = time.perf_counter()

    if len(audio) > settings.simulator_max_audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Audio exceeds {settings.simulator_max_audio_bytes} bytes",
        )
    if not audio:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Audio is empty")
    if base_mime_type(mime_type) not in SUPPORTED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported audio type {base_mime_type(mime_type)!r}",
        )
    provider = get_speech_provider()
    if not provider.is_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Speech-to-text is not configured (OPENAI_API_KEY is not set). Use text input instead.",
        )

    call, options = await load_session(db, session_id)
    _check_open(call)

    existing = await _turn_by_client_id(db, turn_client_id)
    if existing is not None:
        if existing.session_id != call.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="turn_client_id belongs to another session")
        return _audio_response(existing, started)
    # Tell the recognizer what this answer probably looks like (an email, ten
    # digits, yes/no, spelled letters) and the HFMG vocabulary -- see
    # app/speech/context.py. Spelling turns use a model that writes letters
    # literally. Read before the rollback below, which expires the session.
    mode = speech_context.recognition_mode(call.state.value, call.collected)
    prompt = speech_context.transcription_prompt(mode) if settings.speech_stt_context else None
    stt_model = settings.speech_stt_spelling_model if mode and mode.startswith("SPELL_") else None

    # End the read transaction so no pooled connection is held during STT,
    # and transcribe before taking the session lock: the provider call is the
    # slow part and needs no database state.
    await db.rollback()
    stt_started = time.perf_counter()
    result = await provider.transcribe(
        audio=audio, mime_type=mime_type, language=settings.voice_language, prompt=prompt, model=stt_model
    )
    stt_ms = _ms(stt_started)
    del audio  # never stored; see VOICE_SIMULATOR_DESIGN.md §4.4

    call, options = await load_session(db, session_id, lock=True)
    _check_open(call)
    existing = await _turn_by_client_id(db, turn_client_id)
    if existing is not None:  # a concurrent retry won the race
        return _audio_response(existing, started)

    transcription = result.transcription
    errors = [] if transcription else [{"stage": "stt", "type": "SpeechError", "message": result.error or "no result"}]
    turn = VoiceSimulatorTurn(
        session_id=call.id,
        turn_client_id=turn_client_id,
        turn_index=await _next_turn_index(db, call.id),
        status="transcribed",
        input_mode="voice",
        utterance=transcription.text if transcription else "",
        stt_raw=transcription.raw if transcription else {"error": result.error},
        stt_confidence=transcription.confidence if transcription else None,
        call_ended=False,
        llm_trace=[],
        errors=errors,
        stt_ms=stt_ms,
    )
    db.add(turn)
    options.last_activity_at = _now()
    await db.commit()
    await db.refresh(turn)
    return _audio_response(turn, started)


def _audio_response(turn: VoiceSimulatorTurn, started: float) -> schemas.AudioResponse:
    stt_error = next((e["message"] for e in (turn.errors or []) if e.get("stage") == "stt"), None)
    transcript = turn.utterance or ""
    return schemas.AudioResponse(
        turn_client_id=turn.turn_client_id,
        transcript=transcript,
        confidence=turn.stt_confidence,
        empty=not transcript.strip(),
        error=stt_error,
        raw=turn.stt_raw,
        timings=schemas.AudioTimings(stt_ms=turn.stt_ms, server_ms=_ms(started)),
    )


async def process_turn(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    request: schemas.ProcessRequest,
) -> schemas.ProcessResponse:
    """Run one caller utterance through orchestrator.handle_turn, then TTS."""
    started = time.perf_counter()

    lock_started = time.perf_counter()
    call, options = await load_session(db, request.session_id, lock=True)
    queue_wait_ms = _ms(lock_started)

    turn = await _turn_by_client_id(db, request.turn_client_id)
    if turn is not None and turn.session_id != call.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="turn_client_id belongs to another session")
    if turn is not None and turn.status == "completed":
        # Idempotent retry (client timeout, double submit): return what
        # already happened. The orchestrator's own replay guard only covers
        # ticket creation, so a re-run could double-count misunderstandings.
        ticket = await _ticket(db, call.ticket_id)
        return schemas.ProcessResponse(
            turn=turn_out(turn),
            reply=_reply(turn.agent_text or "", call_ended=turn.call_ended, turn_id=turn.id, tts=options.tts_enabled),
            session=_session_out(call, options, ticket),
        )
    if turn is not None and turn.status == "processing":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This turn is already being processed")

    _check_open(call)
    completed_turns = (
        await db.execute(
            select(func.count())
            .select_from(VoiceSimulatorTurn)
            .where(VoiceSimulatorTurn.session_id == call.id, VoiceSimulatorTurn.status == "completed")
        )
    ).scalar_one()
    if completed_turns >= settings.simulator_max_turns_per_session:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Session reached the {settings.simulator_max_turns_per_session}-turn limit",
        )

    if turn is None:
        turn = VoiceSimulatorTurn(
            session_id=call.id,
            turn_client_id=request.turn_client_id,
            turn_index=await _next_turn_index(db, call.id),
            input_mode=request.input_mode,
            call_ended=False,
            llm_trace=[],
            errors=[],
        )
        db.add(turn)

    # A typed utterance overrides a stored transcript, so a tester can fix a
    # mis-transcription before it reaches the agent.
    utterance = request.utterance if request.utterance is not None else (turn.utterance or "")
    utterance = utterance.strip()
    turn.utterance = utterance
    turn.status = "processing"
    turn.queue_wait_ms = queue_wait_ms
    if request.utterance is not None and turn.input_mode != "voice":
        turn.input_mode = request.input_mode

    state_before = call.state
    misunderstandings_before = call.misunderstanding_count
    escalated_before = call.escalated
    ticket_before = call.ticket_id
    errors = list(turn.errors or [])

    async def reset_after_failure(exc: Exception, stage_note: str):
        """Record the failure, roll back, and reload a clean session and turn.

        Rolling back matters: the failure may have been a database error that
        left the transaction unusable. That also discards this turn's caller
        line, so it is recorded again.
        """
        nonlocal call, options, turn
        logger.exception("simulator session=%s %s", call.id, stage_note)
        errors.append({"stage": "agent", "type": type(exc).__name__, "message": str(exc)[:500]})
        await db.rollback()
        call, options = await load_session(db, request.session_id, lock=True)
        turn = await _turn_by_client_id(db, request.turn_client_id) or turn
        if turn not in db:
            db.add(turn)
        turn.utterance = utterance
        turn.queue_wait_ms = queue_wait_ms
        record_turn(call, role="caller", text=utterance, confidence=turn.stt_confidence)

    fatal = False
    with trace.collect() as tr:
        llm_started = time.perf_counter()
        try:
            outcome = await orchestrator.handle_turn(
                db, call, utterance=utterance, confidence=turn.stt_confidence
            )
        except Exception as exc:
            # Mirrors voice/routes.py gather(): a crashed turn escalates, so
            # the tester hears exactly what a real caller would.
            await reset_after_failure(exc, "turn failed; escalating")
            try:
                outcome = await orchestrator.escalate(db, call, EscalationReason.SYSTEM_ERROR)
            except Exception as exc2:
                # Escalation creates a ticket too, so whatever broke the turn
                # (e.g. no categories seeded) often breaks this as well. The
                # phone path then says the system-error line and hangs up;
                # do the same rather than fail the request.
                await reset_after_failure(exc2, "escalation also failed; ending call")
                if call.state not in TERMINAL_STATES:
                    call.state = VoiceCallState.ABANDONED
                outcome = orchestrator.TurnOutcome(reply.say_and_hangup(scripts.SYSTEM_ERROR))
                record_turn(call, role="agent", text=scripts.SYSTEM_ERROR)
                fatal = True
        llm_ms = _ms(llm_started)

    for call_trace in tr.llm_calls:
        if call_trace.error:
            errors.append(
                {"stage": "llm", "type": call_trace.schema_name or call_trace.kind, "message": call_trace.error}
            )

    agent_text = outcome.text
    call_ended = outcome.hangup
    ticket_span = tr.span_named("ticket_create")

    turn.state_before = state_before
    turn.state_after = call.state
    turn.intent = "system_error" if fatal else derive_intent(
        state_before=state_before,
        state_after=call.state,
        misunderstandings_before=misunderstandings_before,
        call=call,
        escalated_now=call.escalated and not escalated_before,
        model_output=_last_output(tr),
    )
    turn.agent_text = agent_text
    turn.call_ended = call_ended
    turn.collected_after = dict(call.collected or {})
    turn.ticket_payload = ticket_span.data if ticket_span else None
    turn.ticket_create_ms = ticket_span.duration_ms if ticket_span else None
    turn.llm_trace = tr.llm_calls_json()
    turn.llm_ms = llm_ms
    turn.errors = errors
    turn.status = "completed"

    options.last_activity_at = _now()
    if call_ended:
        call.ended_at = _now()
        options.end_reason = "error" if fatal else "agent_hangup"
    turn.server_total_ms = _ms(started)
    await db.commit()
    await db.refresh(turn)

    created_ticket_id = call.ticket_id if call.ticket_id != ticket_before else None
    if created_ticket_id is not None:
        await _dispatch_ticket_tasks(db, background_tasks, created_ticket_id, send_email=options.send_notifications)

    logger.info(
        "simulator session=%s turn=%s %s->%s intent=%s llm_ms=%.0f tts_ms=%s stt_ms=%s errors=%s",
        call.id,
        turn.turn_index,
        state_before.value,
        call.state.value,
        turn.intent,
        llm_ms,
        f"{turn.tts_ms:.0f}" if turn.tts_ms is not None else "-",
        f"{turn.stt_ms:.0f}" if turn.stt_ms is not None else "-",
        len(errors),
    )

    ticket = await _ticket(db, call.ticket_id)
    return schemas.ProcessResponse(
        turn=turn_out(turn),
        reply=_reply(agent_text, call_ended=call_ended, turn_id=turn.id, tts=options.tts_enabled),
        session=_session_out(call, options, ticket),
    )


async def end_session(
    db: AsyncSession, background_tasks: BackgroundTasks, request: schemas.EndRequest
) -> schemas.SimulatorSession:
    """Hang up. Mirrors voice/routes.py call_status, including abandoned-call salvage."""
    call, options = await load_session(db, request.session_id, lock=True)
    if call.ended_at is not None and call.state in TERMINAL_STATES:
        return _session_out(call, options, await _ticket(db, call.ticket_id))

    call.ended_at = call.ended_at or _now()
    options.end_reason = options.end_reason or request.reason
    salvaged: uuid.UUID | None = None

    if call.state not in TERMINAL_STATES and call.ticket_id is None:
        call.state = VoiceCallState.ABANDONED
        has_description = bool(call.collected.get("description"))
        has_phone = bool(call.collected.get("phone_number") or caller_id_is_usable(call.from_number))
        # "cleared" means the tester threw the session away -- not a caller
        # who hung up mid-intake -- so it never salvages.
        if has_description and has_phone and request.reason != "cleared":
            ticket = await orchestrator.salvage_abandoned_call(db, call)
            salvaged = ticket.id
    elif call.state not in TERMINAL_STATES:
        call.state = VoiceCallState.ABANDONED

    await db.commit()
    if salvaged is not None:
        await _dispatch_ticket_tasks(db, background_tasks, salvaged, send_email=options.send_notifications)
        logger.info("simulator session=%s salvaged into ticket %s", call.id, salvaged)

    logger.info("simulator session=%s ended reason=%s state=%s", call.id, options.end_reason, call.state.value)
    return _session_out(call, options, await _ticket(db, call.ticket_id))


async def get_detail(db: AsyncSession, session_id: uuid.UUID) -> schemas.SessionDetail:
    call, options = await load_session(db, session_id)
    turns = (
        await db.execute(
            select(VoiceSimulatorTurn)
            .where(VoiceSimulatorTurn.session_id == session_id)
            .order_by(VoiceSimulatorTurn.turn_index)
        )
    ).scalars().all()
    return schemas.SessionDetail(
        session=_session_out(call, options, await _ticket(db, call.ticket_id)),
        turns=[turn_out(t) for t in turns],
    )


async def record_client_metrics(
    db: AsyncSession, session_id: uuid.UUID, request: schemas.ClientMetricsRequest
) -> schemas.TurnTimings:
    await load_session(db, session_id)
    turn = await _turn_by_client_id(db, request.turn_client_id)
    if turn is None or turn.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Turn not found")
    for field in ("utterance_ms", "capture_ms", "playback_start_ms", "playback_duration_ms", "turn_total_ms"):
        value = getattr(request, field)
        if value is not None:
            setattr(turn, field, round(value, 2))
    await db.commit()
    await db.refresh(turn)
    return timings_out(turn)

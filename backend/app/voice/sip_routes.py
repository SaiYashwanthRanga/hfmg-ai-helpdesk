"""SIP voice gateway endpoints.

The SIP gateway (SipVoiceGateway / "Sorcery") owns telephony and speech-to-text;
it posts each caller utterance here and speaks back the returned lines. The
conversation logic lives in orchestrator.py -- this module only adapts its
output to JSON.
"""

import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import EscalationReason, VoiceCallSession, VoiceCallState
from app.services.user_directory import UserDirectoryService, get_user_directory
from app.speech import context as speech_context
from app.voice import orchestrator, scripts, turns
from app.voice.tasks import dispatch_ticket_tasks
from app.voice.session import CallBusy, caller_id_is_usable, get_or_create_session, get_session, record_turn, update_collected

logger = logging.getLogger("hfmg.voice.sip")

settings = get_settings()


async def verify_gateway_token(request: Request) -> None:
    expected = settings.voice_sip_gateway_token
    if not expected:
        logger.error("VOICE_SIP_GATEWAY_TOKEN is not set; rejecting SIP gateway request")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="SIP gateway not configured")

    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid gateway token")


router = APIRouter(
    prefix="/voice/sip", tags=["sip-voice"], dependencies=[Depends(verify_gateway_token)]
)


class StartCallRequest(BaseModel):
    call_id: str
    from_number: str = ""
    to_number: str = ""


class TurnRequest(BaseModel):
    call_id: str
    utterance: str = ""
    confidence: float | None = None
    # One stable ID per logical turn, reused on every retry of it (docs/TURN_ID_CONTRACT.md).
    # Absent from a gateway that predates it; such a request is processed every time it arrives.
    turn_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:-]{1,64}$")


class StatusRequest(BaseModel):
    call_id: str
    reason: str = ""


class TurnResponse(BaseModel):
    lines: list[str]
    expect_reply: bool
    ticket_id: str | None = None
    # Transcription prompt for the caller's next answer (app/speech/context.py).
    # Null when no reply is expected or the state has no specific hint.
    stt_prompt: str | None = None


def _stt_prompt(session: VoiceCallSession, expect_reply: bool) -> str | None:
    if not expect_reply or not settings.speech_stt_context:
        return None
    return speech_context.stt_prompt(session.state.value, session.collected)


def _to_response(outcome: orchestrator.TurnOutcome, session: VoiceCallSession) -> TurnResponse:
    return TurnResponse(
        lines=outcome.lines,
        expect_reply=outcome.expect_reply,
        ticket_id=str(outcome.ticket_id) if outcome.ticket_id else None,
        stt_prompt=_stt_prompt(session, outcome.expect_reply),
    )


class TurnReply(TurnResponse):
    """A /turn response. `turn_id` echoes the request's, and is present only when the request
    had one, so a gateway that does not send turn_ids gets exactly the body it always did
    (the route serializes with exclude_unset)."""

    turn_id: str | None = None


def _reply(response: TurnResponse, turn_id: str | None) -> TurnReply:
    fields = response.model_dump()
    return TurnReply(**fields, turn_id=turn_id) if turn_id else TurnReply(**fields)


def _error_response(turn_id: str | None = None) -> TurnReply:
    return _reply(TurnResponse(lines=[scripts.SYSTEM_ERROR], expect_reply=False, ticket_id=None, stt_prompt=None), turn_id)


@dataclass
class _Finished:
    """How a /turn ended: the reply, the ticket (if any) whose summary/email to start, and
    what the log line needs."""

    response: TurnReply
    dispatch_ticket_id: uuid.UUID | None
    state: str
    misunderstandings: int


def _lock_wait(body: TurnRequest) -> float | None:
    """Only a request that can be replayed (it has a turn_id) gives up waiting for the call."""
    return settings.voice_turn_lock_wait_seconds if body.turn_id else None


def _new_ticket(outcome_ticket_id: uuid.UUID | None, ticket_before: uuid.UUID | None) -> uuid.UUID | None:
    """The ticket created on this turn, if any. A turn that only reads back an existing ticket
    must not summarize/email it again."""
    return outcome_ticket_id if outcome_ticket_id not in (None, ticket_before) else None


async def _replay(
    db: AsyncSession, http_response: Response, body: TurnRequest, recorded: turns.RecordedTurn
) -> TurnReply:
    """Answer a repeated turn_id from its saved response; nothing is run, saved or dispatched."""
    saved = recorded.response
    same_content = recorded.fingerprint == turns.fingerprint(body.utterance)
    await db.rollback()  # nothing to save; releases the call's lock
    if not same_content:
        logger.warning("sip call=%s turn_id=%s reused with different content; rejected", body.call_id, body.turn_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "turn_payload_conflict",
                "message": "This turn_id was already used for a different utterance on this call.",
            },
        )
    http_response.headers["Idempotent-Replayed"] = "true"
    logger.info("sip call=%s turn_id=%s replayed", body.call_id, body.turn_id)
    return TurnReply(**{**saved, "turn_id": body.turn_id})


async def _escalate_after_failed_turn(
    db: AsyncSession, body: TurnRequest, content: str | None, ticket_before: uuid.UUID | None
) -> _Finished | None:
    """Hand a call whose turn failed to a person, on a transaction that is known to be good.

    The failed turn may have left the transaction aborted (a database error) or holding
    half-done work (a ticket flushed but the call not yet marked as filed). Neither may
    be committed, nor reused: roll back first, which discards the turn's changes and
    releases the call's lock, then lock and reload the call as it was last committed.
    The turn's utterance is re-recorded so the callback ticket's transcript has it.

    With a turn_id, the escalation's reply is saved under it, so a retry replays it. And the
    turn is looked up again first: if the failure was an uncertain commit (it did go
    through), or an identical request finished in the gap, that saved reply is returned
    instead of escalating over it. In the first case this request never started the
    ticket's summary/email, so it does; in the second the other request did, and it may be
    repeated once. That is the only path where a notification can be duplicated.

    Returns None, with nothing committed, if the escalation cannot be completed; the
    caller then gets the plain error line rather than a promise of a callback.
    """
    try:
        await db.rollback()
        session = await get_session(db, body.call_id, lock=True, wait_seconds=_lock_wait(body))
        if session is None:
            return None
        if body.turn_id:
            recorded = await turns.find(db, session.id, body.turn_id)
            if recorded is not None and recorded.fingerprint == content:
                saved = TurnReply(**{**recorded.response, "turn_id": body.turn_id})
                await db.rollback()
                logger.warning(
                    "sip call=%s turn_id=%s was committed despite the error; replaying it", body.call_id, body.turn_id
                )
                created = uuid.UUID(saved.ticket_id) if saved.ticket_id else None
                return _Finished(saved, _new_ticket(created, ticket_before), "replayed", 0)
        record_turn(session, role="caller", text=(body.utterance or "").strip(), confidence=body.confidence)
        outcome = await orchestrator.escalate(db, session, EscalationReason.SYSTEM_ERROR)
        reply = _reply(_to_response(outcome, session), body.turn_id)
        if body.turn_id:
            turns.save(db, session.id, body.turn_id, content, reply.model_dump(exclude_unset=True))
        await db.commit()
        return _Finished(
            reply, _new_ticket(outcome.ticket_id, ticket_before), session.state.value, session.misunderstanding_count
        )
    except Exception:
        logger.exception("Escalation also failed for SIP call %s", body.call_id)
        try:
            await db.rollback()
        except Exception:
            logger.exception("Rollback after failed escalation also failed for SIP call %s", body.call_id)
        return None


@router.post("/start", response_model=TurnResponse)
async def start_call(
    body: StartCallRequest,
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    # The caller ID is stored with the call but not looked up here: who is calling is not known yet, and
    # only an employee call consults the employee directory (once it is routed there, in /turn).
    await get_or_create_session(
        db, call_sid=body.call_id, from_number=body.from_number, to_number=body.to_number
    )
    # Serialize with any other /start or /turn for this call, so the call starts exactly once
    # however many starts arrive together.
    session = await get_session(db, body.call_id, lock=True)
    if session.state != VoiceCallState.GREETING:
        # A retried /start (the gateway lost our first response). Never reset a
        # call that has already begun: replay the greeting if nothing else has
        # happened yet, otherwise just keep listening.
        only_greeted = (
            session.state in (VoiceCallState.COLLECT_DESCRIPTION, VoiceCallState.IDENTIFY_CALLER)
            and len(session.turns) <= 1
        )
        greeting = scripts.GREETING_IDENTIFY if session.state == VoiceCallState.IDENTIFY_CALLER else scripts.GREETING
        return TurnResponse(
            lines=[greeting] if only_greeted else [],
            expect_reply=True,
            stt_prompt=_stt_prompt(session, True),
        )

    try:
        outcome = await orchestrator.start_call(session)
        await db.commit()
    except Exception:
        logger.exception("Failed to start SIP call %s", body.call_id)
        return _error_response()
    return _to_response(outcome, session)


@router.post("/turn", response_model=TurnReply, response_model_exclude_unset=True)
async def turn(
    body: TurnRequest,
    background_tasks: BackgroundTasks,
    http_response: Response,
    db: AsyncSession = Depends(get_db),
    directory: UserDirectoryService = Depends(get_user_directory),
) -> TurnReply:
    try:
        session = await get_session(db, body.call_id, lock=True, wait_seconds=_lock_wait(body))
    except CallBusy:
        await db.rollback()
        logger.warning("sip call=%s turn_id=%s still in progress", body.call_id, body.turn_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "turn_in_progress",
                "message": "An earlier request for this call is still being processed. Retry with the same turn_id.",
            },
            headers={"Retry-After": "1"},
        )
    if session is None:
        logger.warning("Turn for unknown SIP call %s", body.call_id)
        return _error_response(body.turn_id)

    content = turns.fingerprint(body.utterance) if body.turn_id else None
    if body.turn_id:
        recorded = await turns.find(db, session.id, body.turn_id)
        if recorded is not None:
            return await _replay(db, http_response, body, recorded)

    ticket_before = session.ticket_id
    try:
        outcome = await orchestrator.handle_turn(
            db, session, utterance=body.utterance, confidence=body.confidence, directory=directory
        )
        reply = _reply(_to_response(outcome, session), body.turn_id)
        if body.turn_id:
            # Same transaction as the conversation state and any ticket: all of it or none.
            turns.save(db, session.id, body.turn_id, content, reply.model_dump(exclude_unset=True))
        await db.commit()
        done = _Finished(
            reply, _new_ticket(outcome.ticket_id, ticket_before), session.state.value, session.misunderstanding_count
        )
    except Exception:
        logger.exception("Turn failed for SIP call %s; rolling back and escalating", body.call_id)
        recovered = await _escalate_after_failed_turn(db, body, content, ticket_before)
        if recovered is None:
            return _error_response(body.turn_id)
        done = recovered

    # Only for a ticket created on this turn (see _new_ticket); a replay never gets here.
    if done.dispatch_ticket_id is not None:
        await dispatch_ticket_tasks(db, background_tasks, done.dispatch_ticket_id)

    logger.info(
        "sip call=%s turn_id=%s state=%s misunderstandings=%s stt_confidence=%s",
        body.call_id,
        body.turn_id,
        done.state,
        done.misunderstandings,
        body.confidence,
    )
    return done.response


@router.post("/status", status_code=status.HTTP_204_NO_CONTENT)
async def call_status(
    body: StatusRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Finalize the session when the gateway reports the call has ended."""
    session = await get_session(db, body.call_id, lock=True)
    if session is None:
        return

    session.ended_at = datetime.now(timezone.utc)

    terminal = (VoiceCallState.COMPLETED, VoiceCallState.ESCALATED, VoiceCallState.ABANDONED)
    if session.state in terminal or orchestrator.issue_filed(session):
        await db.commit()
        return

    if session.state == VoiceCallState.CONFIRM_SUMMARY:
        # The ticket was read back and the caller hung up without answering: filed as NOT confirmed.
        update_collected(session, summary_unanswered=True)
    session.state = VoiceCallState.ABANDONED

    # Reachable, as in orchestrator._caller_leaving: a number the caller gave or a real caller ID.
    # (session.from_number is "unknown" when none was sent, so it must not be tested for truthiness.)
    has_description = bool(session.collected.get("description"))
    reachable = bool(session.collected.get("phone_number")) or caller_id_is_usable(session.from_number)
    if has_description and reachable:
        ticket = await orchestrator.salvage_abandoned_call(db, session)
        await db.commit()
        await dispatch_ticket_tasks(db, background_tasks, ticket.id)
        logger.info("Salvaged abandoned SIP call %s into ticket %s", body.call_id, ticket.ticket_number)
    else:
        await db.commit()
        logger.info("SIP call %s abandoned (%s) with nothing to salvage", body.call_id, body.reason)

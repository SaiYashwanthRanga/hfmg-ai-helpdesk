"""SIP voice gateway endpoints.

The SIP gateway (SipVoiceGateway / "Sorcery") owns telephony and speech-to-text;
it posts each caller utterance here and speaks back the returned lines. The
conversation logic is the same orchestrator the Twilio webhooks use -- this
module only adapts its TwiML output to plain JSON.
"""

import hmac
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import EscalationReason, VoiceCallState
from app.voice import orchestrator, scripts
from app.voice.routes import _dispatch_ticket_tasks
from app.voice.session import get_or_create_session, get_session

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


class StatusRequest(BaseModel):
    call_id: str
    reason: str = ""


class TurnResponse(BaseModel):
    lines: list[str]
    expect_reply: bool
    ticket_id: str | None = None


def _to_response(outcome: orchestrator.TurnOutcome) -> TurnResponse:
    """Flatten the orchestrator's TwiML into lines to speak + whether to listen."""
    root = ET.fromstring(outcome.twiml)
    lines = [(el.text or "").strip() for el in root.iter("Say")]
    return TurnResponse(
        lines=[line for line in lines if line],
        expect_reply=root.find(".//Gather") is not None,
        ticket_id=str(outcome.ticket_id) if outcome.ticket_id else None,
    )


def _error_response() -> TurnResponse:
    return TurnResponse(lines=[scripts.SYSTEM_ERROR], expect_reply=False)


@router.post("/start", response_model=TurnResponse)
async def start_call(body: StartCallRequest, db: AsyncSession = Depends(get_db)) -> TurnResponse:
    session = await get_or_create_session(
        db, call_sid=body.call_id, from_number=body.from_number, to_number=body.to_number
    )
    try:
        outcome = await orchestrator.start_call(session)
        await db.commit()
    except Exception:
        logger.exception("Failed to start SIP call %s", body.call_id)
        return _error_response()
    return _to_response(outcome)


@router.post("/turn", response_model=TurnResponse)
async def turn(
    body: TurnRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    session = await get_session(db, body.call_id)
    if session is None:
        logger.warning("Turn for unknown SIP call %s", body.call_id)
        return _error_response()

    try:
        outcome = await orchestrator.handle_turn(
            db, session, utterance=body.utterance, confidence=body.confidence
        )
        await db.commit()
    except Exception:
        logger.exception("Turn failed for SIP call %s; escalating", body.call_id)
        try:
            outcome = await orchestrator.escalate(db, session, EscalationReason.SYSTEM_ERROR)
            await db.commit()
        except Exception:
            logger.exception("Escalation also failed for SIP call %s", body.call_id)
            return _error_response()

    if outcome.ticket_id is not None:
        await _dispatch_ticket_tasks(db, background_tasks, outcome.ticket_id)

    logger.info(
        "sip call=%s state=%s misunderstandings=%s stt_confidence=%s",
        body.call_id,
        session.state.value,
        session.misunderstanding_count,
        body.confidence,
    )
    return _to_response(outcome)


@router.post("/status", status_code=status.HTTP_204_NO_CONTENT)
async def call_status(
    body: StatusRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Finalize the session when the gateway reports the call has ended."""
    session = await get_session(db, body.call_id)
    if session is None:
        return

    session.ended_at = datetime.now(timezone.utc)

    terminal = (VoiceCallState.COMPLETED, VoiceCallState.ESCALATED, VoiceCallState.ABANDONED)
    if session.state in terminal or session.ticket_id is not None:
        await db.commit()
        return

    session.state = VoiceCallState.ABANDONED

    has_description = bool(session.collected.get("description"))
    has_phone = bool(session.collected.get("phone_number") or session.from_number)
    if has_description and has_phone:
        ticket = await orchestrator.salvage_abandoned_call(db, session)
        await db.commit()
        await _dispatch_ticket_tasks(db, background_tasks, ticket.id)
        logger.info("Salvaged abandoned SIP call %s into ticket %s", body.call_id, ticket.ticket_number)
    else:
        await db.commit()
        logger.info("SIP call %s abandoned (%s) with nothing to salvage", body.call_id, body.reason)

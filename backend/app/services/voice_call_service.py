"""Read-side queries for the Voice Operations Center (WIREFRAMES.md §5,
BACKEND_GAP_ANALYSIS.md Tier 2).

This module is query/serialization only -- it never mutates a
`VoiceCallSession`. All conversation-state writes stay in
app/voice/orchestrator.py and app/voice/session.py, exactly as before; this
just gives the REST API a read path onto data the voice module already
populates in full.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import VoiceCallSession, VoiceCallState
from app.schemas.voice_call import VoiceCallDetail, VoiceCallListItem, VoiceCallSummary, VoiceCallTurn

# CALL_FLOW.md documents ESCALATED/COMPLETED/ABANDONED as the three terminal
# states; the running model (app/db/models.py) has no separate
# CREATING_TICKET/READ_BACK states (see DOCS_GAP_REPORT.md) -- ticket
# creation and read-back happen inline within the state transition that
# reaches one of these three, not as their own persisted state.
_TERMINAL_STATES = (VoiceCallState.COMPLETED, VoiceCallState.ESCALATED, VoiceCallState.ABANDONED)


def _to_list_item(session: VoiceCallSession) -> VoiceCallListItem:
    collected = session.collected or {}
    return VoiceCallListItem(
        id=session.id,
        twilio_call_sid=session.twilio_call_sid,
        from_number=session.from_number,
        state=session.state,
        escalated=session.escalated,
        escalation_reason=session.escalation_reason,
        ticket_id=session.ticket_id,
        created_at=session.created_at,
        ended_at=session.ended_at,
        caller_name=collected.get("caller_name"),
        category=collected.get("category"),
        priority=collected.get("priority"),
    )


def _to_detail(session: VoiceCallSession) -> VoiceCallDetail:
    base = _to_list_item(session)
    return VoiceCallDetail(
        **base.model_dump(),
        to_number=session.to_number,
        misunderstanding_count=session.misunderstanding_count,
        email_attempt_count=session.email_attempt_count,
        collected=session.collected or {},
        turns=[VoiceCallTurn(**turn) for turn in (session.turns or [])],
        updated_at=session.updated_at,
    )


async def list_voice_calls(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    state: VoiceCallState | None = None,
    escalated: bool | None = None,
) -> tuple[list[VoiceCallListItem], int]:
    filters = []
    if state is not None:
        filters.append(VoiceCallSession.state == state)
    if escalated is not None:
        filters.append(VoiceCallSession.escalated == escalated)

    base_stmt = select(VoiceCallSession)
    count_stmt = select(func.count()).select_from(VoiceCallSession)
    for f in filters:
        base_stmt = base_stmt.where(f)
        count_stmt = count_stmt.where(f)

    total = (await db.execute(count_stmt)).scalar_one()

    stmt = base_stmt.order_by(VoiceCallSession.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    sessions = (await db.execute(stmt)).scalars().all()
    return [_to_list_item(s) for s in sessions], total


async def get_voice_call(db: AsyncSession, call_id: uuid.UUID) -> VoiceCallDetail:
    session = await db.get(VoiceCallSession, call_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voice call not found")
    return _to_detail(session)


async def get_summary(db: AsyncSession) -> VoiceCallSummary:
    state_counts_stmt = select(VoiceCallSession.state, func.count()).group_by(VoiceCallSession.state)
    rows = (await db.execute(state_counts_stmt)).all()
    by_state = {state: count for state, count in rows}

    active = sum(count for state, count in by_state.items() if state not in _TERMINAL_STATES)
    completed = by_state.get(VoiceCallState.COMPLETED, 0)
    escalated = by_state.get(VoiceCallState.ESCALATED, 0)
    abandoned = by_state.get(VoiceCallState.ABANDONED, 0)

    return VoiceCallSummary(active=active, completed=completed, escalated=escalated, abandoned=abandoned)

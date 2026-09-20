import math
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import VoiceCallState
from app.schemas.voice_call import VoiceCallDetail, VoiceCallPage, VoiceCallSummary
from app.services import voice_call_service

router = APIRouter(prefix="/voice-calls", tags=["voice-calls"])


@router.get("", response_model=VoiceCallPage)
async def list_voice_calls(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    state: VoiceCallState | None = None,
    escalated: bool | None = None,
    db: AsyncSession = Depends(get_db),
) -> VoiceCallPage:
    items, total = await voice_call_service.list_voice_calls(
        db, page=page, page_size=page_size, state=state, escalated=escalated
    )
    total_pages = math.ceil(total / page_size) if total else 0
    return VoiceCallPage(items=items, page=page, page_size=page_size, total=total, total_pages=total_pages)


@router.get("/summary", response_model=VoiceCallSummary)
async def voice_call_summary(db: AsyncSession = Depends(get_db)) -> VoiceCallSummary:
    return await voice_call_service.get_summary(db)


@router.get("/{call_id}", response_model=VoiceCallDetail)
async def get_voice_call(call_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> VoiceCallDetail:
    return await voice_call_service.get_voice_call(db, call_id)

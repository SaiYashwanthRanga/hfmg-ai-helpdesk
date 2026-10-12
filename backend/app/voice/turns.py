"""Idempotency records for /turn requests that carry a gateway-supplied turn_id.

See docs/TURN_ID_CONTRACT.md. All of these run on the caller's transaction, under the
call's row lock (session.get_session(lock=True)), so a record is committed together with
the turn's effects or not at all.
"""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import VoiceTurn


@dataclass(frozen=True)
class RecordedTurn:
    fingerprint: str
    response: dict


def fingerprint(utterance: str | None) -> str:
    """What identifies a turn's content. Only the caller's words: the recognizer's
    confidence is metadata and may legitimately differ between sends."""
    return hashlib.sha256((utterance or "").strip().encode("utf-8")).hexdigest()


async def find(db: AsyncSession, session_id: uuid.UUID, turn_id: str) -> RecordedTurn | None:
    stmt = select(VoiceTurn.fingerprint, VoiceTurn.response).where(
        VoiceTurn.session_id == session_id, VoiceTurn.turn_id == turn_id
    )
    row = (await db.execute(stmt)).first()
    return RecordedTurn(fingerprint=row.fingerprint, response=dict(row.response)) if row else None


def save(db: AsyncSession, session_id: uuid.UUID, turn_id: str, fingerprint_: str, response: dict) -> None:
    """Add the record to the open transaction; it commits with the turn."""
    db.add(VoiceTurn(session_id=session_id, turn_id=turn_id, fingerprint=fingerprint_, response=response))

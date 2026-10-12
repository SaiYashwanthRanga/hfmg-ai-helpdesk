"""Load, create, and persist per-call conversation state."""

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import VoiceCallSession, VoiceCallState
from app.services.user_directory import normalize_phone

# Caller ID values the gateway or carrier sends when the number is withheld or unavailable.
_UNUSABLE_CALLER_IDS = {"", "anonymous", "unknown", "restricted", "private", "+266696687"}
_COLUMN_WIDTH = 32  # voice_call_sessions.from_number / to_number


def caller_id_is_usable(from_number: str) -> bool:
    """A caller ID that is a real telephone number: at least ten digits, and not a placeholder
    word or a run of one repeated digit ("0000000000"). A short value such as an extension is
    not a telephone number and is never treated as one."""
    normalized = (from_number or "").strip().lower()
    if normalized in _UNUSABLE_CALLER_IDS:
        return False
    digits = "".join(c for c in normalized if c.isdigit())
    if len(digits) < 10:
        return False
    national = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
    return len(set(national)) > 1


def _fit_caller_id(raw: str | None) -> str:
    """The caller ID as stored: a SIP/tel URI, or anything too long for the column, is reduced to
    the telephone number it carries (or cut to fit); everything else is kept exactly as sent."""
    text_ = (raw or "").strip()
    if len(text_) > _COLUMN_WIDTH or "@" in text_ or text_.lower().startswith(("sip:", "sips:", "tel:")):
        return normalize_phone(text_) or text_[:_COLUMN_WIDTH]
    return text_


class CallBusy(Exception):
    """The call's row lock was not free within the requested wait."""


_LOCK_NOT_AVAILABLE = "55P03"


async def get_session(
    db: AsyncSession, call_sid: str, *, lock: bool = False, wait_seconds: float | None = None
) -> VoiceCallSession | None:
    """`lock` holds a row lock until the transaction ends, so overlapping requests
    for one call (a gateway retry) run one at a time and the later one sees the
    earlier one's committed state.

    `wait_seconds` bounds the wait for that lock (default: wait as long as it takes);
    past it, CallBusy is raised and the transaction must be rolled back. The bound
    applies to this wait only, not to later locks in the same transaction."""
    stmt = select(VoiceCallSession).where(VoiceCallSession.call_id == call_sid)
    if not lock:
        return (await db.execute(stmt)).scalar_one_or_none()

    stmt = stmt.with_for_update().execution_options(populate_existing=True)
    if wait_seconds is None:
        return (await db.execute(stmt)).scalar_one_or_none()
    await db.execute(text("SELECT set_config('lock_timeout', :ms, true)"), {"ms": f"{max(1, int(wait_seconds * 1000))}ms"})
    try:
        session = (await db.execute(stmt)).scalar_one_or_none()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE:
            raise CallBusy(call_sid) from exc
        raise
    await db.execute(text("SELECT set_config('lock_timeout', '0', true)"))
    return session


async def get_or_create_session(
    db: AsyncSession, *, call_sid: str, from_number: str, to_number: str
) -> VoiceCallSession:
    """Idempotent by call_id, so a gateway retry of turn 0 resumes the same call.

    Safe against two simultaneous first requests: the insert yields to an existing row
    (the unique call_id) instead of raising, and the row that won is read back."""
    caller = _fit_caller_id(from_number)
    values = dict(
        call_id=call_sid,
        from_number=caller or "unknown",
        to_number=(to_number or "unknown")[:_COLUMN_WIDTH],
        state=VoiceCallState.GREETING,
        collected={"phone_number": caller} if caller_id_is_usable(caller) else {},
        turns=[],
    )
    await db.execute(pg_insert(VoiceCallSession).values(**values).on_conflict_do_nothing(index_elements=["call_id"]))
    await db.commit()
    session = await get_session(db, call_sid)
    assert session is not None  # inserted above, or already there
    return session


def record_turn(session: VoiceCallSession, *, role: str, text: str, confidence: float | None = None) -> None:
    """Append to the transcript.

    Reassigns the list because SQLAlchemy does not track in-place mutation of
    a JSONB column.
    """
    session.turns = [
        *session.turns,
        {
            "role": role,
            "text": text,
            "confidence": confidence,
            "at": datetime.now(timezone.utc).isoformat(),
        },
    ]


def update_collected(session: VoiceCallSession, **values) -> None:
    """Merge slot values into the collected payload (same JSONB caveat)."""
    session.collected = {**session.collected, **values}


def transcript_text(session: VoiceCallSession) -> str:
    lines = []
    for turn in session.turns:
        speaker = "Agent" if turn.get("role") == "agent" else "Caller"
        lines.append(f"{speaker}: {turn.get('text', '')}")
    return "\n".join(lines)


async def save(db: AsyncSession, session: VoiceCallSession) -> None:
    await db.commit()

"""Dashboard and Analytics aggregation queries (WIREFRAMES.md §2/§7,
BACKEND_GAP_ANALYSIS.md Tiers 1 & 3).

AI Resolution Rate is deliberately not computed: DESIGN.md §20 lists three
non-equivalent candidate definitions and none has been chosen, so this
returns a `blocked` status with a reason instead of a number, rather than
picking one silently (REMAINING_PRODUCT_DECISIONS.md).

`calls_today`/`escalations` and `escalation-rate` below use disclosed,
reasonable-default definitions where WIREFRAMES.md/DESIGN.md named the
metric but didn't fully specify its window -- each is documented inline and
in REMAINING_PRODUCT_DECISIONS.md, not silently assumed.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AISummaryStatus,
    Category,
    Priority,
    Ticket,
    TicketSource,
    TicketStatus,
    VoiceCallSession,
    VoiceCallState,
)

# "Open" = not yet in a terminal state. No single field defines this, so the
# definition lives here, once, rather than being re-guessed per query.
_CLOSED_TICKET_STATUSES = (TicketStatus.RESOLVED, TicketStatus.CLOSED, TicketStatus.CANCELLED)
_TERMINAL_CALL_STATES = (VoiceCallState.COMPLETED, VoiceCallState.ESCALATED, VoiceCallState.ABANDONED)

_AI_RESOLUTION_RATE_BLOCKED_REASON = (
    "AI Resolution Rate has no finalized definition yet -- DESIGN.md §20 lists "
    "three non-equivalent candidate definitions. This is a product decision, "
    "not something to pick silently in a query. See REMAINING_PRODUCT_DECISIONS.md."
)


def _start_of_today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


async def get_kpis(db: AsyncSession) -> dict:
    open_count_stmt = select(func.count()).select_from(Ticket).where(Ticket.status.notin_(_CLOSED_TICKET_STATUSES))
    open_count = (await db.execute(open_count_stmt)).scalar_one()

    start_of_today = _start_of_today()
    today_count_stmt = select(func.count()).select_from(Ticket).where(Ticket.created_at >= start_of_today)
    today_count = (await db.execute(today_count_stmt)).scalar_one()

    # Disclosed assumption (REMAINING_PRODUCT_DECISIONS.md): "Calls Today"
    # and "Escalations" are both day-scoped, matching their KPI-row siblings
    # (Open Tickets excepted, which is a point-in-time count by nature).
    calls_today_stmt = select(func.count()).select_from(VoiceCallSession).where(
        VoiceCallSession.created_at >= start_of_today
    )
    calls_today = (await db.execute(calls_today_stmt)).scalar_one()

    escalations_today_stmt = (
        select(func.count())
        .select_from(VoiceCallSession)
        .where(VoiceCallSession.created_at >= start_of_today, VoiceCallSession.escalated.is_(True))
    )
    escalations_today = (await db.execute(escalations_today_stmt)).scalar_one()

    return {
        "open_tickets": {"status": "ready", "value": open_count, "blocked_reason": None},
        "tickets_today": {"status": "ready", "value": today_count, "blocked_reason": None},
        "calls_today": {"status": "ready", "value": calls_today, "blocked_reason": None},
        "escalations": {"status": "ready", "value": escalations_today, "blocked_reason": None},
        "ai_resolution_rate": {
            "status": "blocked",
            "value": None,
            "blocked_reason": _AI_RESOLUTION_RATE_BLOCKED_REASON,
        },
    }


async def get_recent_activity(db: AsyncSession, *, limit: int) -> list[Ticket]:
    stmt = select(Ticket).options(selectinload(Ticket.category)).order_by(Ticket.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


async def get_tickets_by_category(db: AsyncSession, *, days: int) -> list[dict]:
    stmt = (
        select(Category.name, func.count(Ticket.id))
        .join(Ticket, Ticket.category_id == Category.id)
        .where(Ticket.created_at >= _since(days))
        .group_by(Category.name)
        .order_by(func.count(Ticket.id).desc())
    )
    rows = (await db.execute(stmt)).all()
    return [{"category": name, "count": count} for name, count in rows]


async def get_tickets_by_priority(db: AsyncSession, *, days: int) -> list[dict]:
    stmt = (
        select(Ticket.priority, func.count())
        .where(Ticket.created_at >= _since(days))
        .group_by(Ticket.priority)
    )
    rows = dict((await db.execute(stmt)).all())
    # Zero-fill every priority in a fixed order so a bar chart never silently
    # drops a category that happened to have no tickets in the window.
    return [{"priority": p, "count": rows.get(p, 0)} for p in Priority]


async def get_tickets_by_source(db: AsyncSession, *, days: int) -> list[dict]:
    stmt = (
        select(Ticket.source, func.count())
        .where(Ticket.created_at >= _since(days))
        .group_by(Ticket.source)
    )
    rows = dict((await db.execute(stmt)).all())
    return [{"source": s, "count": rows.get(s, 0)} for s in TicketSource]


async def get_calls_by_day(db: AsyncSession, *, days: int) -> list[dict]:
    since = _since(days)
    day_column = func.date(VoiceCallSession.created_at)
    stmt = (
        select(day_column, func.count())
        .where(VoiceCallSession.created_at >= since)
        .group_by(day_column)
    )
    rows = {d: count for d, count in (await db.execute(stmt)).all()}

    # Zero-fill every day in the window -- a trend chart with gaps for
    # no-call days would misrepresent "zero" as "no data" (DESIGN_SYSTEM.md §17).
    today = date.today()
    items = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        items.append({"date": day, "count": rows.get(day, 0)})
    return items


async def get_escalation_rate(db: AsyncSession, *, days: int) -> dict:
    since = _since(days)
    terminal_stmt = (
        select(func.count())
        .select_from(VoiceCallSession)
        .where(VoiceCallSession.created_at >= since, VoiceCallSession.state.in_(_TERMINAL_CALL_STATES))
    )
    total_terminal = (await db.execute(terminal_stmt)).scalar_one()

    escalated_stmt = (
        select(func.count())
        .select_from(VoiceCallSession)
        .where(VoiceCallSession.created_at >= since, VoiceCallSession.escalated.is_(True))
    )
    escalated = (await db.execute(escalated_stmt)).scalar_one()

    rate = round((escalated / total_terminal) * 100, 1) if total_terminal else 0.0
    return {"total_terminal_calls": total_terminal, "escalated_calls": escalated, "rate_percent": rate}


async def get_ai_summary_usage(db: AsyncSession, *, days: int) -> list[dict]:
    stmt = (
        select(Ticket.ai_summary_status, func.count())
        .where(Ticket.created_at >= _since(days))
        .group_by(Ticket.ai_summary_status)
    )
    rows = dict((await db.execute(stmt)).all())
    total = sum(rows.values())

    items = []
    for value in AISummaryStatus:
        count = rows.get(value, 0)
        percentage = round((count / total) * 100, 1) if total else 0.0
        items.append({"status": value, "count": count, "percentage": percentage})
    return items

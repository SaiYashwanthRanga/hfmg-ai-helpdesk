"""Latency statistics for the AI Call Simulator.

Per-session metrics feed the page's latency panel; the window roll-up feeds
GET /voice-simulator/stats, the observability view (session counts, stage
latencies, failure rate). Volumes are small -- a session has tens of turns,
a day hundreds -- so aggregation happens in Python over plain rows.
"""

import math
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import VoiceCallSession, VoiceSimulatorTurn
from app.simulator import schemas
from app.simulator.service import load_session, timings_out

# Order is the order the latency panel shows them.
STAGES = (
    "capture_ms",
    "stt_ms",
    "llm_ms",
    "tts_ms",
    "playback_start_ms",
    "turn_total_ms",
    "server_total_ms",
    "queue_wait_ms",
    "ticket_create_ms",
)


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for an empty series."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def stage_stats(values: Iterable[float | None]) -> schemas.StageStats:
    """`current` is the last non-null value, i.e. the most recent turn that measured it."""
    series = [v for v in values if v is not None]
    if not series:
        return schemas.StageStats(current=None, avg=None, max=None, p95=None, n=0)
    return schemas.StageStats(
        current=series[-1],
        avg=round(sum(series) / len(series), 2),
        max=max(series),
        p95=percentile(series, 95),
        n=len(series),
    )


def _stats_for(turns: Sequence[VoiceSimulatorTurn]) -> dict[str, schemas.StageStats]:
    return {stage: stage_stats(getattr(t, stage) for t in turns) for stage in STAGES}


async def session_metrics(db: AsyncSession, session_id: uuid.UUID) -> schemas.MetricsResponse:
    await load_session(db, session_id)
    turns = (
        await db.execute(
            select(VoiceSimulatorTurn)
            .where(VoiceSimulatorTurn.session_id == session_id, VoiceSimulatorTurn.status == "completed")
            .order_by(VoiceSimulatorTurn.turn_index)
        )
    ).scalars().all()
    return schemas.MetricsResponse(
        session_id=session_id,
        turns=[timings_out(t) for t in turns],
        stats=_stats_for(turns),
    )


async def window_stats(db: AsyncSession, *, hours: int) -> schemas.SimulatorStatsResponse:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    simulated = VoiceCallSession.is_simulated.is_(True)

    sessions_total = (
        await db.execute(
            select(func.count()).select_from(VoiceCallSession).where(simulated, VoiceCallSession.created_at >= since)
        )
    ).scalar_one()
    sessions_active = (
        await db.execute(
            select(func.count()).select_from(VoiceCallSession).where(simulated, VoiceCallSession.ended_at.is_(None))
        )
    ).scalar_one()
    sessions_escalated = (
        await db.execute(
            select(func.count())
            .select_from(VoiceCallSession)
            .where(simulated, VoiceCallSession.created_at >= since, VoiceCallSession.escalated.is_(True))
        )
    ).scalar_one()
    tickets_created = (
        await db.execute(
            select(func.count())
            .select_from(VoiceCallSession)
            .where(simulated, VoiceCallSession.created_at >= since, VoiceCallSession.ticket_id.is_not(None))
        )
    ).scalar_one()

    turns = (
        await db.execute(
            select(VoiceSimulatorTurn)
            .where(
                VoiceSimulatorTurn.created_at >= since,
                VoiceSimulatorTurn.status == "completed",
                VoiceSimulatorTurn.input_mode != "system",
            )
            .order_by(VoiceSimulatorTurn.created_at)
        )
    ).scalars().all()
    failed = sum(1 for t in turns if t.errors)

    return schemas.SimulatorStatsResponse(
        window_hours=hours,
        sessions_total=sessions_total,
        sessions_active=sessions_active,
        sessions_escalated=sessions_escalated,
        tickets_created=tickets_created,
        turns_total=len(turns),
        turns_failed=failed,
        failure_rate=round(failed / len(turns), 4) if turns else 0.0,
        stages=_stats_for(turns),
        generated_at=datetime.now(timezone.utc),
    )

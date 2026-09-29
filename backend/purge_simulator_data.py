"""Delete AI Call Simulator data older than SIMULATOR_RETENTION_DAYS.

Simulator sessions hold whatever testers said out loud, which may include
real names or patient details despite the on-page warning, so they are not
kept indefinitely (VOICE_SIMULATOR_DESIGN.md §4.4). Audio is never stored;
this removes transcripts, turn traces and SIMULATOR tickets.

Run from backend/ (there is no job runner yet -- schedule with cron or Task
Scheduler, see OPERATIONS_RUNBOOK.md):

    python purge_simulator_data.py            # uses SIMULATOR_RETENTION_DAYS
    python purge_simulator_data.py --days 3
    python purge_simulator_data.py --dry-run
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.db.base import async_session_factory, engine
from app.db.models import Ticket, TicketSource, VoiceCallSession


async def purge(days: int, dry_run: bool) -> tuple[int, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old_sessions = (VoiceCallSession.is_simulated.is_(True), VoiceCallSession.created_at < cutoff)
    old_tickets = (Ticket.source == TicketSource.SIMULATOR, Ticket.created_at < cutoff)

    async with async_session_factory() as db:
        sessions = (await db.execute(select(func.count()).select_from(VoiceCallSession).where(*old_sessions))).scalar_one()
        tickets = (await db.execute(select(func.count()).select_from(Ticket).where(*old_tickets))).scalar_one()
        if not dry_run:
            # Turns and simulator options cascade from the session row.
            await db.execute(delete(VoiceCallSession).where(*old_sessions))
            await db.execute(delete(Ticket).where(*old_tickets))
            await db.commit()
    await engine.dispose()
    return sessions, tickets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=get_settings().simulator_retention_days)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.days < 0:
        parser.error("--days must be zero or more")

    sessions, tickets = asyncio.run(purge(args.days, args.dry_run))
    verb = "Would delete" if args.dry_run else "Deleted"
    print(f"{verb} {sessions} simulator session(s) and {tickets} SIMULATOR ticket(s) older than {args.days} day(s).")


if __name__ == "__main__":
    main()

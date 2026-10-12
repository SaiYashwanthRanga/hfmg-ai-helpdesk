"""Ticket numbers are allocated by the database, so concurrent creators never collide."""

import asyncio
import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text

from app.db.base import async_session_factory
from app.db.models import Ticket
from app.schemas.ticket import TicketCreate
from app.services import ticket_service

pytestmark = pytest.mark.asyncio(loop_scope="session")

NUMBER_FORMAT = re.compile(r"^HFMG-\d{4}-\d{6}$")


def _payload(category_id, n: int = 0) -> TicketCreate:
    return TicketCreate(
        caller_name=f"Test Caller {n}",
        phone_number="+18455550142",
        category_id=category_id,
        description=f"Synthetic issue {n}",
    )


async def _create(category_id, n: int = 0, **kwargs) -> str:
    """One ticket from its own session, the way each request gets its own."""
    async with async_session_factory() as db:
        ticket = await ticket_service.create_ticket(db, _payload(category_id, n), **kwargs)
        return ticket.ticket_number


def _pin_year(monkeypatch, year: int) -> None:
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(year, 6, 1, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(ticket_service, "datetime", _Clock)


async def test_numbers_keep_the_existing_format_and_count_up(category_id):
    numbers = [await _create(category_id, n) for n in range(3)]

    year = datetime.now(timezone.utc).year
    assert numbers == [f"HFMG-{year}-{n:06d}" for n in (1, 2, 3)]
    assert all(NUMBER_FORMAT.match(number) for number in numbers)


async def test_concurrent_creation_from_separate_sessions_never_collides(category_id):
    """Different callers' requests overlapping: the case the old count-based number lost."""
    total = 25

    results = await asyncio.gather(*(_create(category_id, n) for n in range(total)), return_exceptions=True)

    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, f"{len(errors)} of {total} creations failed, e.g. {errors[0]!r}"
    assert len(set(results)) == total
    year = datetime.now(timezone.utc).year
    assert sorted(results) == [f"HFMG-{year}-{n:06d}" for n in range(1, total + 1)]


async def test_a_deleted_ticket_does_not_free_its_number_for_reuse(category_id):
    first = await _create(category_id, 1)
    second = await _create(category_id, 2)
    async with async_session_factory() as db:
        await db.execute(text("DELETE FROM tickets WHERE ticket_number = :n"), {"n": first})
        await db.commit()

    third = await _create(category_id, 3)

    assert second != third and third not in (first, second)
    assert third.endswith("000003")


async def test_a_new_year_restarts_at_one_and_the_old_year_continues(category_id, monkeypatch):
    _pin_year(monkeypatch, 2031)
    assert [await _create(category_id, n) for n in range(2)] == ["HFMG-2031-000001", "HFMG-2031-000002"]

    _pin_year(monkeypatch, 2032)
    assert await _create(category_id, 3) == "HFMG-2032-000001"

    _pin_year(monkeypatch, 2031)
    assert await _create(category_id, 4) == "HFMG-2031-000003"


async def test_a_rolled_back_creation_does_not_consume_a_number(category_id):
    """The number is allocated inside the ticket's own transaction, so a failed turn leaves no gap."""
    async with async_session_factory() as db:
        await ticket_service.create_ticket(db, _payload(category_id, 1), commit=False)
        await db.rollback()

    assert (await _create(category_id, 2)).endswith("000001")
    async with async_session_factory() as db:
        assert (await db.execute(select(func.count()).select_from(Ticket))).scalar_one() == 1


async def test_simulator_tickets_use_their_own_numbering_and_leave_the_counter_alone(category_id):
    from app.db.models import TicketSource

    simulated = await _create(category_id, 1, source=TicketSource.SIMULATOR)
    real = await _create(category_id, 2)

    assert simulated.startswith("SIM-")
    assert real.endswith("000001")

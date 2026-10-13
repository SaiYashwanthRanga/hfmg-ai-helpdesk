"""The voice call states the code uses can be stored by a database built from the migrations.

The rest of the suite builds its schema with `Base.metadata.create_all`, which always matches the
model, so it cannot notice a database whose `voice_call_state_enum` lags the code (a database not yet
migrated, or a migration that forgot a state). That produced
`invalid input value for enum voice_call_state_enum: "CONFIRM_CALLBACK_NUMBER"` and an HTTP 500 from
the simulator. These tests build a throwaway database with `alembic upgrade head` only and check it
against `VoiceCallState`.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.db.models import VoiceCallSession, VoiceCallState

pytestmark = pytest.mark.asyncio(loop_scope="session")  # the suite's shared loop (see conftest.py)

BACKEND_DIR = Path(__file__).resolve().parents[1]
MIGRATED_DB = "hfmg_alembic_check_test"  # the suite's guard: only *_test databases are ever dropped


async def _recreate_database(url, create: bool) -> None:
    """Drop `url`'s database and, when `create` is true, create it empty."""
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    conn = await asyncpg.connect(admin)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        if create:
            await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


@asynccontextmanager
async def _migrated_database():
    """A database that has only ever seen `alembic upgrade head`; dropped again afterwards."""
    url = make_url(get_settings().database_url).set(database=MIGRATED_DB)
    assert url.database.endswith("_test")
    await _recreate_database(url, create=True)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            env={**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)},
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, f"alembic upgrade head failed: {result.stderr[-2000:]}"
        yield url
    finally:
        await _recreate_database(url, create=False)


@asynccontextmanager
async def _session_on(url):
    # Opened and closed inside the test, so the connection never outlives the test's event loop.
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def test_migrated_enum_holds_exactly_the_states_the_model_defines():
    async with _migrated_database() as url, _session_on(url) as db:
        rows = await db.execute(
            text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'voice_call_state_enum'"
            )
        )
        in_database = {label for (label,) in rows}
    in_code = {state.value for state in VoiceCallState}
    assert in_code - in_database == set(), "states the code can assign but the migrations never created"
    assert in_database - in_code == set(), "states the migrations created but the code no longer defines"
    assert "CONFIRM_CALLBACK_NUMBER" in in_database


async def test_callback_confirmation_state_is_persisted_and_retrieved():
    """The failing path: a session already in the database moves to CONFIRM_CALLBACK_NUMBER."""
    call_id = f"cb-state-{uuid.uuid4().hex[:8]}"
    async with _migrated_database() as url:
        async with _session_on(url) as db:
            db.add(VoiceCallSession(call_id=call_id, from_number="+18455550100", to_number="+18455550199"))
            await db.commit()

            call = (await db.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == call_id))).scalar_one()
            call.state = VoiceCallState.CONFIRM_CALLBACK_NUMBER
            await db.commit()  # raised InvalidTextRepresentationError on a database behind the code

        async with _session_on(url) as db:  # a fresh connection, so this is what was stored
            stored = (await db.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == call_id))).scalar_one()
            raw = (await db.execute(text("SELECT state::text FROM voice_call_sessions WHERE call_id = :c"), {"c": call_id})).scalar_one()
    assert stored.state is VoiceCallState.CONFIRM_CALLBACK_NUMBER
    assert raw == "CONFIRM_CALLBACK_NUMBER"


async def test_every_call_state_can_be_stored_and_read_back():
    expected = {}
    async with _migrated_database() as url:
        async with _session_on(url) as db:
            for state in VoiceCallState:
                call_id = f"all-{state.value.lower()}-{uuid.uuid4().hex[:6]}"[:64]
                expected[call_id] = state
                db.add(VoiceCallSession(call_id=call_id, from_number="+18455550100", to_number="+18455550199", state=state))
            await db.commit()

        async with _session_on(url) as db:
            rows = (await db.execute(select(VoiceCallSession).where(VoiceCallSession.call_id.in_(expected)))).scalars().all()
            stored = {row.call_id: row.state for row in rows}
    assert stored == expected

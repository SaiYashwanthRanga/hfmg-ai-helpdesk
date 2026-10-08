import os
import uuid
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url


def _test_database_url() -> str:
    """The database the suite may destroy -- never the development database.

    The fixtures below drop, recreate and truncate every table. Before this
    guard they ran against DATABASE_URL from backend/.env, which is the
    developer's working database, and wiped it. Now the suite uses
    TEST_DATABASE_URL if set, else the .env database's name plus "_test"
    (created on first run), and refuses any database whose name does not end
    in "_test".
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        url = make_url(explicit)
    else:
        env_file = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
        base = os.environ.get("DATABASE_URL") or env_file.get("DATABASE_URL")
        if not base:
            raise pytest.UsageError("Set TEST_DATABASE_URL (or DATABASE_URL in backend/.env) to run the tests.")
        url = make_url(base)
        if not (url.database or "").endswith("_test"):
            url = url.set(database=f"{url.database}_test")
    if not (url.database or "").endswith("_test"):
        raise pytest.UsageError(
            f"Refusing to run tests against database {url.database!r}: the suite destroys its data. "
            "Point TEST_DATABASE_URL at a database whose name ends in '_test'."
        )
    return url.render_as_string(hide_password=False)


# Must happen before any `app` import: settings and the engine read it at import time.
os.environ["DATABASE_URL"] = _test_database_url()

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.base import Base, async_session_factory, engine  # noqa: E402
from app.main import app  # noqa: E402

settings = get_settings()


async def _ensure_test_database_exists() -> None:
    import asyncpg

    url = make_url(settings.database_url)
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    conn = await asyncpg.connect(admin)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
async def _create_schema():
    """Create the schema once for the whole test session, in the *_test
    database chosen above (never the development database)."""
    assert (make_url(settings.database_url).database or "").endswith("_test")
    await _ensure_test_database_exists()
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS citext")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def _hermetic_ai_summary_defaults(monkeypatch):
    """Force ENABLE_AI_SUMMARY off and clear the OpenAI key for every test by
    default, regardless of what this machine's local backend/.env has set.

    Without this, a developer .env with ENABLE_AI_SUMMARY=true and a real
    OPENAI_API_KEY (as backend/.env documents for local dev) makes
    `create_ticket`'s background task place real, ~10-20s calls to
    api.openai.com during the test suite -- burning real API credits, making
    the suite dramatically slower, and breaking tests that assert the
    documented default (AISummaryStatus.DISABLED). Tests that exercise the
    AI-summary path explicitly re-enable it via their own monkeypatch calls
    and always mock the provider (see test_summarizer.py, test_tickets_api.py's
    regenerate-summary tests) -- they are unaffected by this default.
    """
    monkeypatch.setattr(settings, "enable_ai_summary", False)
    monkeypatch.setattr(settings, "openai_api_key", "")
    yield


@pytest.fixture(autouse=True)
def _voice_flow_defaults(monkeypatch):
    """Scripted conversation tests predate the name, summary and callback-number
    read-backs; they test other behaviour and keep their original question order.
    The read-backs have their own tests (test_voice_conversation.py,
    test_voice_spelling.py, test_callback_confirmation.py), which switch them back on."""
    monkeypatch.setattr(settings, "voice_confirm_name", False)
    monkeypatch.setattr(settings, "voice_confirm_summary", False)
    monkeypatch.setattr(settings, "voice_confirm_callback", False)
    yield


@pytest.fixture(autouse=True)
def _default_caller_classification(monkeypatch):
    """Stub caller type classification to INTERNAL_IT by default.

    The orchestrator now classifies every caller after their first description.
    Existing tests predate this step and would fail without a stub (the real
    function calls the LLM). Tests that exercise classification override this."""
    from app.voice import nlu as _nlu

    async def _it(*_args, **_kwargs):
        return _nlu.CallerClassification("INTERNAL_IT", 0.95)

    monkeypatch.setattr(_nlu, "classify_caller_type", _it)
    yield


@pytest.fixture(autouse=True)
def _hermetic_email_defaults(monkeypatch):
    """Default every test to the SendGrid provider with the internal mail API
    unconfigured, whatever this machine's backend/.env says.

    Otherwise a developer .env with EMAIL_PROVIDER=hfmg_internal and ORG_BASE
    set would make ticket-creation tests send real emails through the
    organization's mail API. Tests for the internal provider opt in explicitly.
    """
    from app.notifications.factory import get_email_provider

    monkeypatch.setattr(settings, "email_provider", "sendgrid")
    monkeypatch.setattr(settings, "org_base", "")
    monkeypatch.setattr(settings, "default_from_email", "")
    get_email_provider.cache_clear()
    yield
    get_email_provider.cache_clear()


@pytest.fixture(autouse=True)
async def _truncate_tables():
    """Empty every table between tests.

    Truncating (rather than dropping/recreating the schema per test) keeps
    the Postgres enum types stable across tests -- asyncpg caches type OIDs
    per connection, and repeatedly dropping/recreating an enum type with the
    same name invalidates that cache mid-suite ("cache lookup failed for
    type ...") once connections are reused from the pool.
    """
    yield
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE TABLE voice_simulator_turns, voice_simulator_sessions, voice_call_sessions, "
            "tickets, categories RESTART IDENTITY CASCADE"
        )


@pytest.fixture
async def db_session():
    """A session for tests that drive the service/orchestrator layer directly."""
    async with async_session_factory() as session:
        yield session


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def category_id() -> uuid.UUID:
    from app.db.models import Category, Priority

    async with async_session_factory() as db:
        category = Category(name="Network", default_priority=Priority.HIGH)
        db.add(category)
        await db.commit()
        await db.refresh(category)
        return category.id

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.db.base import Base, async_session_factory, engine
from app.main import app

settings = get_settings()


@pytest.fixture(scope="session", autouse=True)
async def _create_schema():
    """Create the schema once for the whole test session.

    Assumes DATABASE_URL points at a disposable local Postgres database
    (see backend/README.md) — never point this at a database with real data.
    """
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
            "TRUNCATE TABLE voice_call_sessions, tickets, categories RESTART IDENTITY CASCADE"
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

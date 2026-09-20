import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_health_is_a_static_liveness_check(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_ready_checks_the_database(client):
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_ready_returns_503_when_database_unreachable(client):
    from app.db.base import get_db
    from app.main import app

    async def broken_db():
        class BrokenSession:
            async def execute(self, *_args, **_kwargs):
                raise RuntimeError("simulated database outage")

        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    try:
        response = await client.get("/api/v1/health/ready")
        assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(get_db, None)

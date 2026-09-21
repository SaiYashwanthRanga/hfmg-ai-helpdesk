import pytest

from app.db.models import Category, Priority

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_list_categories_empty_when_none_exist(client):
    response = await client.get("/api/v1/categories")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_categories_returns_active_sorted_by_name(client, db_session):
    db_session.add_all(
        [
            Category(name="Zebra Systems", default_priority=Priority.LOW),
            Category(name="Alpha Network", default_priority=Priority.HIGH),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/v1/categories")
    assert response.status_code == 200
    names = [c["name"] for c in response.json()]

    assert names == ["Alpha Network", "Zebra Systems"]  # alphabetical, not insertion order


async def test_list_categories_excludes_inactive(client, db_session):
    db_session.add_all(
        [
            Category(name="Active One", default_priority=Priority.MEDIUM, is_active=True),
            Category(name="Retired One", default_priority=Priority.MEDIUM, is_active=False),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/v1/categories")
    names = [c["name"] for c in response.json()]

    assert names == ["Active One"]

"""The backend serves the built dashboard when FRONTEND_DIST_DIR is set."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def dashboard_app(tmp_path, monkeypatch):
    from app import main

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>dashboard</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("nope")

    app = FastAPI()
    monkeypatch.setattr(main, "app", app)
    main._serve_dashboard(tmp_path)
    return app


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


async def test_root_and_spa_routes_return_index(dashboard_app):
    assert "dashboard" in (await _get(dashboard_app, "/")).text
    assert "dashboard" in (await _get(dashboard_app, "/tickets/123")).text


async def test_static_assets_are_served(dashboard_app):
    response = await _get(dashboard_app, "/assets/app.js")
    assert response.status_code == 200 and "console.log" in response.text


async def test_unknown_api_path_is_json_404_not_index(dashboard_app):
    response = await _get(dashboard_app, "/api/v1/nope")
    assert response.status_code == 404 and response.json() == {"detail": "Not Found"}


async def test_path_traversal_is_not_served(dashboard_app):
    response = await _get(dashboard_app, "/..%2Fsecret.txt")
    assert "nope" not in response.text

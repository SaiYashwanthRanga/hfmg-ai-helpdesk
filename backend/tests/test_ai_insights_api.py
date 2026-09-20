import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_ai_insights_category_breakdown_is_ready(client, category_id):
    await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "issue",
        },
    )

    response = await client.get("/api/v1/ai-insights")
    assert response.status_code == 200
    body = response.json()

    assert body["category_breakdown"]["status"] == "ready"
    assert body["category_breakdown"]["items"][0]["count"] == 1


async def test_ai_insights_unresolved_sections_are_explicitly_blocked(client):
    response = await client.get("/api/v1/ai-insights")
    body = response.json()

    for key in ("trending_issues", "repeated_problems", "high_risk_alerts", "recommendations"):
        assert body[key]["status"] == "blocked"
        assert body[key]["blocked_reason"]  # a real, non-empty explanation
        assert "items" not in body[key]  # no field that could be mistaken for "no results"

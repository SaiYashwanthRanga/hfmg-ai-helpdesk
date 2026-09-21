import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_ticket(client, category_id, **overrides):
    payload = {
        "caller_name": "Caller",
        "phone_number": "+15550001111",
        "category_id": str(category_id),
        "description": "issue",
        **overrides,
    }
    response = await client.post("/api/v1/tickets", json=payload)
    return response.json()


async def test_kpis_counts_open_and_todays_tickets(client, category_id):
    await _create_ticket(client, category_id)
    resolved = await _create_ticket(client, category_id)
    await client.post(f"/api/v1/tickets/{resolved['id']}/status", json={"status": "OPEN"})
    await client.post(f"/api/v1/tickets/{resolved['id']}/status", json={"status": "RESOLVED"})

    response = await client.get("/api/v1/analytics/kpis")
    assert response.status_code == 200
    body = response.json()

    assert body["open_tickets"]["status"] == "ready"
    assert body["open_tickets"]["value"] == 1  # the RESOLVED one is excluded
    assert body["tickets_today"]["status"] == "ready"
    assert body["tickets_today"]["value"] == 2  # both created just now


async def test_kpis_calls_and_escalations_are_ready_now_that_voice_calls_exist(client, db_session):
    """Tier 2 shipped GET /voice-calls; these two KPIs are no longer blocked."""
    from app.db.models import VoiceCallSession, VoiceCallState

    db_session.add(
        VoiceCallSession(
            twilio_call_sid="CA-kpi-1",
            from_number="+18455550142",
            to_number="+18455559999",
            state=VoiceCallState.ESCALATED,
            escalated=True,
            collected={},
            turns=[],
        )
    )
    await db_session.commit()

    response = await client.get("/api/v1/analytics/kpis")
    body = response.json()

    assert body["calls_today"] == {"status": "ready", "value": 1, "blocked_reason": None}
    assert body["escalations"] == {"status": "ready", "value": 1, "blocked_reason": None}


async def test_kpis_ai_resolution_rate_is_explicitly_blocked(client):
    response = await client.get("/api/v1/analytics/kpis")
    body = response.json()

    assert body["ai_resolution_rate"]["status"] == "blocked"
    assert body["ai_resolution_rate"]["value"] is None
    assert body["ai_resolution_rate"]["blocked_reason"]  # a real, non-empty explanation


async def test_recent_activity_lists_tickets_most_recent_first(client, category_id):
    first = await _create_ticket(client, category_id, caller_name="First Caller")
    second = await _create_ticket(client, category_id, caller_name="Second Caller")

    response = await client.get("/api/v1/analytics/recent-activity")
    assert response.status_code == 200
    items = response.json()["items"]

    assert len(items) == 2
    assert items[0]["ticket_id"] == second["id"]
    assert items[1]["ticket_id"] == first["id"]
    assert items[0]["type"] == "ticket_created"


async def test_recent_activity_respects_limit(client, category_id):
    for i in range(3):
        await _create_ticket(client, category_id, phone_number=f"+1555000{i:04d}")

    response = await client.get("/api/v1/analytics/recent-activity", params={"limit": 2})
    assert len(response.json()["items"]) == 2


async def test_tickets_by_category_groups_and_orders_by_count(client, category_id):
    for _ in range(2):
        await _create_ticket(client, category_id)

    response = await client.get("/api/v1/analytics/tickets-by-category")
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["count"] == 2


async def test_tickets_by_priority_zero_fills_every_level(client, category_id):
    await _create_ticket(client, category_id, priority="URGENT")

    response = await client.get("/api/v1/analytics/tickets-by-priority")
    items = {item["priority"]: item["count"] for item in response.json()["items"]}

    assert items == {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "URGENT": 1}


async def test_tickets_by_source_zero_fills_every_source(client, category_id):
    await _create_ticket(client, category_id)

    response = await client.get("/api/v1/analytics/tickets-by-source")
    items = {item["source"]: item["count"] for item in response.json()["items"]}

    assert items == {"WEB": 1, "PHONE": 0, "EMAIL": 0, "WALK_IN": 0}


async def test_calls_by_day_zero_fills_the_window(client, db_session):
    from app.db.models import VoiceCallSession, VoiceCallState

    db_session.add(
        VoiceCallSession(
            twilio_call_sid="CA-day-1",
            from_number="+18455550142",
            to_number="+18455559999",
            state=VoiceCallState.COMPLETED,
            collected={},
            turns=[],
        )
    )
    await db_session.commit()

    response = await client.get("/api/v1/analytics/calls-by-day", params={"days": 7})
    items = response.json()["items"]

    assert len(items) == 7  # every day in the window present, not just days with calls
    assert sum(item["count"] for item in items) == 1


async def test_escalation_rate_only_counts_terminal_calls(client, db_session):
    from app.db.models import VoiceCallSession, VoiceCallState

    db_session.add_all(
        [
            VoiceCallSession(
                twilio_call_sid="CA-rate-completed",
                from_number="+18455550142",
                to_number="+18455559999",
                state=VoiceCallState.COMPLETED,
                collected={},
                turns=[],
            ),
            VoiceCallSession(
                twilio_call_sid="CA-rate-escalated",
                from_number="+18455550143",
                to_number="+18455559999",
                state=VoiceCallState.ESCALATED,
                escalated=True,
                collected={},
                turns=[],
            ),
            VoiceCallSession(
                twilio_call_sid="CA-rate-in-progress",
                from_number="+18455550144",
                to_number="+18455559999",
                state=VoiceCallState.COLLECT_NAME,
                collected={},
                turns=[],
            ),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/v1/analytics/escalation-rate")
    body = response.json()

    assert body["total_terminal_calls"] == 2  # the in-progress call is excluded
    assert body["escalated_calls"] == 1
    assert body["rate_percent"] == 50.0


async def test_ai_summary_usage_percentages_sum_to_100(client, category_id):
    await _create_ticket(client, category_id)
    await _create_ticket(client, category_id)

    response = await client.get("/api/v1/analytics/ai-summary-usage")
    items = response.json()["items"]

    disabled = next(i for i in items if i["status"] == "DISABLED")
    assert disabled["count"] == 2
    assert disabled["percentage"] == 100.0


async def test_ai_summary_usage_with_no_tickets_returns_zero_without_error(client):
    """Empty-window edge case: total=0 must not raise a ZeroDivisionError and
    every status is still zero-filled."""
    response = await client.get("/api/v1/analytics/ai-summary-usage")
    assert response.status_code == 200
    items = response.json()["items"]

    assert len(items) == 4  # every AISummaryStatus value present
    for item in items:
        assert item["count"] == 0
        assert item["percentage"] == 0.0


async def test_escalation_rate_with_no_calls_returns_zero_without_error(client):
    """total_terminal_calls=0 must not raise a ZeroDivisionError."""
    response = await client.get("/api/v1/analytics/escalation-rate")
    assert response.status_code == 200
    assert response.json() == {
        "total_terminal_calls": 0,
        "escalated_calls": 0,
        "rate_percent": 0.0,
    }


async def test_analytics_days_param_rejects_out_of_range_values(client):
    """days is Query(30, ge=1, le=365) on every windowed analytics endpoint;
    spot-check the boundary on one of them."""
    too_small = await client.get("/api/v1/analytics/tickets-by-category", params={"days": 0})
    assert too_small.status_code == 422

    too_large = await client.get("/api/v1/analytics/tickets-by-category", params={"days": 366})
    assert too_large.status_code == 422

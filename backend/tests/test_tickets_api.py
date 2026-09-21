import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_and_get_ticket(client, category_id):
    payload = {
        "caller_name": "Jamie Rivera",
        "phone_number": "+15559876543",
        "email": "jrivera@hfmg.net",
        "category_id": str(category_id),
        "priority": "HIGH",
        "description": "Cannot connect to the clinic wifi since this morning.",
    }
    response = await client.post("/api/v1/tickets", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["ticket_number"].startswith("HFMG-")
    assert body["status"] == "NEW"
    assert body["ai_summary_status"] == "DISABLED"  # AI summary is off by default

    ticket_id = body["id"]
    get_response = await client.get(f"/api/v1/tickets/{ticket_id}")
    assert get_response.status_code == 200
    assert get_response.json()["caller_name"] == "Jamie Rivera"


async def test_create_ticket_requires_description(client, category_id):
    payload = {
        "caller_name": "Jamie Rivera",
        "phone_number": "+15559876543",
        "category_id": str(category_id),
        "description": "",
    }
    response = await client.post("/api/v1/tickets", json=payload)
    assert response.status_code == 422


async def test_list_tickets_filters_by_status(client, category_id):
    for _ in range(2):
        await client.post(
            "/api/v1/tickets",
            json={
                "caller_name": "Caller",
                "phone_number": "+15550001111",
                "category_id": str(category_id),
                "description": "issue",
            },
        )

    all_tickets = await client.get("/api/v1/tickets")
    assert all_tickets.json()["total"] == 2

    open_tickets = await client.get("/api/v1/tickets", params={"status": "OPEN"})
    assert open_tickets.json()["total"] == 0


async def test_status_transition_valid_and_invalid(client, category_id):
    create_response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "issue",
        },
    )
    ticket_id = create_response.json()["id"]

    valid = await client.post(f"/api/v1/tickets/{ticket_id}/status", json={"status": "OPEN"})
    assert valid.status_code == 200
    assert valid.json()["status"] == "OPEN"

    invalid = await client.post(f"/api/v1/tickets/{ticket_id}/status", json={"status": "CLOSED"})
    assert invalid.status_code == 422


async def test_ticket_responses_include_source(client, category_id):
    """Regression test: TicketListItem/TicketRead previously omitted `source`
    even though the column and API_SPEC.md's documented contract both have
    it (BACKEND_GAP_ANALYSIS.md Tier 0, item 1)."""
    create_response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "issue",
        },
    )
    assert create_response.json()["source"] == "WEB"

    list_response = await client.get("/api/v1/tickets")
    assert list_response.json()["items"][0]["source"] == "WEB"

    ticket_id = create_response.json()["id"]
    detail_response = await client.get(f"/api/v1/tickets/{ticket_id}")
    assert detail_response.json()["source"] == "WEB"


async def test_list_tickets_filters_by_priority_and_source(client, category_id):
    await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "priority": "URGENT",
            "description": "issue",
        },
    )

    urgent = await client.get("/api/v1/tickets", params={"priority": "URGENT"})
    assert urgent.json()["total"] == 1

    low = await client.get("/api/v1/tickets", params={"priority": "LOW"})
    assert low.json()["total"] == 0

    web = await client.get("/api/v1/tickets", params={"source": "WEB"})
    assert web.json()["total"] == 1

    phone = await client.get("/api/v1/tickets", params={"source": "PHONE"})
    assert phone.json()["total"] == 0


async def test_list_tickets_search_matches_caller_name_and_description(client, category_id):
    await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Maria Lopez",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "Cannot connect to the clinic wifi since this morning.",
        },
    )
    await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Jordan Smith",
            "phone_number": "+15550002222",
            "category_id": str(category_id),
            "description": "Printer on the third floor is jammed.",
        },
    )

    by_name = await client.get("/api/v1/tickets", params={"q": "Maria"})
    assert by_name.json()["total"] == 1
    assert by_name.json()["items"][0]["caller_name"] == "Maria Lopez"

    by_description = await client.get("/api/v1/tickets", params={"q": "printer"})
    assert by_description.json()["total"] == 1
    assert by_description.json()["items"][0]["caller_name"] == "Jordan Smith"

    no_match = await client.get("/api/v1/tickets", params={"q": "nonexistent-term-xyz"})
    assert no_match.json()["total"] == 0


async def test_regenerate_summary_rejected_when_disabled(client, category_id):
    create_response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "issue",
        },
    )
    assert create_response.json()["ai_summary_status"] == "DISABLED"
    ticket_id = create_response.json()["id"]

    response = await client.post(f"/api/v1/tickets/{ticket_id}/regenerate-summary")
    assert response.status_code == 409


async def test_regenerate_summary_sets_pending_when_enabled(client, category_id, monkeypatch):
    from app.ai import summarizer
    from app.core.config import get_settings

    create_response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "issue",
        },
    )
    ticket_id = create_response.json()["id"]

    settings = get_settings()
    monkeypatch.setattr(settings, "enable_ai_summary", True)

    class UnconfiguredProvider:
        is_configured = False

    monkeypatch.setattr(summarizer, "get_provider", lambda: UnconfiguredProvider())

    response = await client.post(f"/api/v1/tickets/{ticket_id}/regenerate-summary")
    assert response.status_code == 202
    assert response.json() == {"ai_summary_status": "PENDING"}

    # The background task runs with no API key and must resolve the ticket to
    # FAILED rather than leaving it PENDING forever.
    get_response = await client.get(f"/api/v1/tickets/{ticket_id}")
    assert get_response.json()["ai_summary_status"] == "FAILED"


async def test_regenerate_summary_requires_existing_ticket(client):
    import uuid

    response = await client.post(f"/api/v1/tickets/{uuid.uuid4()}/regenerate-summary")
    assert response.status_code == 404


async def test_regenerate_summary_completes_successfully(client, category_id, monkeypatch):
    """End-to-end: regenerate-summary -> background task -> OpenAI call succeeds
    -> the ticket's ai_summary/ai_summary_status reflect COMPLETED."""
    from app.ai import summarizer
    from app.core.config import get_settings

    create_response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "issue",
        },
    )
    ticket_id = create_response.json()["id"]

    settings = get_settings()
    monkeypatch.setattr(settings, "enable_ai_summary", True)

    class ConfiguredProvider:
        is_configured = True

        async def structured(self, **kwargs):
            return {"summary": "Wifi outage affecting the back office."}

    monkeypatch.setattr(summarizer, "get_provider", lambda: ConfiguredProvider())

    response = await client.post(f"/api/v1/tickets/{ticket_id}/regenerate-summary")
    assert response.status_code == 202

    get_response = await client.get(f"/api/v1/tickets/{ticket_id}")
    body = get_response.json()
    assert body["ai_summary_status"] == "COMPLETED"
    assert body["ai_summary"] == "Wifi outage affecting the back office."


async def test_regenerate_summary_marks_failed_when_openai_call_fails(client, category_id, monkeypatch):
    """The OpenAI call itself failing (provider configured but returns no
    usable data) must leave the ticket in FAILED, not stuck in PENDING."""
    from app.ai import summarizer
    from app.core.config import get_settings

    create_response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "issue",
        },
    )
    ticket_id = create_response.json()["id"]

    settings = get_settings()
    monkeypatch.setattr(settings, "enable_ai_summary", True)

    class FailingProvider:
        is_configured = True

        async def structured(self, **kwargs):
            return None  # simulates a failed/timed-out OpenAI call

    monkeypatch.setattr(summarizer, "get_provider", lambda: FailingProvider())

    response = await client.post(f"/api/v1/tickets/{ticket_id}/regenerate-summary")
    assert response.status_code == 202

    get_response = await client.get(f"/api/v1/tickets/{ticket_id}")
    body = get_response.json()
    assert body["ai_summary_status"] == "FAILED"
    assert body["ai_summary"] is None


async def test_create_ticket_rejects_unknown_category(client):
    import uuid

    response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(uuid.uuid4()),
            "description": "issue",
        },
    )
    assert response.status_code == 400


async def test_create_ticket_rejects_inactive_category(client, db_session):
    from app.db.models import Category, Priority

    category = Category(name="Deprecated System", default_priority=Priority.LOW, is_active=False)
    db_session.add(category)
    await db_session.commit()
    await db_session.refresh(category)

    response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category.id),
            "description": "issue",
        },
    )
    assert response.status_code == 400


async def test_get_ticket_404_for_unknown_id(client):
    import uuid

    response = await client.get(f"/api/v1/tickets/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_update_status_404_for_unknown_ticket(client):
    import uuid

    response = await client.post(f"/api/v1/tickets/{uuid.uuid4()}/status", json={"status": "OPEN"})
    assert response.status_code == 404


async def test_status_transition_rejected_from_terminal_closed_state(client, category_id):
    """CLOSED has no valid outbound transitions (VALID_STATUS_TRANSITIONS) --
    a closed ticket must never be reopened via this endpoint."""
    create_response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "issue",
        },
    )
    ticket_id = create_response.json()["id"]

    await client.post(f"/api/v1/tickets/{ticket_id}/status", json={"status": "OPEN"})
    await client.post(f"/api/v1/tickets/{ticket_id}/status", json={"status": "RESOLVED"})
    closed = await client.post(f"/api/v1/tickets/{ticket_id}/status", json={"status": "CLOSED"})
    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED"

    blocked = await client.post(f"/api/v1/tickets/{ticket_id}/status", json={"status": "OPEN"})
    assert blocked.status_code == 422


async def test_status_transition_to_same_status_is_a_noop(client, category_id):
    """update_status allows new_status == current status even though it's not
    in VALID_STATUS_TRANSITIONS[current] -- an idempotent no-op, not a 422."""
    create_response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": "issue",
        },
    )
    ticket_id = create_response.json()["id"]

    response = await client.post(f"/api/v1/tickets/{ticket_id}/status", json={"status": "NEW"})
    assert response.status_code == 200
    assert response.json()["status"] == "NEW"


async def test_list_tickets_rejects_invalid_pagination(client):
    zero_page = await client.get("/api/v1/tickets", params={"page": 0})
    assert zero_page.status_code == 422

    oversized_page = await client.get("/api/v1/tickets", params={"page_size": 101})
    assert oversized_page.status_code == 422

    zero_page_size = await client.get("/api/v1/tickets", params={"page_size": 0})
    assert zero_page_size.status_code == 422

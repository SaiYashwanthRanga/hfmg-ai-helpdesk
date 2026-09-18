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

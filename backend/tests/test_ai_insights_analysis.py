"""GET /ai-insights/analysis: the model's reading of recent tickets, and what is kept from it."""

import pytest

from app.core.config import get_settings
from app.services import ai_insights_analysis

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()


class _Provider:
    """Stands in for the model: returns `answer` and records what it was sent."""

    name = "test"
    is_configured = True

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def structured(self, *, system, user, schema_name, schema, timeout=None, max_retries=None, model=None):
        self.calls.append({"system": system, "user": user, "schema_name": schema_name})
        return self.answer(user) if callable(self.answer) else self.answer


@pytest.fixture(autouse=True)
def _fresh_cache():
    ai_insights_analysis.clear_cache()
    yield
    ai_insights_analysis.clear_cache()


@pytest.fixture
def use_provider(monkeypatch):
    def install(answer):
        provider = _Provider(answer)
        monkeypatch.setattr(ai_insights_analysis, "get_provider", lambda: provider)
        return provider

    return install


async def _ticket(client, category_id, description, **extra):
    response = await client.post(
        "/api/v1/tickets",
        json={
            "caller_name": "Sample Caller",
            "phone_number": "+15550001111",
            "category_id": str(category_id),
            "description": description,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _answer(**parts):
    return {
        "headline": "Printers are the main problem.",
        "briefing": "Several printer tickets.",
        "themes": [],
        "attention": [],
        "recommendations": [],
        **parts,
    }


async def test_no_tickets_is_empty_and_never_calls_the_model(client, use_provider):
    provider = use_provider(_answer())
    body = (await client.get("/api/v1/ai-insights/analysis")).json()
    assert body["status"] == "empty"
    assert body["tickets_in_period"] == 0
    assert provider.calls == []


async def test_without_a_provider_key_the_counts_still_come_back(client, category_id):
    await _ticket(client, category_id, "Printer on floor two is offline")
    body = (await client.get("/api/v1/ai-insights/analysis")).json()
    assert body["status"] == "unavailable"
    assert body["reason"] == "not_configured"
    assert body["tickets_in_period"] == 1
    assert body["category_movement"][0]["count"] == 1
    assert body["category_movement"][0]["previous_count"] == 0
    assert body["themes"] == []


async def test_theme_counts_come_from_the_cited_tickets_not_the_model(client, category_id, use_provider):
    first = await _ticket(client, category_id, "Printer on floor two is offline", priority="URGENT")
    second = await _ticket(client, category_id, "Front desk printer will not print")
    await _ticket(client, category_id, "Forgot my password")
    # Newest first: T1 = password, T2 = front desk printer, T3 = floor two printer.
    use_provider(
        _answer(
            themes=[
                {"title": "Printers offline", "note": "Printers at two desks.", "ticket_refs": ["T2", "T3", "T99", "T2"]},
                {"title": "Only one ticket", "note": "", "ticket_refs": ["T1"]},
            ]
        )
    )

    body = (await client.get("/api/v1/ai-insights/analysis")).json()

    assert body["status"] == "ready"
    assert body["headline"] == "Printers are the main problem."
    assert body["tickets_analyzed"] == 3
    assert [t["title"] for t in body["themes"]] == ["Printers offline"]  # a theme of one ticket is not a theme
    theme = body["themes"][0]
    assert theme["ticket_count"] == 2  # T99 was never in the request; T2 counted once
    assert theme["open_count"] == 2
    assert theme["top_priority"] == "URGENT"
    assert {t["ticket_number"] for t in theme["tickets"]} == {first["ticket_number"], second["ticket_number"]}


async def test_a_ticket_belongs_to_one_theme(client, category_id, use_provider):
    for text in ("Printer A offline", "Printer B offline", "Printer C offline"):
        await _ticket(client, category_id, text)
    use_provider(
        _answer(
            themes=[
                {"title": "Printers offline", "note": "", "ticket_refs": ["T1", "T2"]},
                {"title": "Printing problems", "note": "", "ticket_refs": ["T2", "T3"]},  # T2 is taken: one left
            ]
        )
    )
    body = (await client.get("/api/v1/ai-insights/analysis")).json()
    assert [t["title"] for t in body["themes"]] == ["Printers offline"]


async def test_claims_that_cite_no_real_ticket_are_dropped(client, category_id, use_provider):
    ticket = await _ticket(client, category_id, "Exam room computer will not start", priority="HIGH")
    use_provider(
        _answer(
            attention=[
                {"title": "Exam room down", "reason": "A provider cannot chart.", "severity": "high", "ticket_refs": ["T1"]},
                {"title": "Made up", "reason": "No evidence.", "severity": "high", "ticket_refs": ["T7"]},
                {"title": "Bad severity", "reason": "x", "severity": "catastrophic", "ticket_refs": ["T1"]},
            ],
            recommendations=[
                {"action": "Send a technician to the exam room.", "rationale": "It blocks charting.", "ticket_refs": ["t1"]},
                {"action": "Buy more servers.", "rationale": "Generic.", "ticket_refs": []},
            ],
        )
    )

    body = (await client.get("/api/v1/ai-insights/analysis")).json()

    assert [a["title"] for a in body["attention"]] == ["Exam room down"]
    assert body["attention"][0]["tickets"] == [{"id": ticket["id"], "ticket_number": ticket["ticket_number"]}]
    assert [r["action"] for r in body["recommendations"]] == ["Send a technician to the exam room."]


async def test_a_ref_written_into_a_sentence_becomes_the_ticket_number(client, category_id, use_provider):
    ticket = await _ticket(client, category_id, "Exam room computer will not start")
    use_provider(
        _answer(
            briefing="T1 blocks charting; T44 was never sent.",
            recommendations=[{"action": "Send a technician.", "rationale": "T1 says it will not start.", "ticket_refs": ["T1"]}],
        )
    )
    body = (await client.get("/api/v1/ai-insights/analysis")).json()
    assert body["briefing"] == f"{ticket['ticket_number']} blocks charting; T44 was never sent."
    assert body["recommendations"][0]["rationale"] == f"{ticket['ticket_number']} says it will not start."


async def test_the_model_never_sees_contact_details_or_ticket_numbers(client, category_id, use_provider):
    ticket = await _ticket(
        client,
        category_id,
        "Call me back on 845-555-0142 or sample.caller@example.org about the scanner",
        email="sample.caller@example.org",
    )
    provider = use_provider(_answer())

    await client.get("/api/v1/ai-insights/analysis")

    sent = provider.calls[0]["user"]
    assert "scanner" in sent
    for secret in ("845-555-0142", "sample.caller@example.org", "Sample Caller", "+15550001111", ticket["ticket_number"]):
        assert secret not in sent


async def test_an_unusable_answer_is_unavailable_not_an_error(client, category_id, use_provider):
    await _ticket(client, category_id, "Printer offline")
    for answer in (None, {"unexpected": True}, {"headline": "", "briefing": ""}):
        ai_insights_analysis.clear_cache()
        use_provider(answer)
        response = await client.get("/api/v1/ai-insights/analysis")
        assert response.status_code == 200
        assert response.json()["status"] == "unavailable"
        assert response.json()["reason"] == "failed"


async def test_a_provider_that_raises_is_unavailable(client, category_id, use_provider):
    await _ticket(client, category_id, "Printer offline")

    def boom(_user):
        raise RuntimeError("provider down")

    use_provider(boom)
    body = (await client.get("/api/v1/ai-insights/analysis")).json()
    assert body["status"] == "unavailable"


async def test_the_reading_is_cached_until_refreshed(client, category_id, use_provider):
    await _ticket(client, category_id, "Printer offline")
    provider = use_provider(_answer())

    await client.get("/api/v1/ai-insights/analysis")
    await _ticket(client, category_id, "Scanner offline")
    second = (await client.get("/api/v1/ai-insights/analysis")).json()

    assert len(provider.calls) == 1
    assert second["tickets_in_period"] == 2  # the counts are always current, only the reading is cached

    refreshed = await client.post("/api/v1/ai-insights/analysis/refresh")
    assert refreshed.status_code == 200
    assert len(provider.calls) == 2
    assert refreshed.json()["tickets_analyzed"] == 2


async def test_switched_off_never_calls_the_model(client, category_id, use_provider, monkeypatch):
    await _ticket(client, category_id, "Printer offline")
    monkeypatch.setattr(settings, "enable_ai_insights", False)
    provider = use_provider(_answer())
    body = (await client.get("/api/v1/ai-insights/analysis")).json()
    assert (body["status"], body["reason"]) == ("unavailable", "disabled")
    assert provider.calls == []


async def test_tickets_by_day_is_zero_filled(client, category_id):
    await _ticket(client, category_id, "Printer offline")
    items = (await client.get("/api/v1/analytics/tickets-by-day?days=7")).json()["items"]
    assert len(items) == 7
    assert sum(i["count"] for i in items) == 1

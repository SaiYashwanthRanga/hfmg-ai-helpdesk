"""SIP gateway endpoint tests: bearer auth and the JSON turn contract."""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import VoiceCallSession, VoiceCallState
from app.speech import context as speech_context
from app.voice import nlu, scripts

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

START_URL = "/api/v1/voice/sip/start"
TURN_URL = "/api/v1/voice/sip/turn"
STATUS_URL = "/api/v1/voice/sip/status"
TOKEN = "test-gateway-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _gateway_token(monkeypatch):
    monkeypatch.setattr(settings, "voice_sip_gateway_token", TOKEN)


async def test_start_greets_and_creates_session(client, db_session):
    response = await client.post(
        START_URL,
        json={"call_id": "sip-1", "from_number": "+18455550142", "to_number": "+18455559999"},
        headers=AUTH,
    )

    assert response.status_code == 200
    body = response.json()
    assert "Horizon Family Medical Group" in " ".join(body["lines"])
    assert body["expect_reply"] is True

    session = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == "sip-1"))
    ).scalar_one()
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION
    assert session.collected["phone_number"] == "+18455550142"


async def test_start_is_idempotent(client, db_session):
    payload = {"call_id": "sip-dup", "from_number": "+18455550142"}
    await client.post(START_URL, json=payload, headers=AUTH)
    await client.post(START_URL, json=payload, headers=AUTH)

    sessions = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == "sip-dup"))
    ).scalars().all()
    assert len(sessions) == 1


async def test_turn_advances_conversation(client, monkeypatch):
    await client.post(START_URL, json={"call_id": "sip-turn", "from_number": "+18455550142"}, headers=AUTH)

    async def fake_description(utterance):
        return nlu.TurnResult(
            value="Printer is jammed",
            confidence="high",
            unable_to_determine=False,
            category="Other",
            extras={"short_issue": "printer"},
        )

    monkeypatch.setattr(nlu, "interpret_description", fake_description)

    response = await client.post(
        TURN_URL, json={"call_id": "sip-turn", "utterance": "the printer is jammed"}, headers=AUTH
    )

    assert response.status_code == 200
    body = response.json()
    assert any(option in " ".join(body["lines"]) for option in scripts.DETAILS_ASK_OPTIONS + scripts.NAME_ASK_OPTIONS)
    assert body["expect_reply"] is True


async def test_turn_for_unknown_call_returns_error_line(client):
    response = await client.post(TURN_URL, json={"call_id": "nope", "utterance": "hi"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["expect_reply"] is False


async def test_status_abandons_call_with_nothing_to_salvage(client, db_session):
    await client.post(START_URL, json={"call_id": "sip-drop"}, headers=AUTH)

    response = await client.post(STATUS_URL, json={"call_id": "sip-drop", "reason": "hangup"}, headers=AUTH)

    assert response.status_code == 204
    session = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == "sip-drop"))
    ).scalar_one()
    await db_session.refresh(session)
    assert session.state == VoiceCallState.ABANDONED
    assert session.ticket_id is None


async def test_missing_or_wrong_token_is_rejected(client):
    body = {"call_id": "sip-auth"}
    assert (await client.post(START_URL, json=body)).status_code == 401
    assert (await client.post(START_URL, json=body, headers={"Authorization": "Bearer wrong"})).status_code == 401


async def test_unconfigured_token_disables_endpoints(client, monkeypatch):
    monkeypatch.setattr(settings, "voice_sip_gateway_token", "")
    response = await client.post(START_URL, json={"call_id": "sip-off"}, headers=AUTH)
    assert response.status_code == 403


async def test_retried_start_does_not_reset_a_call_in_progress(client, db_session):
    await client.post(START_URL, json={"call_id": "sip-retry"}, headers=AUTH)
    session = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == "sip-retry"))
    ).scalar_one()
    session.state = VoiceCallState.COLLECT_PHONE
    session.turns = [*session.turns, {"role": "caller", "text": "printer is jammed"}]
    await db_session.commit()

    response = await client.post(START_URL, json={"call_id": "sip-retry"}, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["lines"] == [] and body["expect_reply"] is True and body["ticket_id"] is None
    assert body["stt_prompt"] == speech_context.stt_prompt("COLLECT_PHONE")
    await db_session.refresh(session)
    assert session.state == VoiceCallState.COLLECT_PHONE


# --- stt_prompt ---------------------------------------------------------------


async def test_start_returns_description_stt_prompt(client):
    response = await client.post(START_URL, json={"call_id": "sip-stt-start"}, headers=AUTH)

    body = response.json()
    assert set(body) == {"lines", "expect_reply", "ticket_id", "stt_prompt"}
    assert body["stt_prompt"] == speech_context.stt_prompt("COLLECT_DESCRIPTION")
    assert "IT issue or a patient service issue" in body["stt_prompt"]


async def test_turn_returns_prompt_for_the_next_answer(client, monkeypatch):
    await client.post(START_URL, json={"call_id": "sip-stt-turn", "from_number": "+18455550142"}, headers=AUTH)

    async def fake_description(utterance):
        return nlu.TurnResult(
            value="Printer is jammed",
            confidence="high",
            unable_to_determine=False,
            category="Other",
            extras={"short_issue": "printer"},
        )

    monkeypatch.setattr(nlu, "interpret_description", fake_description)
    response = await client.post(
        TURN_URL, json={"call_id": "sip-stt-turn", "utterance": "the printer is jammed"}, headers=AUTH
    )

    body = response.json()
    session_state = "COLLECT_DETAILS" if any(
        o in " ".join(body["lines"]) for o in scripts.DETAILS_ASK_OPTIONS
    ) else "COLLECT_NAME"
    assert body["expect_reply"] is True
    assert body["stt_prompt"] == speech_context.stt_prompt(session_state)


async def test_no_stt_prompt_when_the_call_ends(client):
    response = await client.post(TURN_URL, json={"call_id": "sip-stt-unknown", "utterance": "hi"}, headers=AUTH)

    body = response.json()
    assert body["expect_reply"] is False
    assert body["stt_prompt"] is None


async def test_stt_prompt_disabled_by_setting(client, monkeypatch):
    monkeypatch.setattr(settings, "speech_stt_context", False)
    response = await client.post(START_URL, json={"call_id": "sip-stt-off"}, headers=AUTH)

    body = response.json()
    assert body["expect_reply"] is True
    assert body["stt_prompt"] is None


# --- overlapping and failing turns --------------------------------------------------
#
# A call one utterance from filing its ticket: "skip" declines the email question by rule,
# with no model call, and every confirmation read-back is off in tests (conftest).

TURN_STATE = {
    "caller_type": "INTERNAL_IT",
    "description": "Synthetic printer jam",
    "short_issue": "the printer",
    "category": "Other",
    "caller_name": "Test Caller",
    "department": "Billing",
    "phone_number": "+18455550142",
    "started": "this morning",
    "work_blocked": False,
    "asked": ["details", "department", "email"],
}


async def _call_ready_to_file(client, db_session, call_id: str, *, caller_name: str = "Test Caller"):
    """Start a call and park it at the email question with everything else collected."""
    from app.db.models import Category, Priority

    if not (await db_session.execute(select(Category).where(Category.name == "Other"))).first():
        db_session.add(Category(name="Other", default_priority=Priority.MEDIUM))
        await db_session.commit()
    await client.post(START_URL, json={"call_id": call_id, "from_number": "+18455550142"}, headers=AUTH)
    session = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == call_id))
    ).scalar_one()
    session.state = VoiceCallState.COLLECT_EMAIL
    session.collected = {**TURN_STATE, "caller_name": caller_name}
    await db_session.commit()


async def _tickets(db_session):
    from app.db.models import Ticket

    db_session.expire_all()
    return (await db_session.execute(select(Ticket))).scalars().all()


async def _reload(db_session, call_id: str) -> VoiceCallSession:
    db_session.expire_all()
    return (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == call_id))
    ).scalar_one()


def _slow_create_ticket(monkeypatch, delay: float, *, only_for: str | None = None, entered=None, release=None):
    """Hold each ticket creation open for `delay` seconds, widening any race window.
    `entered` (an asyncio.Event) is set when a creation starts, so a test can time
    another request to arrive while it is in progress; `release` replaces the fixed
    delay with waiting (up to 10 s) for that event."""
    import asyncio

    from app.services import ticket_service

    real = ticket_service.create_ticket

    async def slow(db, payload, *args, **kwargs):
        if only_for is None or payload.caller_name == only_for:
            if entered is not None:
                entered.set()
            if release is not None:
                await asyncio.wait_for(release.wait(), timeout=10)
            else:
                await asyncio.sleep(delay)
        return await real(db, payload, *args, **kwargs)

    monkeypatch.setattr(ticket_service, "create_ticket", slow)


async def test_concurrent_turns_on_one_call_file_a_single_ticket(client, db_session, monkeypatch):
    """A gateway retry can overlap the original /turn request. Both must not file a ticket."""
    import asyncio

    from app.voice import sip_routes

    await _call_ready_to_file(client, db_session, "sip-race")
    _slow_create_ticket(monkeypatch, 0.3)  # both requests have read the call before either files
    dispatched: list = []
    real_dispatch = sip_routes.dispatch_ticket_tasks

    async def counting_dispatch(db, background_tasks, ticket_id):
        dispatched.append(ticket_id)
        await real_dispatch(db, background_tasks, ticket_id)

    monkeypatch.setattr(sip_routes, "dispatch_ticket_tasks", counting_dispatch)

    body = {"call_id": "sip-race", "utterance": "skip"}
    first, second = await asyncio.gather(
        client.post(TURN_URL, json=body, headers=AUTH),
        client.post(TURN_URL, json=body, headers=AUTH),
    )

    assert first.status_code == second.status_code == 200
    tickets = await _tickets(db_session)
    assert len(tickets) == 1
    ticket_id = tickets[0].id
    call = await _reload(db_session, "sip-race")
    assert call.collected["ticket_ids"] == [str(ticket_id)]
    # One ticket created, so one summary + helpdesk email -- not one per request.
    assert dispatched == [ticket_id]


async def test_different_calls_file_independent_tickets_without_waiting_on_each_other(
    client, db_session, monkeypatch
):
    import asyncio
    import time

    await _call_ready_to_file(client, db_session, "sip-slow", caller_name="Slow Caller")
    await _call_ready_to_file(client, db_session, "sip-fast", caller_name="Fast Caller")
    # The slow call's ticket creation stays open until the fast call has been answered. If the
    # two calls shared a lock, the fast one would wait for the slow one and neither could finish.
    fast_answered = asyncio.Event()
    _slow_create_ticket(monkeypatch, 0, only_for="Slow Caller", release=fast_answered)
    finished: dict[str, float] = {}

    async def turn(call_id: str):
        response = await client.post(TURN_URL, json={"call_id": call_id, "utterance": "skip"}, headers=AUTH)
        finished[call_id] = time.perf_counter()
        if call_id == "sip-fast":
            fast_answered.set()
        return response

    slow, fast = await asyncio.gather(turn("sip-slow"), turn("sip-fast"))

    assert slow.status_code == fast.status_code == 200
    assert slow.json()["ticket_id"] and fast.json()["ticket_id"]
    assert slow.json()["ticket_id"] != fast.json()["ticket_id"]
    assert finished["sip-fast"] < finished["sip-slow"]  # the fast call was not queued behind the slow one
    assert len(await _tickets(db_session)) == 2


async def test_many_calls_filing_at_once_get_distinct_ticket_numbers(client, db_session):
    import asyncio

    calls = [f"sip-many-{n}" for n in range(12)]
    for call_id in calls:
        await _call_ready_to_file(client, db_session, call_id)

    responses = await asyncio.gather(
        *(client.post(TURN_URL, json={"call_id": c, "utterance": "skip"}, headers=AUTH) for c in calls)
    )

    assert all(r.status_code == 200 and r.json()["ticket_id"] for r in responses)
    tickets = await _tickets(db_session)
    assert len(tickets) == len(calls)
    assert len({t.ticket_number for t in tickets}) == len(calls)


def _fail_create_ticket(monkeypatch, *, times: int | None = 1):
    """create_ticket raises a real database error (a NOT NULL violation, which aborts the
    transaction) for the first `times` calls (always, if None), then behaves normally."""
    from sqlalchemy import text

    from app.services import ticket_service

    real = ticket_service.create_ticket
    state = {"failures": 0}

    async def failing(db, payload, *args, **kwargs):
        if times is None or state["failures"] < times:
            state["failures"] += 1
            await db.execute(text("INSERT INTO tickets (id) VALUES (gen_random_uuid())"))
        return await real(db, payload, *args, **kwargs)

    monkeypatch.setattr(ticket_service, "create_ticket", failing)
    return state


async def test_database_error_mid_turn_is_rolled_back_and_escalated_on_a_clean_transaction(
    client, db_session, monkeypatch
):
    """The failed transaction must not be reused: escalation runs after a rollback, and
    only the escalation's ticket exists afterwards."""
    from app.db.models import EscalationReason

    await _call_ready_to_file(client, db_session, "sip-dberr")
    failures = _fail_create_ticket(monkeypatch, times=1)

    response = await client.post(TURN_URL, json={"call_id": "sip-dberr", "utterance": "skip"}, headers=AUTH)

    assert failures["failures"] == 1
    body = response.json()
    assert response.status_code == 200
    assert body["expect_reply"] is False
    assert scripts.SYSTEM_ERROR not in body["lines"]  # the escalation really happened
    tickets = await _tickets(db_session)
    assert len(tickets) == 1
    ticket_id, description = str(tickets[0].id), tickets[0].description
    assert ticket_id == body["ticket_id"]
    assert "automated intake hit a system error" in description
    call = await _reload(db_session, "sip-dberr")
    assert call.state == VoiceCallState.ESCALATED
    assert call.escalation_reason == EscalationReason.SYSTEM_ERROR
    assert call.collected["ticket_ids"] == [ticket_id]
    assert [t["text"] for t in call.turns if t["role"] == "caller"] == ["skip"]  # the failed turn, recorded once


async def test_unrecoverable_database_failure_says_so_and_leaves_the_call_usable(client, db_session, monkeypatch):
    """If escalation fails too, the caller hears the plain error line (no claim of a ticket), nothing is
    half-saved, and the next request on the call is processed normally."""
    from app.services import ticket_service

    await _call_ready_to_file(client, db_session, "sip-dbdown")
    real = ticket_service.create_ticket
    _fail_create_ticket(monkeypatch, times=None)

    response = await client.post(TURN_URL, json={"call_id": "sip-dbdown", "utterance": "skip"}, headers=AUTH)

    body = response.json()
    assert response.status_code == 200
    assert body == {"lines": [scripts.SYSTEM_ERROR], "expect_reply": False, "ticket_id": None, "stt_prompt": None}
    assert await _tickets(db_session) == []
    call = await _reload(db_session, "sip-dbdown")
    assert call.state == VoiceCallState.COLLECT_EMAIL  # nothing from the failed turns was committed
    assert not call.escalated and call.ticket_id is None
    assert "ticket_ids" not in call.collected and call.collected.get("ticket_filed") is None

    monkeypatch.setattr(ticket_service, "create_ticket", real)  # the database is back
    retry = await client.post(TURN_URL, json={"call_id": "sip-dbdown", "utterance": "skip"}, headers=AUTH)

    assert retry.status_code == 200 and retry.json()["ticket_id"]
    assert len(await _tickets(db_session)) == 1


async def test_exception_after_the_ticket_is_flushed_does_not_commit_partial_state(client, db_session, monkeypatch):
    """The ticket and the call's 'filed' flag commit together or not at all: a failure after the
    ticket was flushed discards both, and the call is escalated on a fresh transaction."""
    from sqlalchemy import func

    from app.db.models import TicketNumberCounter

    await _call_ready_to_file(client, db_session, "sip-late")
    real = scripts.spoken_ticket_number
    raised = []

    def explode_once(number):
        if not raised:
            raised.append(number)
            raise RuntimeError("synthetic failure while building the reply")
        return real(number)

    monkeypatch.setattr(scripts, "spoken_ticket_number", explode_once)

    response = await client.post(TURN_URL, json={"call_id": "sip-late", "utterance": "skip"}, headers=AUTH)

    assert raised, "the failure was never triggered"
    tickets = await _tickets(db_session)
    assert len(tickets) == 1
    ticket_id, ticket_number, description = str(tickets[0].id), tickets[0].ticket_number, tickets[0].description
    assert "automated intake hit a system error" in description  # not the discarded normal ticket
    assert response.json()["ticket_id"] == ticket_id
    call = await _reload(db_session, "sip-late")
    assert call.state == VoiceCallState.ESCALATED
    assert call.collected["ticket_ids"] == [ticket_id]
    # The discarded attempt's number was rolled back with it: no gap.
    assert ticket_number.endswith("000001")
    assert (await db_session.execute(select(func.max(TicketNumberCounter.last_value)))).scalar_one() == 1


async def test_database_errors_are_logged_without_caller_data(client, db_session, monkeypatch, caplog):
    """A failed INSERT's error text would otherwise carry its bound values (name, description)."""
    import uuid

    from app.db.models import Ticket
    from app.services import ticket_service

    await _call_ready_to_file(client, db_session, "sip-phi")
    sentinel = "PHI-SENTINEL-Maria-Lopez-chest-pain"

    async def bad_insert(db, payload, *args, **kwargs):
        db.add(
            Ticket(
                ticket_number="HFMG-9999-000001", caller_name=sentinel, phone_number="555",
                category_id=uuid.uuid4(),  # no such category: a foreign-key violation on flush
                description=sentinel,
            )
        )
        await db.flush()

    monkeypatch.setattr(ticket_service, "create_ticket", bad_insert)

    with caplog.at_level("INFO"):
        response = await client.post(TURN_URL, json={"call_id": "sip-phi", "utterance": "skip"}, headers=AUTH)

    assert response.status_code == 200
    assert "IntegrityError" in caplog.text  # the failure is diagnosable...
    assert sentinel not in caplog.text  # ...without the values that were being written


async def test_hangup_while_the_ticket_is_being_filed_does_not_salvage_a_second_one(
    client, db_session, monkeypatch
):
    """The caller hangs up as their last answer files the ticket: /status must wait for the turn
    and see the ticket, not salvage the 'abandoned' call into a duplicate."""
    import asyncio

    from app.db.models import VoiceCallState

    await _call_ready_to_file(client, db_session, "sip-hangup")
    creating = asyncio.Event()
    _slow_create_ticket(monkeypatch, 0.4, entered=creating)

    async def hang_up():
        await asyncio.wait_for(creating.wait(), timeout=10)  # the turn is inside ticket creation, holding the call
        return await client.post(STATUS_URL, json={"call_id": "sip-hangup", "reason": "hangup"}, headers=AUTH)

    turn, status = await asyncio.gather(
        client.post(TURN_URL, json={"call_id": "sip-hangup", "utterance": "skip"}, headers=AUTH),
        hang_up(),
    )

    assert turn.status_code == 200 and status.status_code == 204
    tickets = await _tickets(db_session)
    assert len(tickets) == 1
    ticket_id = str(tickets[0].id)
    call = await _reload(db_session, "sip-hangup")
    assert call.state == VoiceCallState.ANYTHING_ELSE  # a normally filed ticket, not marked abandoned
    assert call.ended_at is not None
    assert call.collected["ticket_ids"] == [ticket_id]


async def test_simultaneous_start_requests_for_a_new_call_create_one_session(client, db_session):
    import asyncio

    payload = {"call_id": "sip-start-race", "from_number": "+18455550142"}

    responses = await asyncio.gather(
        client.post(START_URL, json=payload, headers=AUTH),
        client.post(START_URL, json=payload, headers=AUTH),
        return_exceptions=True,
    )

    assert not [r for r in responses if isinstance(r, BaseException)], responses
    assert [r.status_code for r in responses] == [200, 200]
    sessions = (
        await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == "sip-start-race"))
    ).scalars().all()
    assert len(sessions) == 1


async def test_failed_commit_discards_the_turn_and_escalates_on_a_fresh_transaction(
    client, db_session, monkeypatch
):
    """The commit that would save the ticket and the 'filed' flag fails (a dropped connection).
    Neither was saved, so neither may be claimed: the call is escalated and has exactly one ticket."""
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import AsyncSession

    await _call_ready_to_file(client, db_session, "sip-commit")
    real_commit = AsyncSession.commit
    attempts = []

    async def flaky_commit(self):
        attempts.append(1)
        if len(attempts) == 1:
            raise OperationalError("COMMIT", {}, Exception("synthetic connection loss"))
        return await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)

    response = await client.post(TURN_URL, json={"call_id": "sip-commit", "utterance": "skip"}, headers=AUTH)

    assert len(attempts) >= 2  # the failed commit, then the escalation's own
    tickets = await _tickets(db_session)
    assert len(tickets) == 1
    ticket_id, description = str(tickets[0].id), tickets[0].description
    assert response.json()["ticket_id"] == ticket_id
    assert "automated intake hit a system error" in description
    call = await _reload(db_session, "sip-commit")
    assert call.state == VoiceCallState.ESCALATED
    assert call.collected["ticket_ids"] == [ticket_id]

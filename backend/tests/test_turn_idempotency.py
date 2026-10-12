"""/turn with a gateway-supplied turn_id: a repeated turn is answered from its saved result.

See docs/TURN_ID_CONTRACT.md. All callers, utterances and tickets here are synthetic.
"""

import asyncio

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import VoiceCallSession, VoiceCallState, VoiceTurn
from app.voice import orchestrator, scripts, sip_routes
from app.voice.session import get_session
from tests.test_sip_routes import (
    AUTH,
    START_URL,
    STATUS_URL,
    TURN_URL,
    _call_ready_to_file,
    _fail_create_ticket,
    _reload,
    _slow_create_ticket,
    _tickets,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
REPLAYED = "Idempotent-Replayed"


@pytest.fixture(autouse=True)
def _gateway_token(monkeypatch):
    monkeypatch.setattr(settings, "voice_sip_gateway_token", "test-gateway-token")
    AUTH["Authorization"] = "Bearer test-gateway-token"


@pytest.fixture
def spies(monkeypatch):
    """Count how often the orchestrator runs a turn and how often a ticket's follow-up is started."""
    counts = {"turns": 0, "dispatched": []}
    real_handle, real_dispatch = orchestrator.handle_turn, sip_routes.dispatch_ticket_tasks

    async def handle_turn(*args, **kwargs):
        counts["turns"] += 1
        return await real_handle(*args, **kwargs)

    async def dispatch(db, background_tasks, ticket_id):
        counts["dispatched"].append(ticket_id)
        await real_dispatch(db, background_tasks, ticket_id)

    monkeypatch.setattr(orchestrator, "handle_turn", handle_turn)
    monkeypatch.setattr(sip_routes, "dispatch_ticket_tasks", dispatch)
    return counts


def _turn(client, call_id: str, turn_id: str | None, utterance: str = "skip"):
    body = {"call_id": call_id, "utterance": utterance}
    if turn_id is not None:
        body["turn_id"] = turn_id
    return client.post(TURN_URL, json=body, headers=AUTH)


async def _recorded(db_session, call_id: str | None = None) -> list[VoiceTurn]:
    db_session.expire_all()
    stmt = select(VoiceTurn).order_by(VoiceTurn.created_at)
    return list((await db_session.execute(stmt)).scalars().all())


# --- replay ------------------------------------------------------------------------


async def test_a_completed_turn_is_replayed_without_running_anything_again(client, db_session, spies):
    await _call_ready_to_file(client, db_session, "idem-replay")

    first = await _turn(client, "idem-replay", "turn-1")
    before = await _reload(db_session, "idem-replay")
    transcript, collected, state = len(before.turns), dict(before.collected), before.state
    second = await _turn(client, "idem-replay", "turn-1")

    assert first.status_code == second.status_code == 200
    assert first.headers.get(REPLAYED) is None
    assert second.headers[REPLAYED] == "true"
    assert second.json() == first.json() and first.json()["ticket_id"]
    after = await _reload(db_session, "idem-replay")
    assert (len(after.turns), after.collected, after.state) == (transcript, collected, state)
    assert len(await _tickets(db_session)) == 1
    assert spies["turns"] == 1  # the second request never reached the conversation logic
    assert len(spies["dispatched"]) == 1  # one summary + helpdesk email for one ticket
    assert len(await _recorded(db_session)) == 1


async def test_a_replay_still_works_after_the_conversation_moved_on_and_the_call_ended(client, db_session, spies):
    await _call_ready_to_file(client, db_session, "idem-later")
    filed = await _turn(client, "idem-later", "turn-1")  # files the ticket: "anything else?"
    goodbye = await _turn(client, "idem-later", "turn-2", "no")  # "no": call completes
    await client.post(STATUS_URL, json={"call_id": "idem-later", "reason": "hangup"}, headers=AUTH)

    replay_first = await _turn(client, "idem-later", "turn-1")
    replay_second = await _turn(client, "idem-later", "turn-2", "no")

    assert filed.json() != goodbye.json() and goodbye.json()["expect_reply"] is False
    assert replay_first.json() == filed.json() and replay_second.json() == goodbye.json()
    assert replay_first.headers[REPLAYED] == replay_second.headers[REPLAYED] == "true"
    assert (await _reload(db_session, "idem-later")).state == VoiceCallState.COMPLETED
    assert spies["turns"] == 2
    assert len(await _tickets(db_session)) == 1


async def test_distinct_turns_on_one_call_are_each_processed_once(client, db_session, spies):
    await _call_ready_to_file(client, db_session, "idem-two")

    one = await _turn(client, "idem-two", "turn-1")
    two = await _turn(client, "idem-two", "turn-2", "no")

    assert one.headers.get(REPLAYED) is None and two.headers.get(REPLAYED) is None
    assert one.json() != two.json()
    assert spies["turns"] == 2
    assert {r.turn_id for r in await _recorded(db_session)} == {"turn-1", "turn-2"}


async def test_the_same_turn_id_on_different_calls_is_independent(client, db_session, spies):
    await _call_ready_to_file(client, db_session, "idem-a")
    await _call_ready_to_file(client, db_session, "idem-b")

    a, b = await asyncio.gather(_turn(client, "idem-a", "turn-1"), _turn(client, "idem-b", "turn-1"))

    assert a.headers.get(REPLAYED) is None and b.headers.get(REPLAYED) is None
    assert a.json()["ticket_id"] and b.json()["ticket_id"] and a.json()["ticket_id"] != b.json()["ticket_id"]
    assert len(await _tickets(db_session)) == 2
    assert len(spies["dispatched"]) == 2
    assert len(await _recorded(db_session)) == 2


# --- concurrency and the in-progress policy -----------------------------------------


async def test_concurrent_identical_requests_process_the_turn_once(client, db_session, monkeypatch, spies):
    await _call_ready_to_file(client, db_session, "idem-race")
    _slow_create_ticket(monkeypatch, 0.3)  # the second request arrives while the first is filing

    first, second = await asyncio.gather(_turn(client, "idem-race", "turn-1"), _turn(client, "idem-race", "turn-1"))

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert {first.headers.get(REPLAYED), second.headers.get(REPLAYED)} == {None, "true"}  # one ran, one replayed
    assert spies["turns"] == 1
    assert len(spies["dispatched"]) == 1
    assert len(await _tickets(db_session)) == 1
    assert len(await _recorded(db_session)) == 1


async def test_a_duplicate_that_outwaits_the_first_attempt_gets_409_and_can_retry(client, db_session, monkeypatch, spies):
    """The wait is bounded and explicit; the duplicate is never processed alongside the original."""
    await _call_ready_to_file(client, db_session, "idem-busy")
    monkeypatch.setattr(settings, "voice_turn_lock_wait_seconds", 0.3)
    filing, release = asyncio.Event(), asyncio.Event()
    _slow_create_ticket(monkeypatch, 0, entered=filing, release=release)

    original = asyncio.create_task(_turn(client, "idem-busy", "turn-1"))
    await asyncio.wait_for(filing.wait(), timeout=10)
    busy = await _turn(client, "idem-busy", "turn-1")
    release.set()
    finished = await original
    retry = await _turn(client, "idem-busy", "turn-1")

    assert busy.status_code == 409
    assert busy.json()["detail"]["code"] == "turn_in_progress"
    assert busy.headers["Retry-After"] == "1"
    assert finished.status_code == 200 and finished.json()["ticket_id"]
    assert retry.status_code == 200 and retry.headers[REPLAYED] == "true" and retry.json() == finished.json()
    assert spies["turns"] == 1
    assert len(await _tickets(db_session)) == 1


async def test_the_lock_wait_bound_does_not_leak_into_later_locks_of_the_transaction(client, db_session):
    await _call_ready_to_file(client, db_session, "idem-timeout-scope")

    session = await get_session(db_session, "idem-timeout-scope", lock=True, wait_seconds=2)

    assert session is not None
    assert (await db_session.execute(text("SHOW lock_timeout"))).scalar_one() == "0"
    await db_session.rollback()


# --- conflicting and invalid ids ------------------------------------------------------


async def test_reusing_a_turn_id_with_a_different_utterance_is_rejected(client, db_session, spies):
    await _call_ready_to_file(client, db_session, "idem-conflict")
    first = await _turn(client, "idem-conflict", "turn-1")
    before = await _reload(db_session, "idem-conflict")
    collected, transcript = dict(before.collected), len(before.turns)

    conflicting = await _turn(client, "idem-conflict", "turn-1", "no thanks")

    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "turn_payload_conflict"
    after = await _reload(db_session, "idem-conflict")
    assert (after.collected, len(after.turns)) == (collected, transcript)  # nothing was run or changed
    assert spies["turns"] == 1 and len(await _tickets(db_session)) == 1
    again = await _turn(client, "idem-conflict", "turn-1")  # the genuine retry is unaffected
    assert again.json() == first.json() and again.headers[REPLAYED] == "true"


async def test_confidence_does_not_make_a_retry_a_different_turn(client, db_session, spies):
    await _call_ready_to_file(client, db_session, "idem-confidence")
    body = {"call_id": "idem-confidence", "utterance": "skip", "turn_id": "turn-1"}

    first = await client.post(TURN_URL, json={**body, "confidence": 0.91}, headers=AUTH)
    second = await client.post(TURN_URL, json={**body, "confidence": 0.5}, headers=AUTH)

    assert second.status_code == 200 and second.json() == first.json() and second.headers[REPLAYED] == "true"


@pytest.mark.parametrize("turn_id", ["", "has space", "semi;colon", "x" * 65, "snowman-☃"])
async def test_a_malformed_turn_id_is_rejected_before_anything_runs(client, db_session, spies, turn_id):
    await _call_ready_to_file(client, db_session, "idem-invalid")

    response = await _turn(client, "idem-invalid", turn_id)

    assert response.status_code == 422
    assert spies["turns"] == 0
    assert await _recorded(db_session) == []


async def test_the_database_allows_one_record_per_call_and_turn_id(client, db_session):
    await _call_ready_to_file(client, db_session, "idem-unique")
    call = await _reload(db_session, "idem-unique")
    db_session.add(VoiceTurn(session_id=call.id, turn_id="turn-1", fingerprint="a", response={}))
    await db_session.commit()
    db_session.add(VoiceTurn(session_id=call.id, turn_id="turn-1", fingerprint="b", response={}))

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# --- legacy gateway (no turn_id) ------------------------------------------------------


async def test_a_request_without_a_turn_id_behaves_as_before_and_is_not_deduplicated(client, db_session, spies):
    """Until the gateway sends turn_ids, a retry is processed like any other utterance."""
    await _call_ready_to_file(client, db_session, "idem-legacy")

    first = await _turn(client, "idem-legacy", None)
    second = await _turn(client, "idem-legacy", None)

    assert first.status_code == second.status_code == 200
    assert first.headers.get(REPLAYED) is None and second.headers.get(REPLAYED) is None
    assert spies["turns"] == 2
    assert await _recorded(db_session) == []
    assert len(await _tickets(db_session)) == 1  # still one ticket: the existing per-call locking


# --- failures: rollback, escalation, uncertain commit ------------------------------------


async def test_an_escalated_turn_is_saved_under_its_turn_id_and_replays_as_escalated(
    client, db_session, monkeypatch, spies
):
    await _call_ready_to_file(client, db_session, "idem-escalated")
    _fail_create_ticket(monkeypatch, times=1)

    first = await _turn(client, "idem-escalated", "turn-1")
    retry = await _turn(client, "idem-escalated", "turn-1")

    assert first.status_code == 200 and first.json()["ticket_id"]
    assert first.json()["lines"] != [scripts.SYSTEM_ERROR]
    assert retry.json() == first.json() and retry.headers[REPLAYED] == "true"
    tickets = await _tickets(db_session)
    assert len(tickets) == 1  # the escalation's callback ticket, once
    assert (await _reload(db_session, "idem-escalated")).state == VoiceCallState.ESCALATED
    assert len(spies["dispatched"]) == 1
    assert len(await _recorded(db_session)) == 1


async def test_a_turn_that_could_not_be_saved_leaves_no_record_and_can_be_retried(client, db_session, monkeypatch, spies):
    """No 'processing' row is left behind to block the retry: the failed turn simply never happened."""
    from app.services import ticket_service

    await _call_ready_to_file(client, db_session, "idem-unsaved")
    real = ticket_service.create_ticket
    _fail_create_ticket(monkeypatch, times=None)

    failed = await _turn(client, "idem-unsaved", "turn-1")

    assert failed.json() == {
        "lines": [scripts.SYSTEM_ERROR], "expect_reply": False, "ticket_id": None, "stt_prompt": None,
        "turn_id": "turn-1",
    }
    assert await _recorded(db_session) == [] and await _tickets(db_session) == []
    assert (await _reload(db_session, "idem-unsaved")).state == VoiceCallState.COLLECT_EMAIL

    monkeypatch.setattr(ticket_service, "create_ticket", real)  # the database is back
    retry = await _turn(client, "idem-unsaved", "turn-1")

    assert retry.status_code == 200 and retry.headers.get(REPLAYED) is None and retry.json()["ticket_id"]
    assert len(await _tickets(db_session)) == 1 and len(await _recorded(db_session)) == 1


async def test_an_uncertain_commit_that_did_succeed_returns_the_saved_turn_not_an_escalation(
    client, db_session, monkeypatch, spies
):
    """The commit went through but the connection failed before the answer: the turn is found
    by its turn_id and its own reply is returned, not a callback ticket on top of it."""
    await _call_ready_to_file(client, db_session, "idem-uncertain")
    real_commit, attempts = AsyncSession.commit, []

    async def commit_then_fail(self):
        attempts.append(1)
        await real_commit(self)
        if len(attempts) == 1:
            raise OperationalError("COMMIT", {}, Exception("synthetic connection loss after commit"))

    monkeypatch.setattr(AsyncSession, "commit", commit_then_fail)

    first = await _turn(client, "idem-uncertain", "turn-1")
    monkeypatch.setattr(AsyncSession, "commit", real_commit)
    retry = await _turn(client, "idem-uncertain", "turn-1")

    assert first.status_code == 200 and first.json()["ticket_id"] and first.json()["expect_reply"] is True
    assert retry.json() == first.json() and retry.headers[REPLAYED] == "true"
    tickets = await _tickets(db_session)
    assert len(tickets) == 1
    description = tickets[0].description
    assert "system error" not in description  # an ordinary ticket, not a callback request
    call = await _reload(db_session, "idem-uncertain")
    assert call.state == VoiceCallState.ANYTHING_ELSE and not call.escalated
    assert spies["turns"] == 1
    assert len(spies["dispatched"]) == 1  # the original request never got to start it, so this one did, once


async def test_an_uncertain_commit_that_did_not_succeed_is_escalated_and_recorded(client, db_session, monkeypatch, spies):
    await _call_ready_to_file(client, db_session, "idem-lost")
    real_commit, attempts = AsyncSession.commit, []

    async def fail_before_commit(self):
        attempts.append(1)
        if len(attempts) == 1:
            raise OperationalError("COMMIT", {}, Exception("synthetic connection loss before commit"))
        await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", fail_before_commit)

    first = await _turn(client, "idem-lost", "turn-1")
    retry = await _turn(client, "idem-lost", "turn-1")

    assert first.status_code == 200 and first.json()["ticket_id"]
    assert retry.json() == first.json() and retry.headers[REPLAYED] == "true"
    tickets = await _tickets(db_session)
    assert len(tickets) == 1 and "automated intake hit a system error" in tickets[0].description
    assert len(await _recorded(db_session)) == 1


async def test_a_turn_that_only_reads_back_an_existing_ticket_never_restarts_its_follow_up(
    client, db_session, monkeypatch, spies
):
    """Not just on replays: even when an uncertain commit sends a turn through recovery, a ticket
    that was already filed earlier on the call is not summarized and emailed again."""
    await _call_ready_to_file(client, db_session, "idem-readback")
    filed = await _turn(client, "idem-readback", "turn-1")
    call = await _reload(db_session, "idem-readback")
    call.state = VoiceCallState.COLLECT_EMAIL  # back at a step that ends in the existing ticket's read-back
    await db_session.commit()
    real_commit, attempts = AsyncSession.commit, []

    async def commit_then_fail(self):
        attempts.append(1)
        await real_commit(self)
        if len(attempts) == 1:
            raise OperationalError("COMMIT", {}, Exception("synthetic connection loss after commit"))

    monkeypatch.setattr(AsyncSession, "commit", commit_then_fail)

    readback = await _turn(client, "idem-readback", "turn-2")

    assert readback.status_code == 200 and readback.json()["ticket_id"] == filed.json()["ticket_id"]
    assert len(await _tickets(db_session)) == 1
    assert len(spies["dispatched"]) == 1  # turn-1's, and nothing for the read-back

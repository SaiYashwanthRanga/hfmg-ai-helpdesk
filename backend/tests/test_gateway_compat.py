"""What the SIP gateway can send, and what the backend does with it.

Covers the gateway developer's audit: abandoned-call tickets and placeholder caller IDs, internal
extensions, the turn_id contract, and Phase 2 (directory) behaviour under repeated and concurrent
requests. Synthetic callers only; the language model is stubbed.
"""

import asyncio

import pytest
from sqlalchemy import func, select

from app.db.models import Ticket, VoiceCallState
from app.main import app
from app.services.user_directory import LookupStatus, UserDirectoryService
from app.voice.session import caller_id_is_usable
from tests.test_directory_identity import AUTH, SIP, DirCall, _said, directory, make_call  # noqa: F401
from tests.test_multi_issue_calls import _session
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

MARIA, UNKNOWN = "+18455550201", "+18455550299"  # Maria is in the synthetic directory (test_directory_identity)


async def _tickets(db_session) -> list[Ticket]:
    db_session.expire_all()
    return list((await db_session.execute(select(Ticket))).scalars().all())


async def _hang_up(client, call_id):
    return await client.post(f"{SIP}/status", json={"call_id": call_id, "reason": "hangup"}, headers=AUTH)


# --- 1. which caller IDs are real numbers ------------------------------------------------------


@pytest.mark.parametrize("caller_id", [
    "", "   ", "anonymous", "Anonymous", " UNKNOWN ", "restricted", "private", "+266696687",
    "unavailable", "withheld", "anonymous@anonymous.invalid", "sip:anonymous@anonymous.invalid",
    "abc", "3000", "123456789", "+1 845 555", "0000000000", "+10000000000", "1111111111",
])
async def test_placeholders_and_malformed_values_are_never_a_usable_caller_number(caller_id):
    assert caller_id_is_usable(caller_id) is False


@pytest.mark.parametrize("caller_id", ["+18455550142", "18455550142", "8455550142", "(845) 555-0142", "+442079460958"])
async def test_a_real_number_in_any_common_form_is_usable(caller_id):
    assert caller_id_is_usable(caller_id) is True


# --- 1. when an abandoned call becomes a ticket ----------------------------------------------------


@pytest.mark.parametrize("from_number", ["", "anonymous", "unknown", "restricted", "private", "+266696687"])
async def test_a_call_dropped_with_no_usable_caller_id_and_no_number_given_makes_no_ticket(
    make_call, client, db_session, from_number
):
    call = make_call(f"gw-drop-{from_number or 'blank'}", from_number)
    await call.start()
    await call.report()

    response = await _hang_up(client, call.call_id)

    assert response.status_code == 204
    session = await _session(db_session, call.call_id)
    assert session.state == VoiceCallState.ABANDONED and session.ended_at is not None
    assert session.ticket_id is None
    assert await _tickets(db_session) == []  # "unknown" is not a number the helpdesk could call


async def test_a_call_dropped_with_a_usable_caller_id_is_still_salvaged_into_one_ticket(make_call, client, db_session, sent_emails):
    call = make_call("gw-drop-real", UNKNOWN)
    await call.start()
    await call.report()

    await _hang_up(client, call.call_id)
    await _hang_up(client, call.call_id)  # the gateway may report the hangup twice

    tickets = await _tickets(db_session)
    assert len(tickets) == 1
    assert tickets[0].phone_number == UNKNOWN and tickets[0].caller_number == UNKNOWN
    assert "INCOMPLETE VOICE INTAKE" in tickets[0].description


async def test_a_number_the_caller_said_is_enough_even_when_the_caller_id_was_withheld(make_call, client, db_session, sent_emails):
    call = make_call("gw-drop-spoken", "anonymous")
    await call.start()
    await call.report()
    session = await _session(db_session, call.call_id)
    session.collected = {**session.collected, "phone_number": "+18455550177", "phone_spoken": True}
    await db_session.commit()

    await _hang_up(client, call.call_id)

    tickets = await _tickets(db_session)
    assert len(tickets) == 1 and tickets[0].phone_number == "+18455550177"


@pytest.mark.parametrize("from_number", ["", "anonymous", UNKNOWN])
async def test_a_call_dropped_before_any_problem_was_described_makes_no_ticket(make_call, client, db_session, from_number):
    call = make_call(f"gw-nodesc-{from_number or 'blank'}", from_number)
    await call.start()

    await _hang_up(client, call.call_id)

    assert await _tickets(db_session) == []
    assert (await _session(db_session, call.call_id)).state == VoiceCallState.ABANDONED


# --- odd caller-ID formats must not break /start ----------------------------------------------------


@pytest.mark.parametrize("from_number", [
    "sip:+18455550142@voice.nextiva.example.test",  # a SIP URI instead of the bare number
    "+1 (845) 555-0142",
    "x" * 80,  # longer than the stored column
])
async def test_start_survives_whatever_caller_id_format_arrives(client, _call_fixture, from_number):
    response = await client.post(f"{SIP}/start", json={"call_id": "gw-format", "from_number": from_number}, headers=AUTH)

    assert response.status_code == 200 and response.json()["expect_reply"] is True


@pytest.mark.parametrize("from_number", ["x" * 80])
async def test_an_oversized_destination_number_does_not_break_start_either(client, _call_fixture, from_number):
    response = await client.post(
        f"{SIP}/start", json={"call_id": "gw-long-to", "from_number": "+18455550142", "to_number": from_number}, headers=AUTH
    )

    assert response.status_code == 200


async def test_a_sip_uri_caller_id_is_reduced_to_the_number_it_carries(make_call, db_session, directory):
    call = make_call("gw-uri", "sip:+18455550201@voice.nextiva.example.test")

    await call.start()

    session = await _session(db_session, "gw-uri")
    assert session.from_number == "+18455550201" and session.collected["phone_number"] == "+18455550201"
    assert session.collected["directory"]["status"] == "matched"  # the directory sees the number, not the URI


# --- 2. internal extensions --------------------------------------------------------------------------
#
# The gateway sends `from_number` and `to_number` only (StartCallRequest). Nothing in that tells an
# extension from a short telephone number, and a short caller ID is not guessed at: it is "not a usable
# number", so there is no lookup and the normal questions apply. Extension lookup itself works in the
# directory service and needs only a field that says "this is an extension" (docs/GATEWAY_COMPATIBILITY.md).


@pytest.mark.parametrize("short_id", ["2001", "x2001", "2001 ", "ext 2001"])
async def test_a_short_caller_id_is_not_taken_for_an_extension_so_nothing_is_looked_up(make_call, db_session, directory, short_id):
    call = make_call("gw-short", short_id)

    await call.start()
    asked = await call.report()

    session = await _session(db_session, "gw-short")
    assert directory.counts == {"phone": 0, "identifier": 0}
    assert "directory" not in session.collected and "phone_number" not in session.collected
    assert session.state == VoiceCallState.COLLECT_NAME and "directory entry" not in _said(asked)


async def test_the_directory_resolves_extensions_and_numbers_the_way_a_future_extension_field_would_need(directory):
    assert directory.find_by_extension("2001").entry.name == "Maria Example"  # a valid, unique extension
    assert directory.find_by_phone("+18455550201").entry.name == "Maria Example"  # a full telephone number
    assert directory.find_by_extension("9999").status is LookupStatus.NOT_FOUND  # unknown
    shared = directory.find_by_extension("2004")  # two records share it
    assert (shared.status, shared.candidate_count, shared.entry) == (LookupStatus.AMBIGUOUS, 2, None)
    assert directory.find_by_phone("+18455550204").status is LookupStatus.AMBIGUOUS  # and a shared telephone number


async def test_a_full_number_unknown_number_and_ambiguous_number_through_the_call(make_call, db_session, directory):
    expected = {"+18455550201": "matched", "+18455550299": "not_found", "+18455550204": "ambiguous"}
    for number, status in expected.items():
        call = make_call(f"gw-num-{number[-3:]}", number)
        await call.start()
        assert (await _session(db_session, call.call_id)).collected["directory"]["status"] == status


# --- 3. turn_id: the contract the gateway's strict mode expects --------------------------------------
#
# Implemented and tested in test_turn_idempotency.py / test_turn_contract.py (not in commit de7ceb0, which
# the gateway developer inspected: it is 20+ commits older). The check here is only that the contract is
# live on the routes the gateway calls.


async def test_the_turn_id_contract_is_live_on_the_gateways_routes(make_call, client, db_session, directory):
    call = make_call("gw-turnid", MARIA)
    await call.start()
    body = {"call_id": "gw-turnid", "utterance": "hello", "turn_id": "6b1f0c52-3a4e-4d0a-9d2c-5f6a9c7e1b10"}

    first = await client.post(f"{SIP}/turn", json=body, headers=AUTH)
    replay = await client.post(f"{SIP}/turn", json=body, headers=AUTH)
    conflict = await client.post(f"{SIP}/turn", json={**body, "utterance": "something else"}, headers=AUTH)

    assert first.json()["turn_id"] == body["turn_id"] and "Idempotent-Replayed" not in first.headers
    assert replay.json() == first.json() and replay.headers["Idempotent-Replayed"] == "true"
    assert (conflict.status_code, conflict.json()["detail"]["code"]) == (409, "turn_payload_conflict")


# --- 4. Phase 2 under repeated and concurrent requests ------------------------------------------------


async def test_simultaneous_starts_look_the_caller_up_once_and_leave_one_coherent_call(make_call, client, db_session, directory):
    call = make_call("gw-par-start", MARIA)

    results = await asyncio.gather(*(call.start() for _ in range(8)))

    assert all(r["expect_reply"] is True for r in results)
    assert directory.counts["phone"] == 1  # one lookup however many starts arrived together
    session = await _session(db_session, "gw-par-start")
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION
    assert [t["role"] for t in session.turns] == ["agent"]  # one greeting, not one per request
    rows = (await db_session.execute(select(func.count()).select_from(type(session)).where(type(session).call_id == "gw-par-start"))).scalar_one()
    assert rows == 1


async def test_a_start_that_arrives_after_the_caller_has_spoken_changes_nothing(make_call, client, db_session, directory):
    call = make_call("gw-late-start", MARIA)
    await call.start()
    await call.report()
    before = await _session(db_session, "gw-late-start")
    collected, turns, state = dict(before.collected), len(before.turns), before.state

    late = await call.start()

    after = await _session(db_session, "gw-late-start")
    assert (after.collected, len(after.turns), after.state) == (collected, turns, state)
    assert late["lines"] == [] and directory.counts["phone"] == 1


# --- 4. the dashboard's view of a call -----------------------------------------------------------------------


async def test_a_call_waiting_on_the_identity_question_shows_correctly_in_the_dashboard_api(make_call, client, db_session, directory):
    call = make_call("gw-dash", MARIA)
    await call.start()
    await call.report()
    stored = await _session(db_session, "gw-dash")

    detail = await client.get(f"/api/v1/voice-calls/{stored.id}")
    listing = await client.get("/api/v1/voice-calls", params={"state": "CONFIRM_IDENTITY"})

    assert detail.status_code == 200 and detail.json()["state"] == "CONFIRM_IDENTITY"
    assert detail.json()["collected"]["directory"]["status"] == "matched"
    assert detail.json()["caller_name"] is None  # nothing is shown as the caller's name until they confirm
    assert listing.status_code == 200 and [i["call_id"] for i in listing.json()["items"]] == ["gw-dash"]


async def test_a_confirmed_call_shows_the_confirmed_name_in_the_dashboard_api(make_call, client, db_session, directory, sent_emails):
    call = make_call("gw-dash-ok", MARIA)
    await call.start()
    await call.report()
    await call.turn("yes")
    stored = await _session(db_session, "gw-dash-ok")

    detail = (await client.get(f"/api/v1/voice-calls/{stored.id}")).json()

    assert detail["caller_name"] == "Maria Example" and detail["collected"]["identity_confirmed"] is True


async def test_a_second_start_arriving_while_the_first_is_still_starting_does_not_start_the_call_twice(
    make_call, client, db_session, directory, monkeypatch
):
    """Held deterministically: the first /start is parked inside start_call when the second arrives."""
    from app.voice import orchestrator

    real_start, entered, release, started = orchestrator.start_call, asyncio.Event(), asyncio.Event(), []

    async def parked_start_call(session, **kwargs):
        started.append(1)
        if len(started) == 1:
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=10)
        return await real_start(session, **kwargs)

    monkeypatch.setattr(orchestrator, "start_call", parked_start_call)
    call = make_call("gw-held-start", MARIA)

    first = asyncio.create_task(call.start())
    await asyncio.wait_for(entered.wait(), timeout=10)
    second = asyncio.create_task(call.start())
    await asyncio.sleep(0.3)  # long enough for an unserialized second request to get past the state check
    release.set()
    await asyncio.gather(first, second)

    assert len(started) == 1  # the second one saw a call that had already started
    assert directory.counts["phone"] == 1
    session = await _session(db_session, "gw-held-start")
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION and [t["role"] for t in session.turns] == ["agent"]

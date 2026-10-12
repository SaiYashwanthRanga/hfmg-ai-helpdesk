"""The /turn wire contract the SIP gateway codes against (docs/TURN_ID_CONTRACT.md).

These pin exact status codes, error strings, body shapes and headers, so changing any of them
fails here and is a deliberate decision. Synthetic data only; no external services.
"""

import asyncio
import time
import uuid

import pytest
from pydantic import ValidationError

from app.core.config import TURN_RESPONSE_MARGIN_SECONDS, Settings, get_settings
from app.voice import scripts
from tests.test_sip_routes import (
    AUTH,
    TURN_URL,
    _call_ready_to_file,
    _fail_create_ticket,
    _slow_create_ticket,
)
from tests.test_turn_idempotency import REPLAYED, _gateway_token, _turn, spies  # noqa: F401 (fixtures)

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

LEGACY_KEYS = {"lines", "expect_reply", "ticket_id", "stt_prompt"}


def _guid() -> str:
    return str(uuid.uuid4())


# --- success and replay ------------------------------------------------------------


async def test_a_successful_turn_echoes_the_turn_id_and_keeps_the_existing_fields(client, db_session):
    await _call_ready_to_file(client, db_session, "contract-ok")
    turn_id = _guid()

    response = await _turn(client, "contract-ok", turn_id)

    body = response.json()
    assert response.status_code == 200
    assert set(body) == LEGACY_KEYS | {"turn_id"}
    assert body["turn_id"] == turn_id
    assert body["ticket_id"] and body["lines"] and body["expect_reply"] is True
    assert REPLAYED not in response.headers


async def test_a_completed_duplicate_returns_the_identical_body_marked_as_a_replay(client, db_session, spies):
    await _call_ready_to_file(client, db_session, "contract-replay")
    turn_id = _guid()

    first = await _turn(client, "contract-replay", turn_id)
    duplicate = await _turn(client, "contract-replay", turn_id)

    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert duplicate.json()["turn_id"] == turn_id
    assert duplicate.headers[REPLAYED] == "true"
    assert spies["turns"] == 1 and len(spies["dispatched"]) == 1  # nothing re-run, nothing re-dispatched


@pytest.mark.parametrize("fmt", ["D", "D-upper", "N"])
async def test_the_guid_formats_a_gateway_can_produce_are_accepted_and_echoed_unchanged(client, db_session, fmt):
    await _call_ready_to_file(client, db_session, f"contract-fmt-{fmt}")
    guid = uuid.uuid4()
    turn_id = {"D": str(guid), "D-upper": str(guid).upper(), "N": guid.hex}[fmt]

    body = (await _turn(client, f"contract-fmt-{fmt}", turn_id)).json()

    assert body["turn_id"] == turn_id


async def test_an_escalated_turn_and_the_plain_error_line_also_echo_the_turn_id(client, db_session, monkeypatch):
    from app.services import ticket_service

    await _call_ready_to_file(client, db_session, "contract-escalated")
    real = ticket_service.create_ticket
    _fail_create_ticket(monkeypatch, times=1)
    escalated_id, error_id, unknown_id = _guid(), _guid(), _guid()

    escalated = await _turn(client, "contract-escalated", escalated_id)
    assert escalated.json()["turn_id"] == escalated_id and escalated.json()["ticket_id"]
    assert (await _turn(client, "contract-escalated", escalated_id)).json()["turn_id"] == escalated_id

    await _call_ready_to_file(client, db_session, "contract-error-line")
    _fail_create_ticket(monkeypatch, times=None)
    error_line = await _turn(client, "contract-error-line", error_id)
    assert error_line.json() == {
        "lines": [scripts.SYSTEM_ERROR], "expect_reply": False, "ticket_id": None, "stt_prompt": None,
        "turn_id": error_id,
    }
    monkeypatch.setattr(ticket_service, "create_ticket", real)

    unknown_call = await _turn(client, "contract-no-such-call", unknown_id)
    assert unknown_call.json()["turn_id"] == unknown_id and unknown_call.json()["lines"] == [scripts.SYSTEM_ERROR]


# --- duplicates still in progress -------------------------------------------------------


async def test_a_duplicate_of_a_turn_in_progress_gets_409_turn_in_progress_before_the_gateway_times_out(
    client, db_session, monkeypatch
):
    await _call_ready_to_file(client, db_session, "contract-busy")
    monkeypatch.setattr(settings, "voice_turn_lock_wait_seconds", 0.5)
    filing, release = asyncio.Event(), asyncio.Event()
    _slow_create_ticket(monkeypatch, 0, entered=filing, release=release)
    turn_id = _guid()

    original = asyncio.create_task(_turn(client, "contract-busy", turn_id))
    await asyncio.wait_for(filing.wait(), timeout=10)
    started = time.perf_counter()
    busy = await _turn(client, "contract-busy", turn_id)
    answered_after = time.perf_counter() - started
    release.set()
    await original

    assert busy.status_code == 409
    assert busy.json() == {
        "detail": {
            "code": "turn_in_progress",
            "message": "An earlier request for this call is still being processed. Retry with the same turn_id.",
        }
    }
    assert busy.headers["Retry-After"] == "1"
    assert REPLAYED not in busy.headers
    assert 0.4 <= answered_after < settings.voice_gateway_attempt_timeout_seconds - TURN_RESPONSE_MARGIN_SECONDS


async def test_the_default_lock_wait_leaves_the_gateway_time_to_receive_the_answer():
    defaults = Settings(_env_file=None)

    assert defaults.voice_gateway_attempt_timeout_seconds == 8.0
    assert 0 < defaults.voice_turn_lock_wait_seconds <= defaults.voice_gateway_attempt_timeout_seconds - TURN_RESPONSE_MARGIN_SECONDS


@pytest.mark.parametrize("wait", [8.0, 7.0, 6.1, 0, -1])
async def test_a_lock_wait_the_gateway_could_not_outlast_is_refused_at_startup(wait):
    with pytest.raises(ValidationError, match="VOICE_TURN_LOCK_WAIT_SECONDS"):
        Settings(_env_file=None, voice_turn_lock_wait_seconds=wait)


async def test_a_lock_wait_just_inside_the_limit_is_allowed():
    assert Settings(_env_file=None, voice_turn_lock_wait_seconds=6.0).voice_turn_lock_wait_seconds == 6.0


# --- conflicts ----------------------------------------------------------------------


async def test_the_same_turn_id_with_different_words_gets_409_turn_payload_conflict(client, db_session, spies):
    await _call_ready_to_file(client, db_session, "contract-conflict")
    turn_id = _guid()
    first = await _turn(client, "contract-conflict", turn_id)

    conflict = await _turn(client, "contract-conflict", turn_id, "something else entirely")

    assert conflict.status_code == 409
    assert conflict.json() == {
        "detail": {
            "code": "turn_payload_conflict",
            "message": "This turn_id was already used for a different utterance on this call.",
        }
    }
    assert REPLAYED not in conflict.headers and "Retry-After" not in conflict.headers
    assert spies["turns"] == 1
    assert (await _turn(client, "contract-conflict", turn_id)).json() == first.json()  # the real retry is unharmed


# --- legacy and malformed ----------------------------------------------------------------


async def test_a_request_without_a_turn_id_gets_exactly_the_old_body(client, db_session):
    await _call_ready_to_file(client, db_session, "contract-legacy")

    response = await _turn(client, "contract-legacy", None)

    assert response.status_code == 200
    assert set(response.json()) == LEGACY_KEYS  # no turn_id key at all
    assert REPLAYED not in response.headers
    unknown = await _turn(client, "contract-legacy-unknown", None)
    assert unknown.json() == {"lines": [scripts.SYSTEM_ERROR], "expect_reply": False, "ticket_id": None, "stt_prompt": None}


@pytest.mark.parametrize(
    "turn_id",
    [
        "{" + str(uuid.uuid4()) + "}",  # Guid.ToString("B")
        "(" + str(uuid.uuid4()) + ")",  # Guid.ToString("P")
        "",
        " ",
        "x" * 65,
        "has space",
        "a/b",
    ],
)
async def test_a_malformed_turn_id_gets_422_and_nothing_runs(client, db_session, spies, turn_id):
    await _call_ready_to_file(client, db_session, "contract-bad-id")

    response = await _turn(client, "contract-bad-id", turn_id)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)  # FastAPI's standard validation error
    assert spies["turns"] == 0


async def test_the_request_field_names_are_unchanged(client, db_session):
    """call_id, utterance, confidence (and the new turn_id) are the whole /turn request."""
    await _call_ready_to_file(client, db_session, "contract-fields")

    response = await client.post(
        TURN_URL,
        json={"call_id": "contract-fields", "utterance": "skip", "confidence": 0.93, "turn_id": _guid()},
        headers=AUTH,
    )

    assert response.status_code == 200


async def test_a_turn_saved_before_the_echo_existed_still_replays_with_the_turn_id(client, db_session):
    """A record written without turn_id in its saved body (an older row) is echoed on replay anyway."""
    from app.db.models import VoiceTurn
    from app.voice import turns
    from tests.test_sip_routes import _reload

    await _call_ready_to_file(client, db_session, "contract-old-row")
    call = await _reload(db_session, "contract-old-row")
    turn_id = _guid()
    db_session.add(
        VoiceTurn(
            session_id=call.id, turn_id=turn_id, fingerprint=turns.fingerprint("skip"),
            response={"lines": ["Saved earlier."], "expect_reply": True, "ticket_id": None, "stt_prompt": None},
        )
    )
    await db_session.commit()

    replay = await _turn(client, "contract-old-row", turn_id)

    assert replay.status_code == 200 and replay.headers[REPLAYED] == "true"
    assert replay.json() == {
        "lines": ["Saved earlier."], "expect_reply": True, "ticket_id": None, "stt_prompt": None, "turn_id": turn_id,
    }

"""AI Call Simulator API tests.

The model behind the voice NLU is the deterministic FakeProvider (keyword
rules, zero latency), so these exercise the real orchestrator, the real NLU
parsing and validation, and the simulator's tracing without an API key.
Speech is a stub: no audio ever leaves the test process.
"""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core import trace
from app.core.config import Settings, get_settings
from app.db.models import (
    Category,
    Priority,
    Ticket,
    TicketSource,
    VoiceCallSession,
    VoiceSimulatorTurn,
)
from app.llm.fake_provider import FakeProvider
from app.simulator import mock_callers, service
from app.speech.base import SpeechResult, SpeechStreamError, Transcription
from app.voice import nlu, orchestrator, twiml
from app.voice.nlu import VOICE_CATEGORIES

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
BASE = "/api/v1/voice-simulator"


@pytest.fixture(autouse=True)
def _simulator_on(monkeypatch):
    monkeypatch.setattr(settings, "enable_voice_simulator", True)
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "fake_llm_latency_ms", 0)
    monkeypatch.setattr(nlu, "get_provider", lambda: FakeProvider())


@pytest.fixture
async def categories(db_session):
    for name in VOICE_CATEGORIES:
        db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


class StubSpeech:
    name = "stub"

    def __init__(self, text="My printer is jammed and nobody can print", configured=True, fail=False, fail_tts=False):
        self.text = text
        self.configured = configured
        self.fail = fail
        self.fail_tts = fail_tts
        self.transcribed: list[tuple[int, str]] = []
        self.prompts: list[str | None] = []
        self.models: list[str | None] = []

    @property
    def is_configured(self):
        return self.configured

    async def transcribe(self, *, audio, mime_type, language, prompt=None, model=None):
        self.transcribed.append((len(audio), mime_type))
        self.prompts.append(prompt)
        self.models.append(model)
        if self.fail:
            return SpeechResult(error="provider down")
        return SpeechResult(transcription=Transcription(text=self.text, raw={"text": self.text}))

    async def synthesize(self, *, text, audio_format="mp3"):
        return SpeechResult(audio=b"ID3fake-mp3")

    async def synthesize_stream(self, *, text, audio_format="mp3"):
        if self.fail_tts:
            raise SpeechStreamError("tts down")
        yield b"ID3fake-"
        yield b"mp3"


async def _start(client, **body):
    response = await client.post(f"{BASE}/start", json={"tts": False, **body})
    assert response.status_code == 201, response.text
    return response.json()


async def _say(client, session_id, utterance, turn_client_id=None):
    response = await client.post(
        f"{BASE}/process",
        json={
            "session_id": session_id,
            "turn_client_id": str(turn_client_id or uuid.uuid4()),
            "utterance": utterance,
            "input_mode": "text",
        },
    )
    return response


async def _full_intake(client, **start):
    body = await _start(client, **start)
    sid = body["session"]["id"]
    for line in INTAKE_LINES:
        response = await _say(client, sid, line)
        assert response.status_code == 200, response.text
    return sid, response.json()


# One line per question: problem, when/can you work, name + department,
# callback number, email, email confirmation.
INTAKE_LINES = (
    "My Outlook won't open, it just spins",
    "It started this morning, but I can still work.",
    "This is Maria Lopez, billing",
    "8 4 5 5 5 5 0 1 4 2",
    "m l o p e z at hfmg dot net",
    "yes that's right",
)


# --- gating ---------------------------------------------------------------


async def test_every_route_is_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "enable_voice_simulator", False)
    assert (await client.get(f"{BASE}/config")).status_code == 404
    assert (await client.post(f"{BASE}/start", json={})).status_code == 404
    assert (await client.get(f"{BASE}/stats")).status_code == 404


async def test_routes_are_404_in_production_even_if_flag_flipped_at_runtime(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    assert (await client.post(f"{BASE}/start", json={})).status_code == 404


async def test_settings_refuse_simulator_in_production():
    with pytest.raises(ValidationError):
        Settings(enable_voice_simulator=True, environment="production", _env_file=None)
    assert Settings(enable_voice_simulator=True, environment="staging", _env_file=None).enable_voice_simulator


async def test_settings_status_reports_simulator_flag(client, monkeypatch):
    body = (await client.get("/api/v1/settings/status")).json()
    assert body["environment"]["voice_simulator_enabled"] is True
    monkeypatch.setattr(settings, "enable_voice_simulator", False)
    body = (await client.get("/api/v1/settings/status")).json()
    assert body["environment"]["voice_simulator_enabled"] is False


# --- conversation ---------------------------------------------------------


async def test_start_greets_and_creates_simulated_session(client, db_session, categories):
    body = await _start(client, label="QA")
    assert body["greeting"]["text"].startswith("Thank you for calling Horizon Family Medical Group")
    assert body["greeting"]["audio"] is None  # tts disabled
    assert body["session"]["state"] == "COLLECT_DESCRIPTION"
    assert body["turn"]["intent"] == "greeting"

    call = await db_session.get(VoiceCallSession, uuid.UUID(body["session"]["id"]))
    assert call.is_simulated is True
    assert call.twilio_call_sid.startswith("SIM-")


async def test_full_intake_creates_isolated_simulator_ticket(client, db_session, categories):
    sid, last = await _full_intake(client)

    session = last["session"]
    assert session["state"] == "ANYTHING_ELSE"
    assert session["collected"]["caller_name"] == "Maria Lopez"
    assert session["collected"]["email"] == "mlopez@hfmg.net"
    assert session["collected"]["category"] == "Microsoft 365"
    ticket = session["ticket"]
    assert ticket["ticket_number"].startswith("SIM-")
    assert ticket["category"] == "Microsoft 365"

    turn = last["turn"]
    assert turn["intent"] == "confirm"
    assert turn["ticket_payload"]["source"] == "SIMULATOR"
    assert turn["ticket_payload"]["caller_name"] == "Maria Lopez"
    assert turn["timings"]["ticket_create_ms"] is not None
    assert [c["schema_name"] for c in turn["llm_trace"]] == ["record_answer"]
    assert "Your ticket number is" in last["reply"]["text"]

    db_ticket = (await db_session.execute(select(Ticket))).scalar_one()
    assert db_ticket.source == TicketSource.SIMULATOR

    detail = (await client.get(f"{BASE}/session/{sid}")).json()
    intents = [t["intent"] for t in detail["turns"]]
    assert intents == [
        "greeting", "report_issue", "provide_details", "provide_name", "provide_phone", "provide_email", "confirm",
    ]
    assert session["collected"]["department"] == "Billing"  # normalized to the department list
    assert session["collected"]["started"] == "this morning"
    assert session["collected"]["work_blocked"] is False

    final = await _say(client, sid, "no that's all")
    assert final.json()["reply"]["call_ended"] is True
    assert final.json()["session"]["state"] == "COMPLETED"
    assert final.json()["session"]["end_reason"] == "agent_hangup"


async def test_simulator_data_never_reaches_operational_views(client, db_session, categories):
    await _full_intake(client)
    other = (await db_session.execute(select(Category).where(Category.name == "Other"))).scalar_one()
    created = await client.post(
        "/api/v1/tickets",
        json={"caller_name": "Real", "phone_number": "+18455550100", "category_id": str(other.id),
              "description": "real ticket"},
    )
    assert created.status_code == 201

    tickets = (await client.get("/api/v1/tickets")).json()
    assert [t["caller_name"] for t in tickets["items"]] == ["Real"]
    simulated = (await client.get("/api/v1/tickets", params={"source": "SIMULATOR"})).json()
    assert simulated["total"] == 1

    assert (await client.get("/api/v1/voice-calls")).json()["total"] == 0
    assert (await client.get("/api/v1/voice-calls/summary")).json()["active"] == 0

    kpis = (await client.get("/api/v1/analytics/kpis")).json()
    assert kpis["tickets_today"]["value"] == 1
    assert kpis["open_tickets"]["value"] == 1
    assert kpis["calls_today"]["value"] == 0

    sources = (await client.get("/api/v1/analytics/tickets-by-source")).json()["items"]
    assert "SIMULATOR" not in {s["source"] for s in sources}
    recent = (await client.get("/api/v1/analytics/recent-activity")).json()
    assert all(not item["ticket_number"].startswith("SIM-") for item in recent["items"])
    by_category = (await client.get("/api/v1/analytics/tickets-by-category")).json()["items"]
    assert sum(c["count"] for c in by_category) == 1


async def test_simulated_session_is_hidden_from_voice_call_detail(client, categories):
    body = await _start(client)
    assert (await client.get(f"/api/v1/voice-calls/{body['session']['id']}")).status_code == 404


async def test_caller_id_skips_phone_question(client, categories):
    body = await _start(client, caller_id="+18455550142")
    sid = body["session"]["id"]
    await _say(client, sid, "The VPN won't connect since this morning and I can't do anything")
    response = await _say(client, sid, "This is Tom Reilly, finance")
    assert response.json()["session"]["state"] == "COLLECT_EMAIL"
    assert response.json()["session"]["caller_id"] == "+18455550142"


async def test_request_for_human_escalates_and_ends_call(client, categories):
    sid = (await _start(client))["session"]["id"]
    response = (await _say(client, sid, "I want to talk to a real person")).json()
    assert response["turn"]["intent"] == "request_human"
    # No caller ID: the agent first asks where to call back.
    assert response["session"]["state"] == "COLLECT_PHONE"
    assert response["reply"]["call_ended"] is False

    response = (await _say(client, sid, "8 4 5 5 5 5 0 1 4 2")).json()
    assert response["session"]["escalated"] is True
    assert response["session"]["collected"]["phone_number"] == "+18455550142"
    assert response["session"]["escalation_reason"] == "CALLER_REQUESTED"
    assert response["reply"]["call_ended"] is True
    assert response["session"]["ticket"]["ticket_number"].startswith("SIM-")

    again = await _say(client, sid, "hello?")
    assert again.status_code == 409


async def test_unclear_turn_is_labelled_and_counted(client, categories):
    sid = (await _start(client))["session"]["id"]
    response = (await _say(client, sid, "um")).json()
    assert response["turn"]["intent"] == "unclear"
    assert response["session"]["misunderstanding_count"] == 1


async def test_retrying_a_turn_is_idempotent(client, categories):
    sid = (await _start(client))["session"]["id"]
    turn_id = uuid.uuid4()
    first = (await _say(client, sid, "um", turn_client_id=turn_id)).json()
    second = (await _say(client, sid, "um", turn_client_id=turn_id)).json()
    assert first["turn"]["id"] == second["turn"]["id"]
    assert second["session"]["misunderstanding_count"] == 1


async def test_turn_id_from_another_session_is_rejected(client, categories):
    a = (await _start(client))["session"]["id"]
    b = (await _start(client))["session"]["id"]
    turn_id = uuid.uuid4()
    assert (await _say(client, a, "um", turn_client_id=turn_id)).status_code == 200
    assert (await _say(client, b, "um", turn_client_id=turn_id)).status_code == 409


async def test_agent_crash_escalates_with_system_error(client, categories, monkeypatch):
    sid = (await _start(client))["session"]["id"]

    async def boom(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(orchestrator, "handle_turn", boom)
    response = (await _say(client, sid, "My printer is jammed")).json()
    assert response["session"]["escalation_reason"] == "SYSTEM_ERROR"
    assert response["turn"]["intent"] == "system_error"
    assert response["turn"]["errors"][0]["stage"] == "agent"
    assert response["reply"]["call_ended"] is True

    detail = (await client.get(f"{BASE}/session/{sid}")).json()
    assert detail["turns"][-1]["utterance"] == "My printer is jammed"


async def test_ticket_creation_failure_ends_the_call_instead_of_a_500(client):
    # Reproduces a real failure: no categories seeded, so creating the ticket
    # raises -- and so does the SYSTEM_ERROR escalation, which also creates a
    # ticket. That used to escape as a 500 (seen in the browser as "Failed to
    # fetch"); now the caller hears the phone path's system-error line.
    body = await _start(client)
    sid = body["session"]["id"]
    last = None
    for line in INTAKE_LINES:
        last = await _say(client, sid, line)
        assert last.status_code == 200, last.text

    turn = last.json()["turn"]
    assert turn["intent"] == "system_error"
    assert [e["stage"] for e in turn["errors"]] == ["agent", "agent"]
    assert last.json()["reply"]["call_ended"] is True
    assert "sorry" in last.json()["reply"]["text"].lower()
    session = last.json()["session"]
    assert session["state"] == "ABANDONED" and session["end_reason"] == "error"
    assert session["ticket"] is None

    detail = (await client.get(f"{BASE}/session/{sid}")).json()
    assert detail["turns"][-1]["utterance"] == "yes that's right"
    assert detail["turns"][-1]["status"] == "completed"


async def test_llm_failures_are_recorded_as_turn_errors(client, categories, monkeypatch):
    class Down(FakeProvider):
        async def structured(self, **kwargs):
            trace.record_llm_call(
                kind="structured", schema_name=kwargs["schema_name"], started=0.0, system="", user="",
                output=None, error="timeout after 4.0s", attempts=2,
            )
            return None

    monkeypatch.setattr(nlu, "get_provider", lambda: Down())
    sid = (await _start(client))["session"]["id"]
    response = (await _say(client, sid, "My printer is jammed")).json()
    assert response["turn"]["errors"] == [
        {"stage": "llm", "type": "record_issue", "message": "timeout after 4.0s"}
    ]
    assert response["turn"]["intent"] == "unclear"


async def test_turn_limit(client, categories, monkeypatch):
    monkeypatch.setattr(settings, "simulator_max_turns_per_session", 2)
    monkeypatch.setattr(settings, "voice_max_misunderstandings", 99)
    sid = (await _start(client))["session"]["id"]
    assert (await _say(client, sid, "um")).status_code == 200  # greeting + 1 = 2 completed
    assert (await _say(client, sid, "um")).status_code == 422


async def test_concurrent_session_cap(client, categories, monkeypatch):
    monkeypatch.setattr(settings, "simulator_max_concurrent_sessions", 1)
    await _start(client)
    response = await client.post(f"{BASE}/start", json={"tts": False})
    assert response.status_code == 409


async def test_hourly_session_cap(client, categories, monkeypatch):
    monkeypatch.setattr(settings, "simulator_max_sessions_per_hour", 1)
    sid = (await _start(client))["session"]["id"]
    await client.post(f"{BASE}/end", json={"session_id": sid})
    assert (await client.post(f"{BASE}/start", json={"tts": False})).status_code == 429


async def test_idle_sessions_are_swept_on_start(client, db_session, categories, monkeypatch):
    sid = (await _start(client))["session"]["id"]
    monkeypatch.setattr(settings, "simulator_session_idle_timeout_seconds", -1)
    await _start(client)
    detail = (await client.get(f"{BASE}/session/{sid}")).json()
    assert detail["session"]["state"] == "ABANDONED"
    assert detail["session"]["end_reason"] == "idle"
    assert detail["session"]["ticket"] is None


async def test_unknown_session_is_404(client):
    missing = uuid.uuid4()
    assert (await client.get(f"{BASE}/session/{missing}")).status_code == 404
    assert (await _say(client, str(missing), "hi")).status_code == 404


# --- hang-up --------------------------------------------------------------


async def test_hangup_mid_intake_salvages_a_ticket(client, categories):
    sid = (await _start(client, caller_id="+18455550142"))["session"]["id"]
    await _say(client, sid, "The label printer in the lab stopped working")
    ended = (await client.post(f"{BASE}/end", json={"session_id": sid, "reason": "user_hangup"})).json()
    assert ended["state"] == "ABANDONED"
    assert ended["ticket"]["ticket_number"].startswith("SIM-")


async def test_clearing_a_session_never_salvages(client, categories):
    sid = (await _start(client, caller_id="+18455550142"))["session"]["id"]
    await _say(client, sid, "The label printer in the lab stopped working")
    ended = (await client.post(f"{BASE}/end", json={"session_id": sid, "reason": "cleared"})).json()
    assert ended["ticket"] is None
    assert ended["end_reason"] == "cleared"


async def test_end_is_idempotent(client, categories):
    sid = (await _start(client))["session"]["id"]
    first = (await client.post(f"{BASE}/end", json={"session_id": sid})).json()
    second = (await client.post(f"{BASE}/end", json={"session_id": sid})).json()
    assert first["ended_at"] == second["ended_at"]


async def test_notifications_are_opt_in(client, categories, monkeypatch):
    sent = []

    async def fake_send(ticket, event):
        sent.append(ticket.ticket_number)

    monkeypatch.setattr(service, "send_ticket_notification", fake_send)
    monkeypatch.setattr(settings, "simulator_allow_notifications", True)
    await _full_intake(client)
    assert sent == []
    await _full_intake(client, send_notifications=True)
    assert len(sent) == 1 and sent[0].startswith("SIM-")


async def test_notifications_are_refused_unless_the_server_allows_them(client, categories):
    # Default: SIMULATOR_ALLOW_NOTIFICATIONS=false, so nobody can use the
    # simulator to flood the help desk inbox.
    response = await client.post(f"{BASE}/start", json={"tts": False, "send_notifications": True})
    assert response.status_code == 422
    assert (await client.get(f"{BASE}/config")).json()["notifications_allowed"] is False


async def test_simulator_cannot_touch_a_real_call(client, db_session, categories):
    real = VoiceCallSession(
        twilio_call_sid="CA-real-1", from_number="+18455550100", to_number="+18455559999",
        state="COLLECT_NAME", collected={}, turns=[],
    )
    db_session.add(real)
    await db_session.commit()
    assert (await client.get(f"{BASE}/session/{real.id}")).status_code == 404
    assert (await _say(client, str(real.id), "hello")).status_code == 404
    assert (await client.post(f"{BASE}/end", json={"session_id": str(real.id)})).status_code == 404
    await db_session.refresh(real)
    assert real.state.value == "COLLECT_NAME" and real.ended_at is None


# --- audio ----------------------------------------------------------------


async def _upload(client, sid, turn_id, data=b"\x1aE\xdf\xa3webm", content_type="audio/webm;codecs=opus"):
    return await client.post(
        f"{BASE}/audio",
        data={"session_id": sid, "turn_client_id": str(turn_id)},
        files={"audio": ("utterance.webm", data, content_type)},
    )


async def test_audio_is_transcribed_then_processed(client, db_session, categories, monkeypatch):
    stub = StubSpeech()
    monkeypatch.setattr(service, "get_speech_provider", lambda: stub)
    sid = (await _start(client, tts=True))["session"]["id"]

    turn_id = uuid.uuid4()
    audio = (await _upload(client, sid, turn_id)).json()
    assert audio["transcript"] == stub.text
    assert audio["empty"] is False
    assert audio["timings"]["stt_ms"] is not None
    assert stub.transcribed == [(8, "audio/webm;codecs=opus")]

    response = await client.post(
        f"{BASE}/process",
        json={"session_id": sid, "turn_client_id": str(turn_id), "input_mode": "voice"},
    )
    body = response.json()
    assert body["turn"]["utterance"] == stub.text
    assert body["turn"]["input_mode"] == "voice"
    assert body["turn"]["timings"]["stt_ms"] is not None
    assert body["session"]["collected"]["category"] == "Printer"
    # Recognition was told what the answer probably looks like.
    assert "HFMG IT help desk" in stub.prompts[0]

    # Speech is streamed from speech_path, not inlined; first-audio time is recorded.
    assert body["reply"]["audio"] is None
    speech = await client.get(f"/api/v1{body['reply']['speech_path']}")
    assert speech.status_code == 200
    assert speech.headers["content-type"] == "audio/mpeg"
    assert speech.content == b"ID3fake-mp3"
    metrics = (await client.get(f"{BASE}/metrics/{sid}")).json()
    assert metrics["turns"][-1]["tts_ms"] is not None

    # Only text and metadata are persisted -- no column holds audio bytes.
    row = (await db_session.execute(select(VoiceSimulatorTurn).where(VoiceSimulatorTurn.turn_client_id == turn_id))).scalar_one()
    assert b"webm" not in repr(row.stt_raw).encode()


async def test_speech_failure_is_a_502_and_recorded_on_the_turn(client, categories, monkeypatch):
    monkeypatch.setattr(service, "get_speech_provider", lambda: StubSpeech(fail_tts=True))
    body = await _start(client, tts=True)
    greeting_path = body["greeting"]["speech_path"]
    assert greeting_path
    response = await client.get(f"/api/v1{greeting_path}")
    assert response.status_code == 502
    detail = (await client.get(f"{BASE}/session/{body['session']['id']}")).json()
    assert detail["turns"][0]["errors"][0]["stage"] == "tts"


async def test_no_speech_path_when_tts_is_off(client, categories, monkeypatch):
    monkeypatch.setattr(service, "get_speech_provider", lambda: StubSpeech())
    body = await _start(client, tts=False)
    assert body["greeting"]["speech_path"] is None
    # And a turn id from a TTS-off session never streams.
    response = await client.get(f"{BASE}/speech/{body['turn']['id']}")
    assert response.status_code == 404


async def test_stt_context_can_be_turned_off(client, categories, monkeypatch):
    stub = StubSpeech()
    monkeypatch.setattr(service, "get_speech_provider", lambda: stub)
    monkeypatch.setattr(settings, "speech_stt_context", False)
    sid = (await _start(client))["session"]["id"]
    await _upload(client, sid, uuid.uuid4())
    assert stub.prompts == [None]


async def test_corrected_transcript_overrides_stt(client, categories, monkeypatch):
    monkeypatch.setattr(service, "get_speech_provider", lambda: StubSpeech(text="my pinter is jamed"))
    sid = (await _start(client))["session"]["id"]
    turn_id = uuid.uuid4()
    await _upload(client, sid, turn_id)
    body = (await _say(client, sid, "My printer is jammed", turn_client_id=turn_id)).json()
    assert body["turn"]["utterance"] == "My printer is jammed"
    assert body["turn"]["input_mode"] == "voice"


async def test_stt_failure_becomes_an_empty_turn_not_an_error_response(client, categories, monkeypatch):
    monkeypatch.setattr(service, "get_speech_provider", lambda: StubSpeech(fail=True))
    sid = (await _start(client))["session"]["id"]
    turn_id = uuid.uuid4()
    audio = (await _upload(client, sid, turn_id)).json()
    assert audio["empty"] is True
    assert audio["error"] == "provider down"

    body = (
        await client.post(f"{BASE}/process", json={"session_id": sid, "turn_client_id": str(turn_id), "input_mode": "voice"})
    ).json()
    assert body["turn"]["intent"] == "unclear"
    assert body["turn"]["errors"][0]["stage"] == "stt"


async def test_audio_limits(client, categories, monkeypatch):
    monkeypatch.setattr(service, "get_speech_provider", lambda: StubSpeech())
    monkeypatch.setattr(settings, "simulator_max_audio_bytes", 10)
    sid = (await _start(client))["session"]["id"]
    assert (await _upload(client, sid, uuid.uuid4(), data=b"x" * 11)).status_code == 413
    assert (await _upload(client, sid, uuid.uuid4(), content_type="text/plain")).status_code == 415
    assert (await _upload(client, sid, uuid.uuid4(), data=b"")).status_code == 422

    monkeypatch.setattr(service, "get_speech_provider", lambda: StubSpeech(configured=False))
    assert (await _upload(client, sid, uuid.uuid4())).status_code == 503


async def test_oversized_upload_is_refused_before_parsing(client, categories, monkeypatch):
    monkeypatch.setattr(settings, "simulator_max_audio_bytes", 10)
    sid = (await _start(client))["session"]["id"]
    # Claimed size far beyond the cap: the middleware answers from the header alone.
    response = await client.post(
        f"{BASE}/audio",
        content=b"x",
        headers={"content-type": "multipart/form-data; boundary=x", "content-length": str(10_000_000)},
    )
    assert response.status_code == 413
    assert sid


# --- metrics --------------------------------------------------------------


async def test_metrics_and_client_metrics(client, categories):
    sid = (await _start(client))["session"]["id"]
    turn_id = uuid.uuid4()
    await _say(client, sid, "My printer is jammed", turn_client_id=turn_id)

    timings = (
        await client.post(
            f"{BASE}/session/{sid}/client-metrics",
            json={"turn_client_id": str(turn_id), "capture_ms": 812.5, "turn_total_ms": 1500, "playback_start_ms": 40},
        )
    ).json()
    assert timings["capture_ms"] == 812.5

    metrics = (await client.get(f"{BASE}/metrics/{sid}")).json()
    assert len(metrics["turns"]) == 2
    assert metrics["stats"]["turn_total_ms"] == {"current": 1500.0, "avg": 1500.0, "max": 1500.0, "p95": 1500.0, "n": 1}
    assert metrics["stats"]["llm_ms"]["n"] == 2

    bad = await client.post(
        f"{BASE}/session/{sid}/client-metrics", json={"turn_client_id": str(uuid.uuid4()), "capture_ms": 1}
    )
    assert bad.status_code == 404


async def test_stats_roll_up(client, categories, monkeypatch):
    await _full_intake(client)
    sid = (await _start(client))["session"]["id"]

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(orchestrator, "handle_turn", boom)
    await _say(client, sid, "anything")

    stats = (await client.get(f"{BASE}/stats", params={"hours": 1})).json()
    assert stats["sessions_total"] == 2
    assert stats["sessions_active"] == 1
    assert stats["tickets_created"] == 2
    assert stats["sessions_escalated"] == 1
    assert stats["turns_total"] == len(INTAKE_LINES) + 1
    assert stats["turns_failed"] == 1
    assert stats["failure_rate"] == pytest.approx(1 / (len(INTAKE_LINES) + 1), abs=1e-4)
    assert stats["stages"]["llm_ms"]["n"] == len(INTAKE_LINES) + 1


# --- mock callers ---------------------------------------------------------


async def test_mock_callers_are_deterministic(client):
    a = (await client.get(f"{BASE}/mock-caller", params={"seed": 7})).json()
    b = (await client.get(f"{BASE}/mock-caller", params={"seed": 7})).json()
    assert a == b
    assert set(a["answers"]) >= {"COLLECT_DESCRIPTION", "COLLECT_NAME", "COLLECT_EMAIL", "ANYTHING_ELSE"}

    issue = (await client.get(f"{BASE}/random-issue", params={"seed": 3, "category": "Printer"})).json()
    assert issue["category"] == "Printer"
    assert (await client.get(f"{BASE}/random-issue", params={"seed": 3, "category": "Fax"})).status_code == 422


async def test_mock_caller_bank_only_uses_voice_categories():
    assert {i.category for i in mock_callers.ISSUES} == set(VOICE_CATEGORIES)


async def test_mock_caller_drives_a_full_call(client, categories):
    caller = (await client.get(f"{BASE}/mock-caller", params={"seed": 11, "category": "Network"})).json()
    body = await _start(client)
    sid, state = body["session"]["id"], body["session"]["state"]
    ended = False
    for _ in range(12):
        response = (await _say(client, sid, caller["answers"][state])).json()
        state, ended = response["session"]["state"], response["reply"]["call_ended"]
        if ended:
            break
    assert ended
    assert response["session"]["ticket"] is not None
    assert response["session"]["collected"]["category"] == caller["expect"]["category"]


# --- building blocks ------------------------------------------------------


async def test_spoken_text_unescapes_twiml():
    body = twiml.say_and_hangup("Thanks, O'Brien & co. <3")
    assert twiml.spoken_text(body) == "Thanks, O'Brien & co. <3"
    assert twiml.ends_call(body)
    assert not twiml.ends_call(twiml.ask("Name?"))


async def test_trace_is_a_no_op_without_collect():
    trace.record_llm_call(
        kind="text", schema_name=None, started=0.0, system="", user="", output="x", error=None, attempts=1
    )
    with trace.span("ignored"):
        pass
    assert trace.active() is None

    with trace.collect() as tr:
        with trace.span("inside", data={"k": 1}):
            pass
    assert tr.span_named("inside").data == {"k": 1}


async def test_simulator_ticket_numbers_are_namespaced():
    from app.services.ticket_service import _simulator_ticket_number

    number = _simulator_ticket_number()
    assert number.startswith("SIM-") and len(number) <= 32
    assert number != _simulator_ticket_number()

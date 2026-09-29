# AI Call Simulator — Guide

**Status:** Implemented on branch `feature/voice-simulator`, 2026-09-28. Not yet deployed anywhere.
**Audience:** engineers, QA, and whoever enables it in a test environment.
**Related:** [VOICE_SIMULATOR_DESIGN.md](VOICE_SIMULATOR_DESIGN.md) (design and the deviations made while building it), [security review](docs/reviews/VOICE_SIMULATOR_SECURITY_REVIEW.md), [performance report](docs/reviews/VOICE_SIMULATOR_PERFORMANCE.md), [completion report and readiness assessment](docs/reviews/VOICE_SIMULATOR_COMPLETION_REPORT.md).

The simulator is a dashboard page, `/voice-simulator`, where you "phone" the help desk's voice agent from a browser. You talk into the microphone, or type, and the agent answers aloud. Every step is timed, and every turn can be inspected. No Twilio account or phone line is involved.

It is **off by default** and **cannot be turned on in production**.

---

## 1. Architecture summary

```
Browser (/voice-simulator)                         FastAPI (/api/v1/voice-simulator)
─────────────────────────                          ─────────────────────────────────
mic → MediaRecorder ──audio──▶ POST /audio ──▶ SpeechProvider.transcribe   (OpenAI STT)
typed / mock text ──────────▶ POST /process ─▶ voice.orchestrator.handle_turn  ◀── the production agent
                                                 ├─ voice.nlu → LLMProvider   (same prompts, timeouts)
                                                 └─ ticket_service.create_ticket(source=SIMULATOR)
speaker ◀── mp3 (base64) ◀──────────────────── SpeechProvider.synthesize   (OpenAI TTS)
latency panel ◀── GET /metrics, POST /client-metrics
```

**One agent.** The simulator adds no conversation logic. It calls the same `orchestrator.handle_turn()` that answers Twilio webhooks, and turns the TwiML reply back into plain text (`twiml.spoken_text`). Classification, priority, escalation, retries, ticket creation and abandoned-call salvage all run the production code.

**What is new:**

| Piece | Where | Why |
|---|---|---|
| Speech-to-text / text-to-speech | `backend/app/speech/` | On the phone path Twilio does both, so nothing existed to reuse |
| Request-scoped trace | `backend/app/core/trace.py` | Captures every model call, its output, timing, and the ticket payload, without changing any production signature. It does nothing when no trace is active |
| Simulator service and API | `backend/app/simulator/` | Session lifecycle, timing, idempotency, limits, mock callers, metrics |
| Fake LLM provider | `backend/app/llm/fake_provider.py` | `LLM_PROVIDER=fake`, which answers with keyword rules. Used for load tests and demos; refused in production |
| Page | `frontend/src/pages/VoiceSimulatorPage.tsx` and `components/voiceSimulator/`, `hooks/voiceSimulator/`, `lib/voiceSimulator/` | Lazy-loaded into its own chunk |

**Isolation.** Simulated calls are ordinary `voice_call_sessions` rows with `is_simulated = true`, and their tickets have `source = SIMULATOR` and `SIM-2026-XXXXXXXX` numbers. Every operational query excludes both:

- the ticket queue (unless you filter to Simulator explicitly)
- the Calls page and call summary
- every dashboard KPI and analytics chart
- AI Insights

### Sequence: one voice turn

```mermaid
sequenceDiagram
    autonumber
    actor T as Tester
    participant B as Browser
    participant A as /voice-simulator API
    participant S as SpeechProvider
    participant O as orchestrator.handle_turn
    participant DB as Postgres
    T->>B: speaks (push-to-talk release, or VAD detects a pause)
    B->>A: POST /audio (blob, turn_client_id)
    A->>S: transcribe — no DB connection held
    A->>DB: store transcript (status=transcribed)
    A-->>B: transcript, stt_ms
    B->>A: POST /process (turn_client_id)
    A->>DB: lock session row (queue_wait_ms)
    A->>O: handle_turn inside trace.collect()
    O->>DB: ticket_service.create_ticket(source=SIMULATOR) when intake completes
    A->>DB: commit turn (llm_ms, trace, intent)
    A->>S: synthesize reply — after commit
    A-->>B: turn, reply (+ mp3), session
    B->>T: plays reply; first frame → turn_total_ms
    B-)A: POST /session/{id}/client-metrics
```

The error paths and the load test sequence are in [VOICE_SIMULATOR_DESIGN.md](VOICE_SIMULATOR_DESIGN.md) §6. They match the implementation, except that TTS now runs after the commit (see the design's §11).

---

## 2. API reference

Base path `/api/v1/voice-simulator`. Every route returns **404** unless `ENABLE_VOICE_SIMULATOR=true` and `ENVIRONMENT` is not `production`. Errors use the app's usual `{"detail": "..."}`. Request and response models are in `backend/app/simulator/schemas.py`, and the TypeScript mirror is `frontend/src/types/voiceSimulator.ts`.

| Method & path | Purpose | Notable responses |
|---|---|---|
| `GET /config` | What the server supports: speech configured, LLM configured, models, limits, voice categories, whether ticket email is allowed | |
| `POST /start` | Create a session and return the greeting (`session`, `greeting`, `turn`). Body: `caller_id?`, `tts`, `label?`, `send_notifications` | `409` concurrent cap, `429` hourly cap, `422` if `send_notifications` is requested but not allowed |
| `POST /audio` | Multipart `session_id`, `turn_client_id`, `audio`. Speech-to-text only; the audio is discarded after the call | `413` over the size cap (checked from `Content-Length` before parsing, then again on the bytes), `415` type, `422` empty, `503` speech not configured. A provider failure returns **200** with `empty: true` and `error` |
| `POST /process` | Run one agent turn. `utterance` omitted = use the `/audio` transcript; given = override it (fix a mis-transcription). Returns as soon as the agent has decided what to say; `reply.speech_path` points at the spoken reply | `409` call ended / turn in progress / id belongs to another session, `422` turn cap. Repeating a `turn_client_id` returns the stored turn without re-running it |
| `GET /speech/{turn_id}` | The turn's spoken reply, **streamed** as it is synthesized (`audio/mpeg`), so playback starts on the first bytes. Repeated lines come from an in-memory cache. Records time-to-first-audio as the turn's `tts_ms` | `404` unknown turn or TTS off for the session, `502` provider produced no audio |
| `POST /end` | Hang up. `reason`: `user_hangup` (salvages a ticket if a problem and callback number were collected, like the phone path) or `cleared` (never salvages) | Idempotent |
| `GET /session/{id}` | Session plus every turn: transcript, intent, slots after the turn, ticket payload, LLM trace, errors, timings | `404` for unknown ids **and for real Twilio calls** |
| `GET /metrics/{id}` | Per-turn timings, plus current / avg / p95 / max per stage | |
| `POST /session/{id}/client-metrics` | Browser-measured capture, playback and total-turn times for one turn | Best-effort; the page never waits on it |
| `GET /stats?hours=24` | Observability roll-up across all simulated sessions: session counts, active sessions, tickets, turns, failure rate, per-stage latency | |
| `GET /mock-caller?seed=&category=&escalate=` | A scripted caller with an answer for every agent state, plus what a correct agent should conclude | Deterministic per seed |
| `GET /random-issue?seed=&category=` | One utterance from the curated issue bank | `422` unknown category |

`GET /settings/status` also gained `environment.voice_simulator_enabled`, which the sidebar uses to show or hide the "Call Simulator" entry.

**Stage definitions** (all milliseconds; server and browser clocks are never compared):

| Field | Measured by | From → to |
|---|---|---|
| `capture_ms` | browser | caller stops talking → `/audio` request sent |
| `stt_ms` | server | transcription call |
| `queue_wait_ms` | server | waiting for a DB connection and the session row lock |
| `llm_ms` | server | the whole `handle_turn` (every NLU call, DB work, ticket creation) |
| `ticket_create_ms` | server | `ticket_service.create_ticket`, on the turn that created a ticket |
| `tts_ms` | server | time to the first audio byte of the streamed reply (was: whole synthesis, before the latency work) |
| `server_total_ms` | server | the whole `/process` request |
| `playback_start_ms` | browser | reply received → first audio frame |
| `turn_total_ms` | browser | caller stops talking → first agent audio. **How long the caller waits in silence** |

---

## 3. Deployment guide

The simulator is for development and test environments. The steps below enable it; skip them for production.

1. **Migrate.** `alembic upgrade head` applies `e41f7a2c9b10_add_voice_simulator`. It adds the `SIMULATOR` ticket source, `voice_call_sessions.is_simulated`, `voice_simulator_sessions` and `voice_simulator_turns`. The migration runs safely whether or not the feature is enabled.
2. **Configure** `backend/.env` (full list in `backend/.env.example`):
   ```ini
   ENVIRONMENT=staging                  # anything but "production"
   ENABLE_VOICE_SIMULATOR=true
   OPENAI_API_KEY=sk-...                # NLU; also STT/TTS unless you only type
   # Optional
   SPEECH_STT_MODEL=gpt-4o-mini-transcribe
   SPEECH_TTS_MODEL=gpt-4o-mini-tts
   SPEECH_TTS_VOICE=alloy
   SIMULATOR_ALLOW_NOTIFICATIONS=false  # keep false unless you are testing the email itself
   ```
   To run without any OpenAI key (demos, load tests), set `LLM_PROVIDER=fake`. You can then type turns but not speak them.

   Voice defaults were chosen by measurement ([latency and accuracy report](docs/reviews/VOICE_LATENCY_ACCURACY_REPORT.md)): `VOICE_NLU_MODEL=gpt-4.1-mini`, `SPEECH_TTS_MODEL=tts-1` (streamed mp3), `SPEECH_STT_MODEL=gpt-4o-mini-transcribe` with context prompts. On the page, **Agent voice → Instant (browser voice)** removes TTS latency entirely, at the cost of a robotic voice.
3. **Restart** the API. The sidebar shows **Call Simulator** after the next settings poll, within about 30 s.
4. **Serve over HTTPS or on localhost.** Browsers only allow microphone access on secure origins. On plain `http://` to a non-localhost host, the page falls back to typed input and says why.
5. **Keep it internal.** `/api/v1/voice-simulator/*` falls under the existing internal-only `location /api/` ACL in [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) §6.2. Never add it to the public Twilio `location`. Behind Nginx, allow the upload size, because Nginx's default `client_max_body_size` is 1 MB and would reject longer utterances with its own 413:
   ```nginx
   location /api/v1/voice-simulator/audio {
       allow 10.0.0.0/8; allow 172.16.0.0/12; allow 192.168.0.0/16; deny all;
       client_max_body_size 3m;
       proxy_pass http://127.0.0.1:8000;
       include /etc/nginx/proxy_params;
       proxy_set_header X-Forwarded-Proto https;
   }
   ```
6. **Schedule the retention purge** (see §5.3).

**Production:** leave `ENABLE_VOICE_SIMULATOR` unset. If someone sets it together with `ENVIRONMENT=production`, the API refuses to start. If it is changed at runtime, every simulator route still returns 404. `LLM_PROVIDER=fake` is likewise ignored in production.

**Rollback:** set `ENABLE_VOICE_SIMULATOR=false` and restart. That removes the feature with no data change. To remove the schema too, run `alembic downgrade dd3a82a4a05a`. **This deletes every simulated session and `SIMULATOR` ticket** and rebuilds `ticket_source_enum` without the value. Real tickets and calls are untouched.

---

## 4. Testing guide

### Automated

| Suite | Command | Covers |
|---|---|---|
| Backend | `cd backend && .venv/Scripts/python -m pytest` (needs a disposable local Postgres, see `backend/README.md`) | `tests/test_voice_simulator.py`, 39 tests: gating and production refusal, full intake, isolation from every operational view, idempotent retry, cross-session id reuse, escalation, agent crash → SYSTEM_ERROR, LLM errors recorded, turn/concurrency/hourly caps, idle sweep, salvage on hang-up, notification opt-in and server refusal, audio success/failure/limits, pre-parse size rejection, metrics, stats, mock caller determinism and a full mock-driven call, TwiML unescaping, trace no-op. The rest of the suite (162 tests) guards the existing behaviour the isolation filters touched |
| Frontend unit | `cd frontend && npm test` | `tests/voiceSimulator.test.ts`, 28 tests under Node's built-in runner: every state-machine transition and ignored event, latency stats and budgets, CSV/transcript exports, replay divergence, load-test runner (expectations, concurrency cap, refused sessions, abort) |
| Frontend types / lint / build | `npx tsc -b`, `npm run lint`, `npm run build` | |

The backend tests use the fake LLM provider and a stub speech provider, so they never call OpenAI.

### Manual (browser)

These have **not** been run by the author. The page was built and type-checked but not opened in a browser. Walk through this before relying on it:

1. Start a call with the microphone. Check that the greeting plays, the speaker meter moves, and the status goes Speaking → Listening.
2. Push-to-talk: hold Space and describe a problem. The transcript should appear, then the reply. Check the latency table fills in.
3. Switch to Continuous (`L`), talk, and pause. The turn should end after about 0.8 s of silence, and the agent's voice must not trigger a turn.
4. Finish the intake, then check that the ticket preview shows a `SIM-` number and the ticket queue does **not** list it unless you filter Source = Simulator.
5. Deny microphone permission: an inline message should appear, and typing should still work.
6. Mock caller, Random issue, Replay (after hanging up), Load test with 5 callers, and all four exports.
7. Keyboard only: tab through everything, and try `?` for the shortcut list. Try a screen reader: status changes and new messages should be announced.
8. Browsers: Chrome, Edge, Firefox, Safari (the recorder falls back to `audio/mp4`), and mobile Chrome/Safari at phone width.

### Load testing

In the page, **Load test** runs 1, 5, 10 or 25 scripted callers concurrently: typed turns, no audio, one SIMULATOR ticket each. It reports average, p95 and max turn latency, agent (LLM) time, average and max queue wait, error rate, ticket-creation time and throughput, with CSV and JSON export. The same seed reproduces the same callers. Figures from a real run are in the [performance report](docs/reviews/VOICE_SIMULATOR_PERFORMANCE.md).

The runner is plain TypeScript (`frontend/src/lib/voiceSimulator/loadTest.ts`), so it can also be driven from Node against any environment. The performance report shows how.

---

## 5. Operations guide

### 5.1 What to watch

- **`GET /api/v1/voice-simulator/stats`** (also the "Simulator health" strip on the page): sessions and active sessions, tickets, turns, **failure rate** (turns that recorded an STT, LLM, TTS or agent error), and avg/p95/max for STT, LLM, TTS and end-to-end.
- **Logs**, logger `hfmg.simulator`, one INFO line per turn with no caller text:
  ```
  simulator session=<uuid> turn=3 COLLECT_NAME->COLLECT_EMAIL intent=provide_name llm_ms=812 tts_ms=431 stt_ms=598 errors=0
  ```
  Also `hfmg.speech.openai` (STT/TTS provider failures) and the existing `hfmg.llm.openai`.

### 5.2 Limits (all in `.env`)

| Setting | Default | Effect when hit |
|---|---|---|
| `SIMULATOR_MAX_CONCURRENT_SESSIONS` | 30 | `/start` → 409 |
| `SIMULATOR_MAX_SESSIONS_PER_HOUR` | 300 | `/start` → 429 |
| `SIMULATOR_MAX_TURNS_PER_SESSION` | 40 | `/process` → 422 |
| `SIMULATOR_MAX_AUDIO_BYTES` | 2,000,000 (roughly 2–8 min of Opus, depending on browser bitrate) | `/audio` → 413 |
| `SIMULATOR_SESSION_IDLE_TIMEOUT_SECONDS` | 600 | untouched sessions are closed (ABANDONED, no salvage) on the next `/start` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 5 / 10 | shared with the whole API; see the performance report before changing |

### 5.3 Retention

Audio is never written anywhere. Transcripts, LLM traces (which contain caller speech) and SIMULATOR tickets are kept until purged:

```bash
cd backend
python purge_simulator_data.py --dry-run     # what would go
python purge_simulator_data.py               # older than SIMULATOR_RETENTION_DAYS (14)
```

There is no job runner, so schedule it daily with cron or Task Scheduler. Until it is scheduled, simulator data accumulates.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Page says "The AI Call Simulator is turned off" | `ENABLE_VOICE_SIMULATOR` unset, or `ENVIRONMENT=production` | Set it (non-production only) and restart |
| API refuses to start: *"ENABLE_VOICE_SIMULATOR=true is not allowed when ENVIRONMENT=production"* | Both set | Working as intended; unset one |
| No "Call Simulator" in the sidebar, but the URL works | Settings status not refreshed yet, or the frontend predates the change | Wait about 30 s, or rebuild the frontend |
| "Use microphone" / "Speak replies" greyed out | No `OPENAI_API_KEY` (speech not configured) | Set the key, or type your turns |
| "This browser can't record audio here" | Page served over plain http from a non-localhost host | Serve over HTTPS or use localhost |
| "Microphone access was blocked" | Permission denied | Allow it in the address bar's site settings; typing still works |
| Every turn: "I'm sorry, I didn't catch that", and escalates after three | The model can't be reached. The inspector's Raw AI response shows `OPENAI_API_KEY is not set`, a timeout, or 401 | Fix the key/egress. The page warns when the server has no LLM key |
| Spoken turns always empty; Errors shows `stt: …` | STT provider failing (key, egress, model name) | Check `hfmg.speech.openai` logs and `SPEECH_STT_MODEL` |
| Replies shown but not heard; Errors shows `tts: …` | TTS provider failing | Same, with `SPEECH_TTS_MODEL` / `SPEECH_TTS_VOICE` |
| Background noise starts turns in Continuous mode | Speech threshold too low | Audio settings → raise the speech threshold, or use push-to-talk |
| `/start` → 409 "sessions are already active" | Concurrent cap; often abandoned tabs | Wait for the idle sweep (10 min), end sessions, or raise the cap |
| Load test at 25 callers shows queue wait in seconds | DB pool exhausted (15 connections per worker) | Expected at default settings; see the performance report. This affects Twilio calls equally |
| Send ticket email toggle disabled | `SIMULATOR_ALLOW_NOTIFICATIONS=false` (default) | Set true only when testing the email itself |
| Simulator ticket missing from the Tickets page | By design | Filter Source = Simulator, or use the ticket preview's "Open ticket" link |

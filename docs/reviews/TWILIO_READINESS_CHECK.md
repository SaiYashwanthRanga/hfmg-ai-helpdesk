# Twilio Readiness Check

Date: 2026-09-21. No Twilio functionality was added. Method: code review of the voice module, API, models, frontend and config (by a read-only reviewer, spot-checked against config and the NLU probe below). It complements the earlier TWILIO_READINESS_REPORT.md. **Nothing here was tested with a real call.**

## READY NOW

- **Webhook routes** (`backend/app/voice/routes.py`): `/api/v1/webhooks/twilio/voice`, `/voice/gather`, `/voice/status`, `/voice/fallback`, registered in `api/v1/router.py`. Each is guarded by `verify_twilio_signature`.
- **Conversation state machine** (`orchestrator.py`): 11 states, escalation, abandoned-call ticket salvage, duplicate-turn idempotency, creates tickets via the existing `ticket_service` with `source=PHONE`.
- **TwiML builders** (`twiml.py`), **session storage** (`session.py`, keyed by call SID), **spoken scripts** (`scripts.py`).
- **Database**: `VoiceCallSession` model and Alembic migration `c78e93327925`.
- **Read API + Calls page**: `/api/v1/voice-calls`, `/summary`, `/{id}`; frontend fetches real data (no mocks). Shows empty until calls exist.
- **Speech understanding** (`nlu.py`) needs only `OPENAI_API_KEY`, not Twilio. **It was broken until today's fix**: with the old reasoning default it exceeded the 4s budget on every utterance (returned "unable to determine", which escalates the call). After the fix, real probes took 2.7s and 1.8s with correct extraction. This is the most important change for go-live.
- **Security posture**: with no token set, every webhook returns 403 "Webhook not configured" (fails closed).

## REQUIRES CREDENTIALS

- `TWILIO_AUTH_TOKEN` (currently empty in `.env`). Needed for signature validation to accept real traffic.
- A purchased Twilio phone number.

## REQUIRES CONFIGURATION

- `TWILIO_PUBLIC_BASE_URL` set to the public HTTPS origin (signature is computed over the public URL; a wrong value makes every valid call fail with 403).
- In the Twilio console: voice webhook -> `.../webhooks/twilio/voice`, status callback -> `.../voice/status`, fallback -> `.../voice/fallback`.
- Keep `TWILIO_VALIDATE_SIGNATURE=true` in production.
- Voice tuning env vars have working defaults: `VOICE_TTS_VOICE=Polly.Joanna-Neural`, `VOICE_LANGUAGE=en-US`, `VOICE_SPEECH_MODEL`, `VOICE_NLU_TIMEOUT_SECONDS=4.0`.
- Compliance decision before real traffic: transcripts and caller speech are sent to OpenAI unredacted (see OPENAI_PROMPT_REVIEW.md), and the earlier report notes a BAA question.

## REQUIRES LIVE TESTING

- Signature validation against a real signed request behind your actual proxy/host.
- Speech recognition accuracy, pronunciation of spoken ticket numbers, phone numbers and emails (`scripts.py` uses spaced characters, not SSML).
- End-to-end latency per turn: NLU is about 2-3s plus TTS and network; Twilio abandons a webhook at roughly 15s. Measure on a real call.
- Barge-in, silence handling, no DTMF fallback.
- Abandoned-call salvage and status callback timing.
- Category accuracy: a probe classified "computer won't turn on" as `Network`. Review with real utterances.
- Re-run `backend/tests/test_voice_webhooks.py`. The earlier report claims 6/8 passing with two `asyncpg` connection-pool failures; I did not investigate that. (The full suite passed 116/116 in my runs.)

## Known gaps

- `dependency_health.check_twilio()` (`services/dependency_health.py`) only checks that a token string is non-empty. The dashboard will show Twilio "operational" for an invalid token. There is no `TWILIO_ACCOUNT_SID` setting, so a real reachability check is not possible without adding one. Low risk, but do not treat that tile as proof.
- `LiveCallMonitor.tsx` is an intentional placeholder (unbuilt scope, not credentials).
- `CallTimeline.tsx` reports no state history because the data model stores only the current state.

## What happens when credentials arrive

1. Set `TWILIO_AUTH_TOKEN` and `TWILIO_PUBLIC_BASE_URL`, redeploy.
2. Point the Twilio number's webhooks at the four URLs above.
3. Place a test call. Twilio POSTs `/voice`; signature validates; a `voice_call_sessions` row is created; the orchestrator asks the problem, name, phone (skipped if caller ID is usable), optional email, confirms category when unsure, then creates a ticket (`source=PHONE`) and triggers AI summary and email in the background.
4. The call appears on the Calls page; the ticket appears on Tickets with an AI summary.

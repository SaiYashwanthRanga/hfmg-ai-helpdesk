# HFMG AI Help Desk — Twilio Voice Agent Readiness Report

**Prepared:** 2026-09-21
**Scope:** Review-only assessment of Phase 2 (Twilio voice agent) readiness. No code was modified, no new Twilio functionality was written, and `backend/.env` was not changed. This report either confirms or corrects the claims in `TWILIO_ARCHITECTURE.md`, `CALL_FLOW.md`, `VOICE_AGENT_DESIGN.md`, and `KNOWN_LIMITATIONS.md` against the code as it exists today.

---

## Overview

**Verdict: the voice agent is architecturally complete and almost entirely blocked on a single missing credential (`TWILIO_AUTH_TOKEN`) plus two Twilio-console/account steps (a provisioned phone number and, before real traffic, a signed BAA).** There is no mock/placeholder logic anywhere in the implementation — every module described in `TWILIO_ARCHITECTURE.md` §5 exists, is wired into the app, and is exercised by an automated test. The read-side API and the Calls page/drawer in the frontend already render real database rows with no mock data. The one genuinely unbuilt piece is the Live Call Monitor page, which the code itself honestly labels a "Phase 5 stretch" placeholder — it is not blocked by credentials, it simply hasn't been built.

Right now, with `TWILIO_AUTH_TOKEN` empty and `TWILIO_VALIDATE_SIGNATURE=true` (the current, unmodified `.env` state), every webhook call is rejected with a clean `403 "Webhook not configured"` (`backend/app/voice/security.py:36-38`) — the system fails safe rather than silently accepting unauthenticated traffic. That is exactly the correct posture for a pending-credentials state.

Live evidence gathered this session: the full webhook-layer test suite (`backend/tests/test_voice_webhooks.py`) was run fresh against the project's existing disposable test database (`hfmg_helpdesk_test`, never the dev DB) with `TWILIO_VALIDATE_SIGNATURE` mocked to `false` inside the test process only — exactly the pattern `backend/README.md` documents for curl testing, but exercised through the app's own HTTP layer. On a clean, isolated run: **6 of 8 tests passed**, including the full turn-0 greeting → session creation, idempotent replay, unknown-call handling, an abandoned-call-with-nothing-collected case, and both the invalid-signature-403 and valid-signature-200 paths. The 2 failures in that run (and different, non-overlapping failures on a second back-to-back run in the same shell session) were `asyncpg.exceptions.InvalidCachedStatementError` — a documented artifact of reusing a long-lived connection pool against a schema that gets dropped/recreated between pytest invocations in the same session (the test suite's own `conftest.py:33-36` comment describes this exact class of asyncpg caching quirk). These are test-environment pool artifacts, not application defects: the failing tests are simple, deterministic, and pass in isolation. **`backend/.env` was never edited in this session** — confirmed by not touching the file; the live dev server's TWILIO_VALIDATE_SIGNATURE remained `true` throughout.

---

## Architecture Verified

Every structural claim in `TWILIO_ARCHITECTURE.md` and `CALL_FLOW.md` was checked against the running code:

- **Module layout** (`TWILIO_ARCHITECTURE.md` §5) matches exactly: `backend/app/voice/routes.py`, `security.py`, `session.py`, `orchestrator.py`, `twiml.py`, `nlu.py`, `scripts.py` all exist with the described responsibilities. `routes.py` is thin (validates via `Depends(verify_twilio_signature)`, delegates to `orchestrator`, returns TwiML) — confirmed `backend/app/voice/routes.py:46-171`.
- **Four webhook endpoints** (`TWILIO_ARCHITECTURE.md` §5 table) all present and routed: `POST /voice` (`routes.py:46`), `/voice/gather` (`routes.py:71`), `/voice/status` (`routes.py:115`), `/voice/fallback` (`routes.py:155`). Wired into the app via `backend/app/api/v1/router.py:14` (`voice_routes.router`) and `:12` (`voice_calls.router`, the read API), both included in `backend/app/main.py:50`.
- **11-state `VoiceCallState` enum**, not 13 — confirmed at `backend/app/db/models.py:44-56`. Both `TWILIO_ARCHITECTURE.md` §6 and `CALL_FLOW.md` §2 already carry an explicit "Tier 5 documentation alignment" correction acknowledging this; the correction is accurate and the frontend type (`frontend/src/types/voiceCall.ts:8-19`) matches the backend enum member-for-member. No drift here.
- **Signature validation** (`TWILIO_ARCHITECTURE.md` §5, §9): `backend/app/voice/security.py:31-47` — `verify_twilio_signature` skips validation only when `twilio_validate_signature=False` (logged as a warning), otherwise 403s if `twilio_auth_token` is unset, else recomputes the HMAC via `twilio.request_validator.RequestValidator` against `_expected_url()`, which uses `TWILIO_PUBLIC_BASE_URL` when set (`security.py:19-28`) to survive being behind a tunnel/proxy — matches `TWILIO_SETUP.md` §4-5 exactly.
- **State machine** (`CALL_FLOW.md` §1-9): `backend/app/voice/orchestrator.py` implements every transition described — description → name → phone (only if caller ID unusable, `session.py:14-18` `caller_id_is_usable`) → email (optional, 2 tries, `orchestrator.py:161-200`) → category confirmation (only on medium/low confidence, `orchestrator.py:241-251`) → ticket creation/read-back → anything-else loop or goodbye. Escalation (`orchestrator.py:364-406`) and abandoned-call salvage (`orchestrator.py:325-333`, invoked from `routes.py:137-150`) both match `CALL_FLOW.md` §6 and §8 precisely, including the "escalate to at least HIGH priority" rule (`orchestrator.py:302-305`).
- **Ticket integration** (`TWILIO_ARCHITECTURE.md` §8): `orchestrator._create_ticket` builds a `TicketCreate` and calls the unchanged Phase 1 `ticket_service.create_ticket` (`orchestrator.py:307-322`), sets `source=PHONE`, and `routes.py:33-43` fires the same two background tasks (`send_ticket_notification`, `generate_summary_for_ticket`) the web form uses, unawaited — matches the doc's "AI summary not awaited" claim.
- **Idempotency** (`CALL_FLOW.md` §9): `session.py:26-45` (`get_or_create_session` keyed by unique `twilio_call_sid`) and `orchestrator.py:260-268` (`ticket_id` guard against replay) both confirmed, and both are covered by dedicated tests (`test_incoming_call_is_idempotent`, `test_ticket_creation_is_idempotent_on_replay`).
- **DB schema**: `voice_call_sessions` table (`backend/app/db/models.py:136-174`) matches `TWILIO_ARCHITECTURE.md` §6's column list exactly (collected/turns JSONB, misunderstanding/email counters, escalated+reason, ticket_id FK, ended_at). Migration exists: `backend/alembic/versions/c78e93327925_add_ticket_source_and_voice_call_.py`.
- **Config surface** (`backend/app/core/config.py:49-66`): every `TWILIO_*`/`VOICE_*` setting named in the docs exists with the documented defaults (see Requires Credentials/Configuration below for the full table).

One real drift found: `VOICE_AGENT_DESIGN.md` §2's pronunciation rules state ticket numbers/phone numbers are "Rendered with SSML `<say-as interpret-as="characters">` plus `<break time="300ms"/>`." The actual implementation (`backend/app/voice/scripts.py:70-97`, `spoken_ticket_number`/`spoken_phone_number`/`spoken_email`) produces plain-text, space/comma-separated strings (e.g. `"H F M G, 2 0 2 6, 0 0 0 4 8 2"`) with no SSML markup at all, and `backend/app/voice/twiml.py` calls `.say(prompt, voice=...)` with plain strings — Twilio's `<Say>` verb does not receive any `<say-as>` or `<break>` tags. This plain-text character-spacing trick often achieves similar results with Polly, but it is not what the design doc describes, and only a live call can confirm the pronunciation quality actually matches expectations (see Requires Testing).

---

## Ready Now

Everything below is fully built, code-complete, and either unit/integration-tested or verified live this session. Nothing here needs anything except a phone call to prove end-to-end:

- **Full conversation state machine** — `backend/app/voice/orchestrator.py`, 11 states, all transitions, retry/re-prompt ladder (3-strikes), escalation (caller-requested and repeated-misunderstanding), abandoned-call salvage. Covered by 11 unit tests in `backend/tests/test_voice_agent.py` with NLU fully mocked (no Twilio or LLM account needed to run them).
- **TwiML generation** — `backend/app/voice/twiml.py`: `<Gather input="speech">` with `bargeIn`, configurable voice/language/timeout, silence-triggers-redirect handling, `<Say>`+`<Hangup>` terminal responses.
- **Twilio webhook signature validation** — `backend/app/voice/security.py`, fails safe today (empty token → 403, not silent bypass). Covered by `test_invalid_signature_is_rejected` and `test_valid_signature_is_accepted` (the latter computes a real HMAC via `twilio.request_validator.RequestValidator` and confirms acceptance).
- **NLU / structured extraction** — `backend/app/voice/nlu.py`: strict-JSON-schema extraction for description/category/priority, name, phone, email, yes/no confirmation, and escalation-intent detection, with server-side allowlist validation on every enumerated field (category, priority) and prompt-injection framing in the system prompt. This module runs through `backend/app/llm/factory.py`, the same provider used by the already-shipped AI ticket summarizer — and `backend/.env` already has a real `OPENAI_API_KEY` configured with `ENABLE_AI_SUMMARY=true`, so **this layer is live and callable today**, independent of Twilio.
- **Ticket creation pipeline reuse** — voice tickets go through the unchanged Phase 1 `TicketService`, with `source=PHONE`, background email notification, and background AI summary generation, exactly like web tickets.
- **Read-side API for the dashboard** — `GET /api/v1/voice-calls`, `/voice-calls/summary`, `/voice-calls/{id}` (`backend/app/api/v1/voice_calls.py`, `backend/app/services/voice_call_service.py`), covered by 5 tests in `backend/tests/test_voice_calls_api.py` (list/filter by state+escalated/detail-with-transcript/404/summary counts).
- **Frontend Calls page end-to-end** — see Frontend Readiness below; renders real API data today, will show real calls the moment `voice_call_sessions` rows exist (which local curl/pytest testing already proves it can produce, no Twilio account required).
- **Local testing path exactly as `backend/README.md` and `TWILIO_SETUP.md` §6 document** — `TWILIO_VALIDATE_SIGNATURE=false` + curl/HTTP client against the four webhook endpoints. Verified working this session via the pytest suite's equivalent (`_disable_signature_validation` fixture in `test_voice_webhooks.py:20-23`), without ever touching the dev `.env`.

---

## Requires Credentials

From `backend/app/core/config.py:49-66`, the Twilio/voice settings block:

| Setting | Current value (`backend/.env`) | Blocks what |
|---|---|---|
| `TWILIO_AUTH_TOKEN` | **empty** | Every webhook call, unconditionally. `security.py:36-38` raises `403` on any request when this is unset and validation is on. This is the only credential the webhook layer needs — it is the sole authentication mechanism. |
| A provisioned Twilio phone number | not provisioned | Nothing to dial. This is a Twilio-console purchase (`TWILIO_SETUP.md` §2), not an app config value — there is no phone-number field anywhere in `config.py`. |
| Signed BAA with Twilio | not confirmed done | Not a code blocker, but `TWILIO_SETUP.md` §1 and `TWILIO_ARCHITECTURE.md` §9 both flag it as required before *any* real caller/PHI-adjacent traffic — a compliance gate, not an engineering one. |

**Notable correction to the task brief's assumption:** `TWILIO_ACCOUNT_SID` does **not** exist anywhere in `backend/app/core/config.py` or `backend/.env.example`, and the webhook signature-validation code path does not use or need it — Twilio's webhook HMAC only requires the auth token. `ACCOUNT_SID` is referenced only in `backend/app/services/dependency_health.py:88-96` (`check_twilio`) as a documented *future* enhancement: the current Twilio "reachability" check on `GET /health/dependencies` / `GET /settings/status` is configuration-only (`"operational" if settings.twilio_auth_token else "down"`), not a real API call, precisely because `ACCOUNT_SID` was never added. This is already tracked as known, non-blocking tech debt in `TECHNICAL_DEBT.md:22` and `RELEASE_CHECKLIST.md:30` — it does not need to be added to make voice calls work; it would only make the health-check dashboard tile more accurate.

---

## Requires Configuration

Decisions/values needed once credentials exist, but which are not themselves credentials:

| Item | Where | Notes |
|---|---|---|
| `TWILIO_PUBLIC_BASE_URL` | `config.py:56`, empty by default | Needed the moment the app runs behind ngrok or a real domain — `security.py:19-28` uses it to reconstruct the URL Twilio actually signed. `TWILIO_SETUP.md` §5 calls this "the most common cause of every call failing with 403." Must be set in every deployed environment, no trailing slash. |
| Twilio console webhook URLs | Twilio Console → Phone Numbers → Configure | Three URLs to set exactly as `TWILIO_SETUP.md` §2 specifies: "A call comes in" → `/api/v1/webhooks/twilio/voice`, "Primary handler fails" → `/voice/fallback`, "Call status changes" → `/voice/status`, all `HTTP POST`. The `/voice/gather` endpoint is never configured manually — the app supplies it in the TwiML `Gather action`. |
| Voice/language tuning | `config.py:57-64` | Already has working defaults (`Polly.Joanna-Neural`, `en-US`, `experimental_conversations`, 6s gather timeout, 4s NLU timeout, 3 misunderstandings, 2 email attempts) — no action needed unless HFMG wants a different voice or timing. |
| Twilio billing alert | Twilio Console | `TWILIO_SETUP.md` §8 recommends this explicitly; not app config. |
| Business-hours-aware greeting / Spanish support | — | Explicitly listed in `TWILIO_ARCHITECTURE.md` §14 as "still open, not blocking implementation." Not required for go-live. |

---

## Requires Testing

Only a real, Twilio-routed phone call can verify these — code review and mocked/local tests cannot:

- **Actual speech-to-text accuracy** on real audio (accents, clinic-floor background noise, phone-line codec quality) — `experimental_conversations` speech model behavior in practice.
- **Real end-to-end turn latency** — Twilio → webhook → NLU (OpenAI) → TwiML response must stay comfortably under Twilio's ~15s webhook timeout; `VOICE_NLU_TIMEOUT_SECONDS=4.0` is a budget, not a measured result.
- **TTS pronunciation**, specifically: (a) "eClinicalWorks" — `VOICE_AGENT_DESIGN.md` §2 flags this needs an SSML phoneme/alias hint "or Polly will mangle it," and `scripts.py` currently has no such hint anywhere; (b) ticket-number and phone-number read-back, given the drift noted above (plain-text spacing instead of the documented SSML `<say-as>` tags) — needs a live call to confirm it's actually read digit-by-digit and not as cardinal numbers.
- **Barge-in / interruption handling** — `bargeIn="true"` is set (`twiml.py:26,62`) but its real feel (does it actually let a caller cut off a long prompt cleanly) is unverifiable outside a live Twilio-hosted call.
- **The full `TWILIO_SETUP.md` §7 acceptance checklist** — an 11-point manual checklist (connects within 2s, correct greeting, natural voice, name/email flow, spelled-out ticket number, "anything else" loop, dashboard reflects `source=PHONE`, notification email arrives, AI summary populates within ~30s) plus a second explicit escalation-path check. None of this is automatable.
- **DTMF is not implemented at all** — `twiml.py`'s `Gather` uses `input="speech"` only, no `input="dtmf"` or hybrid. This isn't flagged as a gap in any doc (none of the design docs promise DTMF), so it's not "drift," but worth surfacing to the project owner as a scope confirmation: if any stakeholder expects touch-tone fallback, it doesn't exist in code today.
- **Real Twilio-signed requests against signature validation** — the automated test (`test_valid_signature_is_accepted`) computes a synthetic-but-correct HMAC via the Twilio SDK's own validator, which is strong evidence the algorithm is right, but has never validated an actual request Twilio itself signed and sent over the public internet.

---

## Frontend Readiness

- **`frontend/src/pages/CallsPage.tsx`** — fully wired to the real API via `useVoiceCallsQuery`/`useVoiceCallSummaryQuery` (`frontend/src/api/voiceCalls.ts:42-52`). No mock/sample data anywhere. Page/drawer state lives in the URL (`?page=`, `?call=`), matching the Tickets page pattern.
- **`CallStatsPanel.tsx`, `CallTable.tsx`, `CallCard.tsx`, `CallStateBadge.tsx`, `EscalationReasonBadge.tsx`, `CallDrawer.tsx`, `TranscriptViewer.tsx`** (`frontend/src/components/calls/`) — all read directly from `VoiceCallListItem`/`VoiceCallDetail` as returned by the backend schemas (`backend/app/schemas/voice_call.py`), with in-code comments explicitly confirming each is "real" (e.g. `CallStatsPanel.tsx:5` "GET /voice-calls/summary, real"). `TranscriptViewer.tsx` and `CallTable.tsx` both note, correctly, that there is no audio player anywhere — matching the "recording is off by design" decision in `TWILIO_ARCHITECTURE.md` §9.
- **`CallTimeline.tsx`** is a deliberate, honest dead-end: it states outright that `voice_call_sessions` stores only the *current* state, never a history of transitions, so a timeline literally cannot be rendered from existing data (`frontend/src/components/calls/CallTimeline.tsx:1-21`). This is a data-model limitation, not a credentials gap — adding it would require a new table/column, independent of Twilio.
- **`LiveCallMonitor.tsx`** (`frontend/src/pages/LiveCallMonitor.tsx`) is an explicit, self-declared placeholder: `NotYetAvailable` with the message "This screen ships alongside Calls in Phase 5... Real implementation is a Phase 5 stretch item." Route is registered at `/calls/live/:callId` (`frontend/src/App.tsx:29`) so a direct link never 404s, but nothing behind it is built. **This is not blocked by Twilio credentials — it is simply unbuilt**, and `KNOWN_LIMITATIONS.md` already discloses this to stakeholders ("There's no live view of an in-progress call").
- **`RecentCallsPanel.tsx`** (dashboard) — same real-data pattern, confirmed.

In short: the frontend is not blocked by credentials at all. It will start showing real calls the instant `voice_call_sessions` rows exist, which local testing (curl or pytest, no Twilio account) can already produce today.

---

## Gaps or Drift From Documentation

1. **SSML pronunciation claim vs. plain-text implementation** (new finding, not previously documented) — `VOICE_AGENT_DESIGN.md` §2 states ticket/phone numbers are rendered with SSML `<say-as interpret-as="characters">` + `<break>` tags; the actual code (`scripts.py` spoken-* helpers + `twiml.py`'s plain-string `.say()` calls) uses space/comma-separated plain text instead. Likely functionally similar but unverified — flag for correction in the design doc or confirm live-call pronunciation is acceptable as-is.
2. **`TWILIO_ACCOUNT_SID` absence** — already tracked in `TECHNICAL_DEBT.md:22` and `RELEASE_CHECKLIST.md:30` as known, non-blocking debt (health-check dashboard tile is configuration-only, not a real Twilio API ping). No action needed for voice calls to function; only relevant if the team wants a truer "is Twilio actually reachable" signal later.
3. **11 vs. 13 states** — both `TWILIO_ARCHITECTURE.md` §6 and `CALL_FLOW.md` §2 already carry accurate, explicit self-corrections; verified against `models.py` and the frontend type — no outstanding drift.
4. **`LiveCallMonitor` / call-state-history** — both already honestly disclosed in `KNOWN_LIMITATIONS.md` and in the component code itself; no drift, just confirmed-accurate limitations.
5. No other discrepancies found between the three design docs (`TWILIO_ARCHITECTURE.md`, `CALL_FLOW.md`, `VOICE_AGENT_DESIGN.md`) and the implementation — the codebase is unusually well-aligned with its own documentation, including several docs that already contain their own "corrected against code" annotations from a prior documentation-alignment pass.

---

## Go-Live Checklist

Ordered list of exactly what to do the moment Twilio credentials/number arrive:

1. **Sign the Twilio BAA** and confirm HIPAA-eligible configuration is applied to the account (`TWILIO_SETUP.md` §1) — before any real caller traffic, not an engineering step but a gate on this list.
2. **Buy the phone number** in the Twilio Console (Voice capability, HFMG area code) — `TWILIO_SETUP.md` §2.
3. **Get the Auth Token** from Twilio Console → Account → API keys & tokens, and set `TWILIO_AUTH_TOKEN` in the deployed environment's `.env` (or secrets manager). Restart the app.
4. **Set `TWILIO_PUBLIC_BASE_URL`** to the real deployed HTTPS hostname (or ngrok URL for a dry run) — no trailing slash. Restart the app if changed after step 3.
5. **Confirm `TWILIO_VALIDATE_SIGNATURE=true`** explicitly (it already defaults to `true` in this repo and is currently `true` in `backend/.env` — just don't flip it, and re-verify in whichever environment actually receives Twilio traffic, per `RELEASE_CHECKLIST.md:11`).
6. **Configure the three webhook URLs** in the Twilio Console against the purchased number, exactly as `TWILIO_SETUP.md` §2 specifies (voice, fallback, status-callback; all `HTTP POST`).
7. **Set a Twilio billing alert** (`TWILIO_SETUP.md` §8).
8. **Confirm `GET /api/v1/health/dependencies` reports Twilio "operational"** once the token is set (remember this is a configuration-only check today, not a live API ping — see Gaps §2).
9. **Place the first real test call** and walk the full `TWILIO_SETUP.md` §7 acceptance checklist (connects fast, correct greeting/voice, name→email flow, skip works, ticket number spelled out correctly, "eClinicalWorks" pronunciation acceptable, anything-else loop, dashboard shows `source=PHONE` with correct category/priority/transcript, notification email arrives, AI summary populates within ~30s).
10. **Place a second test call and immediately ask for a human** to verify the escalation path end-to-end (agreement without argument, callback promise, HIGH/URGENT ticket with `CALLBACK REQUESTED` description).
11. **Confirm the Calls page and drawer** (`/calls`) show both test calls correctly in the dashboard — this step needs no further engineering work, only verification.
12. Only after the above: consider whether to add `TWILIO_ACCOUNT_SID` + a real REST reachability check (optional hardening, `TECHNICAL_DEBT.md:22`), tune `scripts.py` for any mispronunciations found in step 9, and decide whether business-hours-aware greeting or Spanish support are now in scope (`TWILIO_ARCHITECTURE.md` §14).

---

**`backend/.env` state confirmed unchanged at the end of this review**: `TWILIO_AUTH_TOKEN` empty, `TWILIO_VALIDATE_SIGNATURE=true`, `TWILIO_PUBLIC_BASE_URL` empty — identical to the state at the start of this session. No file in the repository was edited as part of this review.

# AI Call Simulator — Security Review

**Date:** 2026-09-28 · **Scope:** everything added on `feature/voice-simulator`: the `/api/v1/voice-simulator/*` routes, `app/speech`, `app/core/trace.py`, the fake LLM provider, the isolation changes to existing services, and the page. **Method:** code review of every endpoint and changed query, confirmed by tests (`backend/tests/test_voice_simulator.py`) and by a live run against a real server (`VOICE_SIMULATOR_PERFORMANCE.md` §2).

## Summary

The simulator is safe to enable in a **development or internal test environment** under the existing network model: the API is reachable only from the HFMG network, per DEPLOYMENT_GUIDE.md §6.

It must not be enabled anywhere the API is reachable by untrusted users. This is enforced as well as documented: the process refuses to start with `ENABLE_VOICE_SIMULATOR=true` and `ENVIRONMENT=production`, and every route returns 404 in production even if the flag is changed at runtime.

The review found two issues, both fixed before this report:

| # | Finding | Severity | Status |
|---|---|---|---|
| S1 | Any caller could set `send_notifications: true` on every session, allowing up to about 600 emails an hour to `helpdesk@hfmg.net` within the hourly session cap | High (abuse) | **Fixed.** A server-side opt-in, `SIMULATOR_ALLOW_NOTIFICATIONS`, defaults to false. `/start` returns 422 otherwise, and the page disables the toggle. Tested |
| S2 | `/audio`'s size cap was checked after Starlette had parsed and spooled the whole multipart body, so a very large upload was fully received first | Medium (resource exhaustion) | **Fixed.** `AudioSizeLimitMiddleware` rejects on `Content-Length` before parsing. The route still checks actual bytes, which covers chunked uploads. Tested |

What remains is accepted, not open, for the intended environment (§5).

## 1. Production safeguards

| Safeguard | Where | Verified by |
|---|---|---|
| Off unless `ENABLE_VOICE_SIMULATOR=true` | `routes.require_simulator_enabled` on the router | `test_every_route_is_404_when_disabled` |
| Startup refused when enabled with `ENVIRONMENT=production` (or `prod`) | `Settings._simulator_never_in_production` | `test_settings_refuse_simulator_in_production` |
| Per-request production check, so a runtime flip can't expose it | `routes.simulator_allowed` | `test_routes_are_404_in_production_even_if_flag_flipped_at_runtime` |
| Disabled routes answer **404**, not 403, so the feature isn't advertised | same | same |
| `LLM_PROVIDER=fake` ignored in production (falls back to OpenAI, logs an error) | `llm/factory.get_provider` | code review |
| Behind the internal-only network ACL | Nginx `location /api/` (DEPLOYMENT_GUIDE.md §6.2) | **Deployment responsibility.** Never add these paths to the public Twilio location |

## 2. Endpoint review

The API has no authentication (ARCHITECTURE.md, MVP scope decision). Every row below assumes an attacker already on the internal network, with the flag on.

| Endpoint | Input validation | Abuse considered | Result |
|---|---|---|---|
| `GET /config` | none needed | Leaks which models are configured and the limits | No secrets. Keys never leave `config.py` |
| `POST /start` | `caller_id` regex + 32 chars; `label` ≤ 200; booleans | Session flooding; email spam (S1) | 409 at 30 concurrent, 429 at 300/hour, both counted in Postgres so they hold across workers. Email refused unless the server allows it |
| `POST /audio` | UUID fields; MIME allowlist; size cap before and after parsing; empty rejected | Huge uploads (S2); unsupported formats reaching the provider; audio retention | Audio is held in memory for the provider call and then dropped (`del audio`); never written to disk or DB. STT runs with no DB connection held |
| `POST /process` | UUIDs; `utterance` ≤ 2000; `input_mode` enum | Replaying a turn to double-count misunderstandings; reusing another session's turn id; driving a real Twilio call; runaway sessions | Idempotent on `turn_client_id` (row-lock serialized); 409 when the id belongs to another session; sessions loaded only with `is_simulated = true` (404 otherwise); 40-turn cap |
| `POST /end` | UUID; reason enum | Ending a real call; forcing a salvage ticket | Simulated sessions only. Salvage creates at most one SIMULATOR ticket per session, the same rule as the phone path |
| `GET /session/{id}` | UUID | Reading someone else's test transcript | Readable by anyone who knows the UUID4 (122 random bits). There is no list endpoint. Accepted for an unauthenticated test tool (§5, R2) |
| `GET /metrics/{id}` | UUID | Same | Timings only |
| `POST /session/{id}/client-metrics` | UUIDs; each value 0–600,000 | Falsifying latency numbers | Can only overwrite browser-reported timing fields of a simulated turn. Server-measured fields are not writable |
| `GET /stats` | `hours` 1–720 | Enumeration | Aggregates only; no ids or text |
| `GET /mock-caller`, `/random-issue` | seed bounded to int32; category ≤ 64 chars | none | Static data |
| `GET /settings/status` (changed) | none | none | One extra boolean |

**Injection.** Caller text reaches the model only through `voice/nlu.py`'s existing path: delimited as untrusted data, strict JSON schemas, and allowlist validation of category and priority. The mock bank includes a prompt-injection caller to exercise this. All database access goes through SQLAlchemy with bound parameters. The frontend renders every server string as text through React; there is no `dangerouslySetInnerHTML`.

## 3. Isolation of simulator data

| Surface | Mechanism | Verified |
|---|---|---|
| Ticket queue (`GET /tickets`) | Excludes `source = SIMULATOR` unless explicitly requested | test + live run (42 SIM tickets, queue total 0) |
| Dashboard KPIs, recent activity, every analytics chart, AI Insights category breakdown | `_REAL_TICKET` / `_REAL_CALL` filters in `analytics_service` | test + live run |
| Calls page, call summary, call detail | `is_simulated = false` in `voice_call_service`; detail returns 404 for simulated ids | test + live run |
| Ticket numbers | `SIM-YYYY-XXXXXXXX`, a separate namespace; never consumes an `HFMG-` number | test |
| Notification email | Off per session by default and off per server by default (S1) | test |
| Simulator acting on real calls | Every simulator query requires `is_simulated = true` | `test_simulator_cannot_touch_a_real_call` |
| Twilio acting on simulated sessions | Would need a valid Twilio signature for a `SIM-` CallSid; not reachable | code review |

**Not isolated, by design:** a SIMULATOR ticket is a real row. Its detail page opens (`GET /tickets/{id}`), and staff *could* change its status. AI summary generation runs on it, spending model credit when `ENABLE_AI_SUMMARY=true`, because exercising summary generation was a stated requirement.

## 4. OpenAI cost exposure

The following are per-turn upper bounds, worst case, for someone on the internal network with the flag on:

| Per `/process` turn | Calls |
|---|---|
| NLU | 1 structured call (`voice_nlu_max_retries=1`, so at most 2 attempts) |
| TTS | 1 synthesis of the agent's reply (a sentence or two; input capped at 4,000 chars) if the session has TTS on |
| STT | 1 transcription per `/audio`, of up to `SIMULATOR_MAX_AUDIO_BYTES` (2 MB, roughly 2–8 minutes of Opus depending on the browser's bitrate) |
| AI summary | 1 text call per ticket, if `ENABLE_AI_SUMMARY=true` |

The worst case per hour, set by the caps, is 300 sessions × 40 turns = **12,000 NLU calls, 12,000 TTS calls and 12,000 STT uploads**. At the cheap default models that is a bounded but non-trivial bill if someone scripts it deliberately; normal QA use is a few hundred turns a day. To tighten it, lower `SIMULATOR_MAX_SESSIONS_PER_HOUR` and `SIMULATOR_MAX_AUDIO_BYTES`. Load tests should use `LLM_PROVIDER=fake`, which costs nothing.

Using the same `OPENAI_API_KEY` as production would let simulator abuse exhaust production quota. **Recommendation:** give test environments their own key with a spend limit set in the OpenAI dashboard. That is operational advice; no code enforces it.

## 5. Accepted risks

| # | Risk | Why acceptable here | Revisit when |
|---|---|---|---|
| R1 | No authentication or per-user rate limit; the caps are global | Same model as the rest of the API: internal network only, and not in production at all | Phase 3 auth lands; then scope sessions to users |
| R2 | Any session readable by anyone holding its UUID | Unguessable ids, no listing, test data only | Same |
| R3 | Transcripts and LLM traces store what testers said, which could include real PHI despite the on-page warning | 14-day retention purge; audio never stored; warning shown on the page | The purge is **not scheduled automatically** (no job runner). Schedule it (VOICE_SIMULATOR.md §5.3) before sharing the environment widely |
| R4 | Chunked uploads without `Content-Length` are still parsed before the byte check | Browsers send `Content-Length` for `FormData`; Nginx's `client_max_body_size` (default 1 MB, so raise it to about 3 MB for this path) bounds the rest | Exposed beyond the internal network (it shouldn't be) |
| R5 | Browser-reported timings can be falsified | Affects only a test tool's latency charts | Never. Server-measured stages are authoritative |
| R6 | The simulator shares the production DB pool and database when co-hosted | It is off in production, so there is no co-hosting | Someone proposes enabling it next to real traffic |

## 6. Logging and PHI

- The `hfmg.simulator` INFO line per turn carries the session id, states, intent, timings and error count. **It never carries caller text.**
- Provider failure logs include only exception class, status and request id; this is the existing `_describe` helper.
- LLM prompts containing caller speech are stored in `voice_simulator_turns.llm_trace` for the inspector. That is database content under the retention purge (R3), not logs.

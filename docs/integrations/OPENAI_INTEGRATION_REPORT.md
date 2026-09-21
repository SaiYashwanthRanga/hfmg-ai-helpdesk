# HFMG AI Help Desk — OpenAI Integration Verification Report

> **Point-in-time report (2026-09-21).** Written before the reasoning-effort fix that made gpt-5-family calls default to `minimal` effort. For the failure it missed and the fix, see [../reviews/AI_FAILURE_ANALYSIS.md](../reviews/AI_FAILURE_ANALYSIS.md). Configuration and endpoint findings below were verified at the time and may have shifted since.

**Performed:** 2026-09-21, after a real `OPENAI_API_KEY` was provided for local development.
**Method:** Every finding below is verified against running code — the local backend (`http://localhost:8000`), the project's pytest suite, and live `curl` requests — not asserted from documentation. Commands and outputs are included so results are reproducible.
**Scope:** Configuration loading, the `/health/dependencies` and `/settings/status` endpoints, AI ticket summarization end-to-end, the regenerate-summary endpoint, error handling, fallback behavior, and secret-exposure checks across logs/responses/frontend.

---

## 1. Configuration Loading — Pass

`backend/app/core/config.py` defines `openai_api_key`, `openai_model`, `openai_base_url`, `openai_timeout_seconds`, `openai_max_retries`, `openai_max_output_tokens`, `openai_temperature`, and `openai_reasoning_effort` on the `Settings` model (`pydantic_settings.BaseSettings`, `env_file=".env"`). All are read correctly from `backend/.env`.

Live confirmation via `GET /api/v1/settings/status`:
```json
"openai": {
  "configured": true,
  "status": "down",
  "masked_key": "sk-...FuMA",
  "detail": "Model: gpt-5-nano",
  "last_verified": "2026-09-21T10:32:02.059431Z"
}
```
`configured: true` confirms the key loaded from `.env` into `Settings`. (`status: "down"` is a network-reachability finding, not a config-loading problem — see §7.)

`openai_temperature` has a field validator (`_blank_temperature_is_unset`) that treats a blank `.env` value as `None` rather than a validation error — necessary because `gpt-5` reasoning models reject the `temperature` parameter outright. Confirmed by reading `openai_provider.py::_request_kwargs`, which omits `temperature`/`reasoning` from the request unless explicitly set, and by `test_temperature_is_not_sent_by_default` (passing).

## 2. API Key Reading — Pass

`OpenAIProvider._get_client()` (`app/llm/openai_provider.py:39`) passes `settings.openai_api_key` directly into `AsyncOpenAI(api_key=...)`. `is_configured` correctly reports `bool(settings.openai_api_key)`. No transformation, truncation, or re-encoding happens between `.env` and the SDK client — verified by reading the code path and by the live `configured: true` result above.

## 3. Health/Dependency Endpoints — Pass

`GET /api/v1/health/dependencies` and `GET /api/v1/settings/status` both call `app/services/dependency_health.py::check_openai()`, which:
- Reports `down` immediately, with no network call, when no key is set (`test_unconfigured_providers_report_down`, passing).
- Makes a real `GET {base_url}/models` call with the configured key when one exists, and maps any exception or non-2xx response to `down` rather than raising (`test_unreachable_provider_is_caught_and_reported_down`, passing).
- Caches results for 30 seconds so the dashboard doesn't trigger a live OpenAI call on every page load (`test_result_is_cached_within_ttl`, passing; matches the 30s poll interval documented in the module's own comments).

Live result with the real key configured: `status: "down"` — this environment cannot reach `api.openai.com` (see §7); the endpoint correctly reflects that rather than reporting a false `operational`.

## 4. AI Summary Generation, End-to-End — Pass (behavior), Blocked (live model call)

Live test against the running local backend:
```
POST /api/v1/tickets  ->  201, ai_summary_status: "PENDING"
(background task runs)
GET  /api/v1/tickets/{id}  ->  ai_summary_status: "FAILED", ticket fully intact
```
Backend log for the above:
```
INFO:hfmg.llm.openai:OpenAI call attempt 1/3 failed (APIConnectionError); retrying in 0.17s
INFO:hfmg.llm.openai:OpenAI call attempt 2/3 failed (APIConnectionError); retrying in 0.32s
WARNING:hfmg.llm.openai:OpenAI call failed after 3 attempt(s): APIConnectionError
WARNING:hfmg.ai.summarizer:AI summary generation failed for ticket eeddedf9-...
```
This confirms the full pipeline wiring — ticket creation → background task → `get_provider()` → `OpenAIProvider.structured()` → retry loop → `set_ai_summary(failed=True)` — executes correctly end-to-end. The one link not exercised live is a *successful* model response, because this development environment cannot reach `api.openai.com` at the network level (§7) — this is an environment limitation, not an application defect. The success path (parsing, persistence, `ai_summary_status: COMPLETED`) is covered by `test_summary_is_stored_on_success` (passing, mocked provider) and matches the exact code path used live, since the only difference is what `provider.structured()` returns.

**Recommendation:** re-run the live ticket-creation test from a network that can reach `api.openai.com` (e.g., the production/staging server) to confirm a real completion end-to-end before relying on this in production.

## 5. Regenerate-Summary Endpoint — Pass (backend); Bug Found and Fixed (frontend)

**Backend:** live test —
```
POST /api/v1/tickets/{id}/regenerate-summary  ->  202, {"ai_summary_status": "PENDING"}
(background task runs)
GET  /api/v1/tickets/{id}  ->  ai_summary_status: "FAILED" (same network limitation as §4)
```
Confirms `mark_summary_pending` + `generate_summary_for_ticket` background task both fire correctly. `test_regenerate_summary_sets_pending_when_enabled`, `test_regenerate_summary_rejected_when_disabled`, and `test_regenerate_summary_requires_existing_ticket` all pass.

**Frontend bug found:** `frontend/src/components/tickets/AISummaryPanel.tsx` shipped with the Regenerate button permanently `disabled`, with a comment claiming *"the backend endpoint for it hasn't been built"* and that it "does not exist on the running backend." This was false — the endpoint exists and works, as demonstrated above. No API client function or mutation hook for it existed anywhere in the frontend (`api/client.ts`, `api/tickets.ts`), so even if the button were enabled, nothing would have called the endpoint.

**Fix applied** (in scope: this is a working backend feature the frontend never wired up, discovered during this verification pass):
- Added `api.regenerateSummary(id)` to `frontend/src/api/client.ts`.
- Added `useRegenerateSummaryMutation(ticketId)` to `frontend/src/api/tickets.ts`, which optimistically sets `ai_summary_status` to `PENDING` on success so the existing 3-second poll in `useTicketQuery` picks up the result without a manual refresh.
- Updated `AISummaryPanel` to accept `onRegenerate`/`isRegenerating`, enabling the button whenever status is `COMPLETED` or `FAILED` (disabled while `PENDING` or when the feature is `DISABLED`, with an explanatory tooltip in the `DISABLED` case).
- Wired it into `TicketDrawer.tsx`.
- `tsc -b` passes clean after the change; no other call sites of `AISummaryPanel` existed.

## 6. Error Handling — Pass (via unit tests; live-verified where the environment allows)

| Scenario | Covered by | Result |
|---|---|---|
| Invalid/bad-request-shaped error (401/400-class) | `test_does_not_retry_non_retryable_error` | Single attempt, no retry, returns `None` — matches the code comment that a 401 "will fail identically on every attempt." `AuthenticationError` and `BadRequestError` are both excluded from the `RETRYABLE` tuple and hit the same generic `except Exception` branch. |
| Quota exceeded (`RateLimitError`) | `test_retries_rate_limit`, `test_gives_up_after_max_retries` | Retried with exponential backoff + jitter, then gives up gracefully after `max_retries`, returning `None` rather than raising. |
| Network failure (`APIConnectionError`) | `test_retries_transient_error_then_succeeds`, `test_gives_up_after_max_retries`, and **live** (§4/§5, real `APIConnectionError` from this environment's network restriction) | Retried, then degrades to `None` on exhaustion — live behavior matches the mocked test exactly. |
| Timeout | `test_timeout_returns_none` | `asyncio.wait_for` enforces the timeout budget independently of the SDK's own timeout; exceeding it returns `None`, logged as a warning. |

All four categories return `None` rather than raising past the provider boundary — `generate_summary_for_ticket` and `nlu.interpret_*` both treat `None` as an ordinary, expected outcome (a failed turn / failed summary), not an exception to catch. This was true in every code path read and every test executed; no gap found.

**Untested-live gap (documented, not a defect):** an actual `AuthenticationError` (invalid key rejected by OpenAI's real API) could not be produced live because this environment cannot reach `api.openai.com` at all (§7) — the network-level `APIConnectionError` occurs before any HTTP round-trip completes. `test_does_not_retry_non_retryable_error` covers the identical code path (any non-`RETRYABLE` exception type) using `BadRequestError`, so the behavior is exercised, just not with that exact exception class.

## 7. Fallback Behavior When OpenAI Is Unavailable — Pass

Confirmed at every layer:
- **Ticket creation** never depends on summarization succeeding — `create_ticket` returns `201` before the background task even starts (`generate_summary_for_ticket` runs via `BackgroundTasks`, fire-and-forget).
- **A failed/unreachable provider** leaves the ticket fully usable: `test_provider_failure_marks_summary_failed` confirms `ticket.description` and all other fields are untouched, only `ai_summary_status` becomes `FAILED`. Verified live in §4.
- **The frontend** (`AISummaryPanel.tsx`, `STATUS_COPY`) shows *"AI summary generation failed. You can rely on the description above."* for `FAILED`, rather than a blank or broken state.
- **Voice NLU** (`app/voice/nlu.py`, shares the same `OpenAIProvider`) treats a `None` result as `failed=True` on the conversational turn and escalates rather than misinterpreting a caller — confirmed by `test_provider_failure_produces_failed_turn` and `test_keyword_escalation_works_when_nlu_is_down`.

**Root cause of `status: "down"` in this environment (informational, not an application bug):** direct HTTPS to `api.openai.com` fails at the TLS layer from this machine —
```
curl -v https://api.openai.com/v1/models ...
schannel: next InitializeSecurityContext failed: SEC_E_LOGON_DENIED (0x8009030c)
```
— while other HTTPS hosts (`pypi.org`, `npmjs.com`) succeed, and DNS resolves `api.openai.com` correctly to Cloudflare-fronted IPs. This points to a network-level restriction (proxy/firewall/TLS-inspection) specific to this host or domain, not a bug in the application. **Recommendation:** verify outbound HTTPS to `api.openai.com:443` is permitted from wherever this is actually deployed (production/staging), independent of this finding.

## 8. Secret Exposure Check — Pass

**Logs:** every `logger.*` call touching the OpenAI path logs only that a key is *missing* or an exception *type name* (e.g. `APIConnectionError`), never the key value or request headers:
```
app/llm/openai_provider.py:136,180: logger.warning("OPENAI_API_KEY is not set; cannot call the model")
```
Live log capture during §4/§5's real API calls contained no key material — confirmed by reading the actual `uvicorn --reload` output captured during this session.

**API responses:** `GET /api/v1/settings/status` only ever returns `mask_secret(settings.openai_api_key)` (`"sk-...FuMA"` format — first 3 + last 4 characters), never the raw value. `app/core/masking.py::mask_secret` is the only function touching the raw key for this purpose. Confirmed live and by `test_settings_status_masks_configured_secrets` / `test_settings_status_reports_not_configured` (passing).

**Frontend:** `grep -rn "OPENAI" frontend/src` returns only two comments referencing the env var *name* (`ReadOnlyConfigPanel.tsx`, `api/settings.ts`) plus this report's own filename — no key literal, and no code path reads or displays an unmasked key. `VITE_API_BASE_URL` is the only OpenAI-adjacent env var the frontend build ever sees.

**Settings endpoint:** covered above under API responses — write path doesn't exist at all (by design, per `DESIGN.md` §12), so there is no way to even round-trip a key through it.

No exposure found in any of the four checked surfaces.

---

## Test Results Summary

Full backend suite, run against a disposable `hfmg_helpdesk_test` database with `ENABLE_AI_SUMMARY` explicitly forced to its shipped default (`false`) to avoid local-`.env` leakage (see Failures Found #1):
```
95 passed in 11.60s
```
OpenAI-specific suites (`test_llm_openai.py`, `test_summarizer.py`, `test_health_dependencies.py`, `test_settings_api.py`) — 32 tests, all passing, covering request shape, retry policy, schema validation, failure handling, caching, and secret masking.

## Failures Found

1. **Test-suite fragility (not a production defect):** `test_tickets_api.py::test_create_and_get_ticket` and `::test_regenerate_summary_rejected_when_disabled` assume `ENABLE_AI_SUMMARY=false` (the shipped default) but don't `monkeypatch` it themselves, unlike every AI-summary test in `test_summarizer.py`. Running pytest with a local `.env` that has `ENABLE_AI_SUMMARY=true` (as this session's dev environment now does) makes both tests fail on an assertion mismatch (`PENDING` vs `DISABLED`) — the app itself behaved correctly (graceful degradation to `FAILED` after a real network error), only the test's assumption was wrong. **Not fixed** (out of scope for this verification-only pass; flagged as a recommendation below rather than a code change, since it's test hygiene, not an integration bug).
2. **Frontend Regenerate button wired to nothing** (§5) — the only real integration defect found. **Fixed** (see §5 for the exact change).

## Fixes Applied

- `frontend/src/api/client.ts` — added `regenerateSummary(id)`.
- `frontend/src/api/tickets.ts` — added `useRegenerateSummaryMutation(ticketId)`.
- `frontend/src/components/tickets/AISummaryPanel.tsx` — button now calls the real endpoint instead of shipping permanently disabled with a stale/incorrect comment.
- `frontend/src/components/tickets/TicketDrawer.tsx` — wires the mutation to the panel.

No backend code was changed — the backend's OpenAI integration was correct and required no fixes.

## Production Recommendations

1. **Verify outbound connectivity to `api.openai.com:443`** from the actual production/staging host before relying on AI summaries there — this dev environment's network could not reach it (§7), and that must be ruled out before go-live.
2. **Run one real (non-mocked) ticket creation** against a network with OpenAI access to confirm an actual `COMPLETED` summary end-to-end — every code path is verified, but no live success response was observed in this session due to the network restriction above.
3. **Rotate the API key used during this session.** It was shared in plaintext over a chat channel while wiring up local development; treat it as exposed regardless of whether this specific environment could reach OpenAI with it.
4. **Harden `test_tickets_api.py`** (Failures Found #1) by adding `monkeypatch.setattr(settings, "enable_ai_summary", False)` to the two affected tests, or an autouse fixture that pins it for the whole file — so the suite's correctness doesn't depend on a contributor's local `.env`.
5. **Consider alerting on sustained `openai: down`** in `/health/dependencies` in production — today it's dashboard-only signal with no paging, and a real outage would look identical to this environment's network restriction unless someone checks the dashboard.

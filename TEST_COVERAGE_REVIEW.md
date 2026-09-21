# Test Coverage Review — HFMG AI Help Desk

Date: 2026-09-21

## Overview

This review covers the FastAPI backend (`backend/app`) test suite under `backend/tests/`
(pytest + pytest-asyncio, `asyncio_mode = auto`) and the React frontend (`frontend/`).

The backend suite was already substantial and well-patterned: 12 existing test files
covering tickets, analytics, AI insights, settings, health, dependency health, the
summarizer, SendGrid, OpenAI, and the full voice/Twilio stack, all using consistent
monkeypatch-the-provider mocking so nothing needs live credentials. This pass:

1. Found and fixed a real hermeticity bug in `backend/tests/conftest.py` that was
   causing the suite to make live OpenAI API calls and fail non-deterministically
   whenever run against this machine's local `backend/.env`.
2. Added 15 new passing tests closing the highest-value gaps found: the
   `regenerate-summary` endpoint's success/failure paths, ticket validation edge
   cases, analytics zero-data edge cases, and the entirely-untested `GET /categories`
   endpoint.
3. Confirmed the frontend has zero test coverage and no test framework installed,
   as stated in the task — this pass does not add frontend tooling.

## Baseline

**Important finding first:** `backend/.env` (this developer's local dev config) has
`ENABLE_AI_SUMMARY=true` and a real `OPENAI_API_KEY` set. Since
`backend/tests/conftest.py` had no fixture forcing these off, and
`app/core/config.py`'s `Settings` loads `env_file=".env"`, running `pytest` from
`backend/` picked up these live values. Every ticket-creation test's background task
then placed a **real ~10-20s call to `api.openai.com`**, which:

- made the suite take 5-14 minutes instead of ~11 seconds,
- made 2-4 tests fail non-deterministically (e.g. `test_create_and_get_ticket`
  asserting `ai_summary_status == "DISABLED"` but observing `"PENDING"`), and
- widened the timing window in which the suite's shared Postgres test database
  raced with a concurrent workstream also hitting `hfmg_helpdesk_test` at the same
  time (this repo's parallel "production hardening" pass), producing additional
  lock-contention and unique-constraint failures unrelated to any code defect.

First raw run (contaminated by both the live-OpenAI issue and DB contention from the
concurrent workstream): **22 failed, 69 passed, 4 errors** (849s). A clean re-run
after the contention cleared but before the fix: **4 failed, 90 passed, 1 error**
(318s) — all 4 failures traced directly to the live-OpenAI/`.env` issue above.

**Fix applied** (in scope: `backend/tests/conftest.py` only, no `app/` changes): added
an autouse fixture forcing `settings.enable_ai_summary = False` and
`settings.openai_api_key = ""` for every test by default. Tests that exercise the
AI-summary path already monkeypatch `enable_ai_summary` back to `True` themselves and
always replace the provider with a fake (see `test_summarizer.py`, and the new
`regenerate-summary` tests below), so they're unaffected.

**Clean baseline after the fix, before adding any new tests: 95 passed, 0 failed, 10.81s.**
This is the true baseline pass count.

## Coverage Map

| Module | Endpoint / Method | Status |
|---|---|---|
| `tickets.py` | `POST /tickets` | Tested (happy path, empty description) |
| | `POST /tickets` — unknown/inactive category | **Was untested → added** |
| | `GET /tickets` (list/filter/search/pagination) | Tested (status, priority, source, q); pagination validation **was untested → added** |
| | `GET /tickets/{id}` | Tested happy path; 404 **was untested → added** |
| | `POST /tickets/{id}/regenerate-summary` | Partially tested (409 disabled, 202+unconfigured, 404) → **success/failure-completion paths added** |
| | `POST /tickets/{id}/status` | Tested (valid/invalid transition); 404 and terminal-state (CLOSED) rejection **were untested → added**; same-status no-op **added** |
| `categories.py` | `GET /categories` | **Entirely untested → new test file added** |
| `analytics.py` | `/kpis`, `/recent-activity`, `/tickets-by-category`, `/tickets-by-priority`, `/tickets-by-source`, `/calls-by-day`, `/escalation-rate`, `/ai-summary-usage` | Tested for populated data; zero-data (`escalation-rate`, `ai-summary-usage`) and `days` boundary validation **were untested → added** |
| `ai_insights.py` | `GET /ai-insights` | Tested (ready + blocked sections) |
| `settings.py` | `GET /settings/status` | Tested (masking, not-configured, DB/env fields, no-leak) |
| `health.py` | `/health`, `/health/ready` | Tested (ok + 503 on DB failure) |
| `health` (dependencies) | `/health/dependencies` | Tested (down, operational, exception-caught, cache TTL) |
| `voice_calls.py` | list/filter/detail/summary | Tested thoroughly |
| Twilio webhooks | voice/gather/status, signature validation | Tested thoroughly (valid/invalid signature, idempotency, abandonment salvage) |
| `services/ticket_service.py` | create/get/list/update_status/mark_summary_pending/set_ai_summary | All covered, now including 400/404/422 edge cases |
| `services/analytics_service.py` | all aggregation queries | Covered including new zero-data cases |
| `services/ai_insights_service.py` | `get_ai_insights` | Covered |
| `services/dependency_health.py` | all `check_*` + caching | Covered |
| `services/voice_call_service.py` | list/get/summary | Covered |
| `app/ai/summarizer.py` | `generate_summary_for_ticket` | Covered at unit level (success/disabled/unconfigured/failure/empty) and now at API level via regenerate-summary |
| `app/notifications/*` | SendGrid provider + `send_ticket_notification` | Covered thoroughly (retries, no-PHI-in-logs, fixed from/to) |
| `app/llm/openai_provider.py` | `structured`/`text`, retries, timeouts | Covered thoroughly |
| `app/llm/factory.py`, `app/notifications/factory.py` | provider selection + unknown-provider fallback | Untested (trivial dict lookup + log line — low value, not addressed) |

## Untested Services

- `app/llm/factory.py::get_provider` — the "unknown `LLM_PROVIDER` value falls back to
  OpenAI with a logged error" branch has no test. Low risk (single `if`, no I/O).
- `app/notifications/factory.py::get_email_provider` — same pattern, same low risk.

Everything else in `app/services/` has at least one direct test exercising it.

## Untested API Paths (before this pass)

- `GET /api/v1/categories` — zero tests existed; the `category_id` fixture in
  `conftest.py` creates categories directly via the ORM, so no test ever actually
  hit this read endpoint. **Fixed.**
- `POST /tickets/{id}/regenerate-summary` — the 202-and-actually-completes and
  202-and-actually-fails paths (i.e. the background task's real effect, not just the
  synchronous response) were never asserted. **Fixed.**
- `GET /tickets/{id}` 404 and `POST /tickets/{id}/status` 404 — no test ever hit a
  ticket ID that doesn't exist on these two routes. **Fixed.**

## Missing Edge Cases (identified; highest-value ones fixed, see below)

- Create ticket with an unknown `category_id` → 400 — **untested → added**.
- Create ticket with an inactive category → 400 — **untested → added**.
- Status transition out of a terminal state (`CLOSED`/`CANCELLED`) → 422 — **untested → added** (for `CLOSED`).
- Status transition to the same status (idempotent no-op, allowed even though it's
  not in `VALID_STATUS_TRANSITIONS`) → 200 — **untested → added**.
- `GET /tickets` with `page=0` or `page_size=0`/`>100` → 422 — **untested → added**.
- `escalation-rate` / `ai-summary-usage` with zero rows in the window (division-by-zero
  guard) → **untested → added**.
- `days` query param boundary (`0`, `366`) on windowed analytics endpoints → 422 —
  **untested → added** (spot-checked on one endpoint; all seven share the same
  `Query(30, ge=1, le=365)` pattern).
- `regenerate-summary` when the provider is configured but the OpenAI call itself
  fails (returns no usable summary) → ticket ends in `FAILED`, not stuck in
  `PENDING` — **untested → added**. This was explicitly called out as a priority in
  the task and was the one meaningfully missing case in an otherwise well-tested
  endpoint.
- Not fixed (time-boxed, lower value): status transition out of `CANCELLED` (same
  shape as the `CLOSED` case now covered); `TicketCreate` field-length boundaries
  (e.g. `description` at exactly 10,000 chars, `caller_name` at 200); `EmailStr`
  rejecting a malformed email; category name uniqueness constraint surfacing as a
  clean error rather than a raw 500 (no endpoint creates categories today, so this
  is currently unreachable from the API).

## Missing Integration Tests

- **Ticket creation → AI summary generation → email notification**, as one
  end-to-end chain, is not tested anywhere as a single flow. Each leg is tested in
  isolation (ticket creation in `test_tickets_api.py`, summary generation in
  `test_summarizer.py`/the new regenerate-summary tests, email notification in
  `test_email_sendgrid.py`), and `create_ticket`'s route schedules both background
  tasks (`app/api/v1/tickets.py:32-33`), but no test asserts both side effects fire
  together off a single `POST /tickets` call with both features enabled and mocked.
  Not added in this pass — time-boxed in favor of the regenerate-summary gap, which
  the task flagged as the priority, but this is the single highest-value remaining
  gap (see below).
- **Voice call → ticket creation → analytics** — `test_voice_agent.py` and
  `test_voice_webhooks.py` verify ticket creation from a call, and
  `test_analytics_api.py` verifies calls/escalations show up in KPIs from directly
  seeded `VoiceCallSession` rows, but no test drives a full call through the
  orchestrator and then asserts the resulting ticket appears correctly in
  `/analytics/*` or `/ai-insights`. Lower value than the email chain above since
  each half is well covered independently.

## Tests Added In This Pass

All 15 pass against `hfmg_helpdesk_test` (verified: 95 → 110 passed, 0 failed, 11.33s).

**`backend/tests/conftest.py`** (fix, not a test):
- `_hermetic_ai_summary_defaults` — autouse fixture forcing `enable_ai_summary=False`
  and `openai_api_key=""` by default so the suite never depends on this machine's
  `.env` or makes live OpenAI calls.

**`backend/tests/test_tickets_api.py`**:
- `test_regenerate_summary_completes_successfully` — regenerate-summary end-to-end
  with a fake-but-configured provider that succeeds; asserts `ai_summary_status`
  reaches `COMPLETED` and `ai_summary` is populated after the background task runs.
- `test_regenerate_summary_marks_failed_when_openai_call_fails` — same flow with a
  provider that returns `None` (simulating a real OpenAI failure); asserts the
  ticket lands in `FAILED`, not stuck in `PENDING`. This was the specific gap the
  task called out.
- `test_create_ticket_rejects_unknown_category` — 400 for a `category_id` that
  doesn't exist.
- `test_create_ticket_rejects_inactive_category` — 400 for a category with
  `is_active=False`.
- `test_get_ticket_404_for_unknown_id` — 404 on `GET /tickets/{id}`.
- `test_update_status_404_for_unknown_ticket` — 404 on `POST /tickets/{id}/status`.
- `test_status_transition_rejected_from_terminal_closed_state` — walks a ticket to
  `CLOSED`, then asserts any further transition is 422.
- `test_status_transition_to_same_status_is_a_noop` — `NEW → NEW` returns 200
  (documents the `update_status` early-exit behavior for same-status).
- `test_list_tickets_rejects_invalid_pagination` — `page=0`, `page_size=0`, and
  `page_size=101` all 422.

**`backend/tests/test_analytics_api.py`**:
- `test_ai_summary_usage_with_no_tickets_returns_zero_without_error` — empty window,
  no `ZeroDivisionError`, all four statuses zero-filled.
- `test_escalation_rate_with_no_calls_returns_zero_without_error` — empty window,
  `rate_percent == 0.0` rather than raising.
- `test_analytics_days_param_rejects_out_of_range_values` — `days=0` and `days=366`
  both 422.

**`backend/tests/test_categories_api.py`** (new file):
- `test_list_categories_empty_when_none_exist` — `GET /categories` with no data.
- `test_list_categories_returns_active_sorted_by_name` — confirms alphabetical
  ordering (`ORDER BY name`), not insertion order.
- `test_list_categories_excludes_inactive` — confirms `is_active=False` categories
  are filtered out.

## Frontend Test Coverage

**Zero.** Confirmed: `frontend/package.json` has no `test` script and no
`vitest`/`jest`/`@testing-library/*` in `dependencies` or `devDependencies`. There
are no `*.test.*` or `*.spec.*` files anywhere under `frontend/src` (104 `.ts`/`.tsx`
source files, 0 test files). No frontend test tooling was installed in this pass, per
the task's explicit instruction.

**Recommendation (not actioned):** Vitest + React Testing Library is the natural fit
given the stack (Vite + React 19 + TanStack Query) — it reuses the existing Vite
config/transform pipeline (no separate Babel/webpack setup needed, unlike Jest) and
has first-class support for mocking `fetch`/TanStack Query. Priority order if this is
picked up: (1) the ticket detail view's regenerate-summary button/status display,
since that's the newest wired-up feature and has no coverage on either side today
beyond what this pass added on the backend; (2) the analytics dashboard's handling of
the `blocked`/`ready` KPI status states, since a frontend bug there would silently
mis-render a real backend signal; (3) form validation on the ticket creation form.

## Remaining Gaps Not Addressed

Time-boxed to the gaps above; explicitly not pursued in this pass, with reasoning:

- **Full ticket-creation → summary → email integration test.** Highest-value
  remaining gap (see above). Not added because the task scoped this pass to
  "regenerate-summary + 3-5 other high-value gaps," and each leg of this chain
  already has direct coverage; the marginal value of also asserting them together is
  real but smaller than the gaps that were fully untested (categories API,
  regenerate-summary's failure path).
- **`app/llm/factory.py` / `app/notifications/factory.py` unknown-provider fallback
  branches.** Trivial, low-risk code (one `if`, one log line); not worth a test slot
  given the time-box.
- **Field-length/format boundaries on `TicketCreate`** (description at exactly
  10,000 chars, caller_name at 200, malformed email). Pydantic-level validation that
  the framework itself guarantees works; testing it here would mostly be testing
  Pydantic, not this codebase's logic.
- **Voice-call-to-analytics full round trip.** Lower value than the email chain
  since both halves already have solid independent coverage (see Missing Integration
  Tests above).
- **Concurrent-update races on ticket status** (e.g. two simultaneous
  `POST .../status` calls). `update_status` does a read-then-write with no
  row-level locking (`SELECT` then `UPDATE` in separate statements), so a genuine
  race is possible in principle, but reliably exercising it needs two concurrent
  sessions racing against the same row, which is a meaningfully bigger test (and
  arguably belongs with the parallel hardening workstream's error-handling changes,
  since fixing it would mean changing `app/services/ticket_service.py`, which is
  explicitly out of scope for this pass).

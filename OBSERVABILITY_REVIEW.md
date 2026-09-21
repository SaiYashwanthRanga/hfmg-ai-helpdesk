# HFMG AI Help Desk — Observability Review

**Scope:** API logging, OpenAI logging, request tracing, error reporting, health monitoring, metrics, alerts, dashboards.
**Not in scope / not actionable here:** Twilio voice observability is blocked on Twilio credentials (no `TWILIO_ACCOUNT_SID` in `app/core/config.py`, so no real Twilio API can be called) — the voice *logging code* is reviewed below for what it captures today, but "wire up real Twilio call-quality monitoring" is not listed as a gap to fix.
**Method:** every claim below is backed by a file:line citation from the current code, or an explicit note that a capability was searched for and not found. Nothing here is inferred from architecture docs alone without checking the code.

---

## Overview

The backend has two very different observability postures depending on the code path:

- **The voice call path** (`app/voice/routes.py`, `app/voice/orchestrator.py`) has genuinely useful, PHI-conscious logging: every turn logs `call=<sid> state=<state> misunderstandings=<n> stt_confidence=<c>`, and every failure/escalation/fallback is logged with the CallSid as a natural correlation ID.
- **Everything else** — every REST endpoint in `app/api/v1/` (tickets, analytics, categories, ai_insights, voice_calls, settings, health) and every service in `app/services/` — has **zero logging**. Not a single `logger.*` or `print()` call exists in any of these 9 files (1,015 lines combined). A request that 500s in `TicketService.create_ticket` produces no application log line at all; the only trace is whatever FastAPI's default handler and uvicorn's access log happen to emit.

There is no request-ID/correlation-ID middleware, no structured (JSON) logging, no exception handler beyond FastAPI's default, and no external error-tracking integration (Sentry or otherwise) anywhere in the codebase or `pyproject`/`requirements` — confirmed by grep across `backend/app` and `frontend/src`, and by the absence of any such package in dependency files. `OPERATIONS_RUNBOOK.md` already documents an intended monitoring posture (§3) that is more aspirational than implemented in a few specific ways called out below — this review cites those gaps rather than duplicating the runbook's content.

---

## Current State

### API Logging

- `backend/app/main.py:9` — the entire application logging setup is `logging.basicConfig(level=logging.INFO)`. No formatter, no request/response middleware, no timing.
- `backend/app/main.py:15-21` — the only middleware registered is `CORSMiddleware`. There is no logging middleware, no request-ID middleware, no timing middleware.
- Every route handler in `backend/app/api/v1/tickets.py`, `analytics.py`, `categories.py`, `ai_insights.py`, `voice_calls.py`, `settings.py`, and `health.py` (and every function in the corresponding `app/services/*.py` files: `ticket_service.py`, `analytics_service.py`, `ai_insights_service.py`, `voice_call_service.py`, `dependency_health.py`) contains **no logging calls whatsoever** — verified by `grep -rn "logging\.|logger\.|print\(" backend/app/api/v1 backend/app/services`, which returns zero matches in any of those 9 files.
- Practical consequence: creating a ticket, updating a ticket's status, regenerating an AI summary, listing analytics, or hitting any health endpoint produces no application-level log line — success or failure. The only exception, `app/ai/summarizer.py:56,78`, logs when AI summary generation is skipped/fails, but that module lives under `app/ai/`, not `app/services/`, and is invoked as a `BackgroundTask`, not from the request path itself.
- No endpoint logs latency, status code, ticket ID, or any other per-request context. There is no way to answer "how long did `POST /tickets` take for ticket X" or "what happened right before this 500" from application logs alone.
- Whatever request logging does exist comes entirely from uvicorn's default access logger (not configured anywhere in this repo — it's uvicorn's out-of-the-box behavior when run via its CLI), which logs method/path/status/duration but carries zero application context (no ticket ID, no correlation to the app-level log lines that do exist for voice calls).

### OpenAI Logging

- `backend/app/llm/openai_provider.py:19` — dedicated logger `hfmg.llm.openai`.
- What **is** logged: retryable-error attempts with backoff delay (`:105-111`), final failure after exhausting retries (`:98-100`), timeout exceeded (`:115`), non-retryable errors via `logger.exception` (`:120`), missing API key (`:136,180`), and unparseable/non-object JSON responses (`:162,166`).
- What is **not** logged: no prompt or response content (deliberate and correct for privacy — do not add this), but also **no token usage** (the OpenAI Responses API response includes usage data that is never read or logged — `response.output_text` is the only field consumed, `:94`), **no latency/duration** per call, **no model name** in the log lines themselves (it's in `settings.openai_model` but never included in a log statement), and **no correlation** back to which ticket or call triggered the request — a log line reading "OpenAI call attempt 1/3 failed (RateLimitError)" cannot be tied to a specific ticket without cross-referencing timestamps.
- Success is never logged at all — only failures/retries are. There's no way to derive "how many OpenAI calls succeeded today" from logs; it would have to come from the `ai_summary_status` field already tracked per-ticket in the DB (surfaced via `GET /api/v1/analytics/ai-summary-usage`, `backend/app/api/v1/analytics.py:83-86`) — but that only covers the summarizer path, not the voice NLU path.
- **`backend/app/voice/nlu.py:18`** declares `logger = logging.getLogger("hfmg.voice.nlu")` but **never calls it** — grep confirms zero `logger.*` calls anywhere in this 343-line file, despite every one of its six `interpret_*` functions being able to silently fail (returns a default `TurnResult()` on `data is None`, i.e., when the underlying `OpenAIProvider.structured()` call failed or returned unparseable JSON, `nlu.py:216-217` and similarly at `:248-249,274-275,304-305,334-335`). This is a **documentation/implementation mismatch**: `OPERATIONS_RUNBOOK.md:144` states the `hfmg.voice.nlu` logger "Covers: Caller-speech interpretation failures," but the code does not actually log any such failures — the underlying `hfmg.llm.openai` logger only shows a generic API-layer failure, not that a specific NLU extraction (e.g., "couldn't parse a phone number") failed.

### Request Tracing

- No request-ID / correlation-ID mechanism exists anywhere in the backend. Confirmed by grep for `request_id`, `X-Request-ID`, `correlation`, and `add_exception_handler`/custom middleware across `backend/app` — zero matches beyond the CORS middleware import.
- **Voice calls are the one path with a usable correlation ID today**, and it's a good pattern: `session.twilio_call_sid` (the Twilio `CallSid`) threads through every log line in `app/voice/routes.py` (`:65,85,94,99,105-111,147,150,160,169`) and `app/voice/orchestrator.py` (`:87`). `OPERATIONS_RUNBOOK.md:150-153` documents `journalctl -u hfmg-api | grep "CAxxxxxxxxxxxxxxxx"` as the way to trace one call end-to-end — this works because the CallSid is genuinely in every relevant log line.
- **No equivalent exists for the web/API path.** A ticket created via `POST /tickets`, its subsequent AI summary generation, and its email notification are three separate operations with no shared identifier logged anywhere (and as noted above, the ticket-creation and summary paths don't log at all, so there's nothing to correlate even if an ID existed). If a caller reports "ticket HFMG-1234's summary never generated," there is no log-based way to trace what happened — only the DB's `ai_summary_status` field (`failed`/`pending`/`generated`) tells you the outcome, not why.
- No trace propagates across the FastAPI → BackgroundTasks boundary either: `_dispatch_ticket_tasks` (`backend/app/voice/routes.py:33-43`) and the equivalent in `tickets.py` fire `send_ticket_notification` and `generate_summary_for_ticket` as detached background tasks with no shared ID logged that ties them back to the originating HTTP request.

### Error Reporting

- **Backend:** No custom exception handlers are registered (`backend/app/main.py` has none), so any unhandled exception falls through to FastAPI/Starlette's default handler, which returns a generic `{"detail": "Internal Server Error"}` (500) and prints a traceback via uvicorn's own error logging — not via the application's `hfmg.*` loggers, so it won't be filtered/found the same way as the rest of the app's logs. There is no Sentry, Rollbar, or any other external error-tracking SDK anywhere in the codebase (checked `backend/app` imports and `frontend/src` imports — none found).
- The one place the backend does catch and handle unhandled exceptions deliberately is the voice webhook path (`app/voice/routes.py:64-66,93-100,168-169`), which logs via `logger.exception(...)` and degrades gracefully to a TwiML apology + hangup instead of a raw 500 — this is good defensive design, but it's specific to voice; the equivalent REST endpoints (`tickets.py`, etc.) have no such handling and would surface FastAPI's default 500 with no application log line.
- **Frontend:** `frontend/src/api/client.ts:14-21,29-37` and `frontend/src/api/voiceCalls.ts:9-17` convert non-2xx responses into a typed `ApiError`, and at least one call site (`frontend/src/components/tickets/TicketStatusControl.tsx:39-40`) surfaces it to the user via a toast (`toast.error(...)`). That's a good UX pattern, but nothing logs the error anywhere — no `console.error`, no network beacon to a backend endpoint, no third-party error tracker. Grepped `frontend/src` for `console.error`/`console.warn`/`Sentry`/`ErrorBoundary`/`componentDidCatch` — the only hit is an unrelated dev-time accessibility warning in `frontend/src/components/ui/Button.tsx:41` (`console.warn` for missing `aria-label` on icon buttons).
- **No React error boundary exists anywhere in the render tree.** `frontend/src/main.tsx` renders `<App />` directly inside `<StrictMode><BrowserRouter>` with no boundary; `frontend/src/app/AppShell.tsx` likewise has none. A rendering exception in any component produces React's default behavior (unmounts to a blank screen in production builds) with no user-facing message and nothing logged anywhere. `frontend/src/components/ui/ErrorState.tsx` is a well-designed *presentational* component for showing API/data errors gracefully, but it is not wired to catch render-time exceptions — it only renders when a caller explicitly passes it an error state.

### Health Monitoring

- `GET /health` (`backend/app/api/v1/health.py:12-14`) always returns `{"status": "ok"}` — it does not touch the database or any dependency. It is liveness-only: it tells you the process is alive and can answer HTTP, nothing more. This matches what `OPERATIONS_RUNBOOK.md:70` already documents ("A healthy response does not mean the system is working") — no discrepancy here.
- `GET /health/ready` (`:17-32`) runs `SELECT 1` against the DB and returns 503 on failure — a real readiness check, suitable for a load balancer or an uptime monitor that wants "is the core dependency (DB) reachable."
- `GET /health/dependencies` (`:35-46`, backed by `backend/app/services/dependency_health.py`) reports OpenAI, Twilio, Database, and Email status in one call:
  - `check_openai` (`dependency_health.py:72-85`) makes a real `GET /v1/models` call against the OpenAI API.
  - `check_email` (`:99-113`) makes a real `GET /v3/scopes` call against SendGrid.
  - `check_database` (`:64-69`) runs `SELECT 1`.
  - `check_twilio` (`:88-96`) is **not** a real reachability check — it only checks whether `TWILIO_AUTH_TOKEN` is set, because the app has no `TWILIO_ACCOUNT_SID` config to make a real authenticated Twilio API call. This is explicitly commented as a deliberate scope decision (`:89-95`), not a hidden gap, and per this review's instructions is not actionable (blocked on credentials/config that don't exist yet).
  - All four checks are cached in-process for 30 seconds (`_CACHE_TTL_SECONDS = 30.0`, `:32`), matching the frontend's 30s status-bar poll (`:29-31`). There is no cache-busting query param, so an operator hitting this endpoint manually during an incident could get a stale (up to 30s old) result.
  - **This module has no logging at all** — grep confirms zero `logger`/`logging` calls in `dependency_health.py`. A dependency flipping from `operational` to `down` (or vice versa) is never logged; it's only observable by diffing successive HTTP responses. An operator tailing logs during an OpenAI outage would see nothing from this module — they'd only see it indirectly via `hfmg.llm.openai` failures on actual ticket/call traffic.
- Suitability for an uptime monitor: `/health` is fine as a pure "is the process up" check but gives false confidence (a DB-down system still returns 200 from `/health`). `/health/ready` is the right endpoint for a standard uptime monitor / load-balancer probe. `/health/dependencies` is well-suited for a status-page/dashboard widget but less suited to alerting directly on, since a single flaky check (e.g., an OpenAI blip within the 4s timeout, `:33`) would flip it to "down" with no debounce/hysteresis — an uptime monitor polling this and alerting on every state change would be noisy.
- `GET /settings/status` (`backend/app/api/v1/settings.py:14-65`) reuses the same cached dependency checks and additionally surfaces masked API keys and which providers are configured — useful for a human debugging config, not intended as a machine-polled health signal.

---

## Missing Metrics

None of the following exist today (verified: no metrics library, no Prometheus/StatsD client, no counters/histograms anywhere in `backend/app`):

- **HTTP-level:** request rate, latency (p50/p95/p99), and error rate (4xx/5xx) per endpoint. `OPERATIONS_RUNBOOK.md:62` already lists "5xx rate >1% of requests over 5 min" as something to alert on, but nothing in the codebase computes or exposes a 5xx rate — this alert condition is currently undeliverable without adding it.
- **OpenAI:** call count, success/failure rate, latency, and token usage (input/output tokens are available in the Responses API response but never read, `openai_provider.py:94`) — no per-call or aggregate signal exists. This matters for cost tracking as much as reliability.
- **Email:** send success/failure rate as a time series (only individual log lines exist today, `notifications/email.py:76,80`; no aggregate).
- **AI summary success rate over time:** partially available — `GET /api/v1/analytics/ai-summary-usage` (`analytics.py:83-86`) derives this from the `ai_summary_status` column on tickets, so the *data* exists in Postgres, but there's no alerting threshold or time-series/trend view on it, and it only covers the summarizer path (not voice NLU calls, which have no persisted outcome at all).
- **Voice containment/escalation rate:** already computed on demand via `GET /api/v1/analytics/escalation-rate` (`analytics.py:77-80`) — this is a genuine existing metric, just not continuously sampled or alerted on.
- **Background task outcomes:** `BackgroundTasks`-based email sends and summary generation (`voice/routes.py:33-43`, and the equivalent in `tickets.py`) have no success/failure counter — a spike in silent background-task failures (e.g., after a service restart mid-flight, which `OPERATIONS_RUNBOOK.md:29` already flags as a known data-loss mode) would be invisible.
- **Process-level:** no memory/CPU/event-loop-lag instrumentation; reasonable to skip for an MVP of this size, but worth noting it doesn't exist if the ops team ever needs to diagnose a slow degradation rather than a hard failure.

## Missing Alerts

- **Everything in `OPERATIONS_RUNBOOK.md`'s alert table (§3.1) is aspirational, not wired up.** There is no alerting tool referenced anywhere in the repo (no PagerDuty/Opsgenie config, no Prometheus Alertmanager rules, no external uptime-monitor config file). The table describes *what an operator should configure in whatever monitoring tool they stand up*, not something this codebase implements. Concretely still missing:
  - OpenAI down / degraded (data exists via `/health/dependencies`, but nothing polls it and pages someone).
  - Database unreachable (covered by `/health/ready`, same gap — no poller/alerting attached).
  - Email provider down or misconfigured — `OPERATIONS_RUNBOOK.md:64` says to alert on a log line matching `Email provider not configured`; that string doesn't actually appear anywhere in `notifications/email.py` or `notifications/factory.py` (the actual log lines are `"Email notifications disabled; skipping..."` at `email.py:50` and `"Unknown EMAIL_PROVIDER %r..."` at `factory.py:28`) — the runbook's documented alert-matching string is stale/inaccurate and would never fire.
  - Elevated error rate — no way to compute this at all today (see Missing Metrics above).
  - Repeated NLU/interpretation failures — undeliverable today since `nlu.py` never logs (see OpenAI Logging above); the runbook's advice to alert on "repeated `hfmg.voice.nlu`...errors" (`:66`) has nothing to match against.
- No dead-letter/failure signal for background tasks: if `generate_summary_for_ticket` or `send_ticket_notification` silently fails or is lost on restart, nothing alerts — an operator would only notice via a ticket sitting in `ai_summary_status=PENDING` or `FAILED` indefinitely, and only if they think to look.

## Missing Dashboards

- **An ops/infra dashboard** showing request volume, latency, and error rate over time cannot be built today — the underlying metrics don't exist (see above).
- **A single "is everything healthy right now" board** is close to derivable from `GET /api/v1/health/dependencies` + `GET /api/v1/analytics/kpis` + `GET /api/v1/analytics/escalation-rate`, but nothing currently assembles these into one view for an operator (the frontend's own status bar polls `/health/dependencies` for the dashboard UI, not for an ops audience).
- **An AI cost/usage dashboard** (calls per day, tokens per day, estimated spend) is not derivable at all — no token usage is ever captured (see Missing Metrics).
- **A background-task health board** (queued/in-flight/failed counts) is not derivable — there's no task registry; `BackgroundTasks` are fire-and-forget with no persisted state of their own (ticket-level `ai_summary_status` is the closest proxy, and only for the summary task).

---

## Recommendations

Ordered by priority. Sized for this MVP's actual constraints: no Docker, no Redis, no Celery, stdlib-first.

### 1. Add a request-ID middleware and put it in every log line (High value / Low effort)
Add one small ASGI middleware in `main.py` that generates a UUID per request (or reuses an inbound `X-Request-ID` header), stores it in a `contextvars.ContextVar`, and attaches it via a `logging.Filter` so every log record — including ones emitted deep in `ticket_service.py` — carries it automatically. This is the single highest-leverage fix: it's what makes every other logging gap in this report actually useful once they're filled in, and it costs nothing beyond stdlib `logging`/`contextvars`. Return the same ID as a response header so the frontend can show it in error toasts ("something went wrong, reference: abc123") for support correlation.

### 2. Add logging to `app/api/v1/*.py` and `app/services/*.py` (High value / Medium effort)
Right now a 500 in ticket creation, status update, or analytics leaves zero application trace. At minimum, log unhandled exceptions in each route (or centralize this via one FastAPI exception handler registered in `main.py` that logs `logger.exception(...)` with the request-ID and path before returning the generic 500 — this also fixes the "voice has good error handling, REST does not" asymmetry noted above with one change). Follow the existing pattern already used well in `voice/routes.py` and `notifications/sendgrid_provider.py`: log outcome and identifiers (ticket ID, endpoint), never PII/PHI content.

### 3. Make `nlu.py` actually log its declared logger (Medium value / Low effort)
Six call sites (`nlu.py:216-217,248-249,274-275,304-305,334-335,+yes_no`) silently return a default `TurnResult()` on any failure with no log line. Add a single `logger.warning("NLU %s failed for call", schema_name)` (no utterance content) at the point `_call_structured` returns `None`. This also closes the gap between what `OPERATIONS_RUNBOOK.md:144` already claims this logger does and what it actually does.

### 4. Fix the stale alert-matching string in the runbook (Low effort / avoids a real incident-response failure)
`OPERATIONS_RUNBOOK.md:64` tells whoever configures alerting to match on `"Email provider not configured"`, which does not appear in the code. Update it to match the real log lines (`email.py:50`, `factory.py:28`), or better, have `notifications/email.py` log a distinct, greppable line once at startup/first-use when `SENDGRID_API_KEY` is unset in a non-development environment (this doesn't exist today) so there's one unambiguous string to alert on.

### 5. Switch to structured (JSON) logging via stdlib only (Medium value / Low-Medium effort)
`main.py:9`'s `logging.basicConfig` produces plain text. A `logging.Formatter` subclass that emits one JSON object per line (stdlib `json` + `logging`, no new dependency) makes every log line — especially once request-ID and ticket-ID are included per recommendation #1/#2 — machine-parseable by `journalctl`/`jq` or any future log aggregator, without needing ELK/Loki/any new service. `OPERATIONS_RUNBOOK.md:138` already flags this as a known gap ("logs are plain text, not JSON (Phase 3 adds structured logging)") — this recommendation is that work.

### 6. Log OpenAI latency and token usage (Medium value / Low effort)
In `openai_provider.py:_call`, wrap the `client.responses.create(...)` call with a start/end timestamp and log duration on every path (success and failure), and read `response.usage` (input/output token counts, available on the Responses API response object) into the success log line. This directly enables cost tracking and a real "AI latency" signal without any new infrastructure — it's a few lines in one function.

### 7. Add a lightweight in-process request/error counter, exposed as JSON (Medium value / Medium effort)
Without pulling in Prometheus, a small stdlib module (a `dict` of counters behind a lock, incremented by the same middleware from #1) tracking request count, 5xx count, and per-provider (OpenAI/SendGrid) success/failure counts since process start — exposed as a new `GET /health/metrics` (or folded into `/health/dependencies`) — would make the runbook's "5xx rate >1%" alert (`OPERATIONS_RUNBOOK.md:62`) and OpenAI/email alert conditions actually computable by an external poller, closing the biggest gap between what the runbook says to alert on and what's currently measurable. This is intentionally not a metrics *server* (no `/metrics` Prometheus endpoint, no new service) — just a JSON snapshot an existing uptime tool can poll and threshold on.

### 8. Add a React error boundary at the `AppShell` level (Medium value / Low effort)
`frontend/src/app/AppShell.tsx` has no error boundary; a render exception currently blanks the screen with nothing logged. Wrap `{children}` in a boundary component that renders the existing `ErrorState` component (`components/ui/ErrorState.tsx`, already designed for exactly this) on catch, and `console.error`s the caught error with the component stack — cheap, and reuses a component that already exists but isn't wired up for this purpose.

### 9. Cache-bust `/health/dependencies` for operator use (Low value / Low effort)
Add an optional query param (e.g. `?fresh=1`) that bypasses the 30s cache in `dependency_health.py:50-57`, so an operator debugging a live incident isn't looking at a result up to 30 seconds stale. Low priority, but trivial to add alongside recommendation #7.

### Deliberately not recommended
- **Sentry or any external error-tracking SaaS** — plausible for a future phase once there's an ops budget/BAA review for a third-party vendor (this system handles PHI-adjacent data — `OPERATIONS_RUNBOOK.md:160` already flags that any log aggregator needs a BAA first), but not sized right for "add lightweight logging" — flagging it as a future option, not a current recommendation.
- **OpenTelemetry / a metrics server (Prometheus, Grafana)** — real value eventually, but this system explicitly has no Docker/Redis by design; standing up a new always-on service to scrape metrics is a bigger step than this MVP's architecture currently takes anywhere else. Recommendation #7 gets most of the practical value (computable alert conditions) without it.
- **Twilio-side call-quality/webhook-failure monitoring** — out of scope per this review's instructions (blocked on Twilio credentials, not a code gap).

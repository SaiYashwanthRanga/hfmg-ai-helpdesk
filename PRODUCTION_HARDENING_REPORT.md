# Production Hardening Report — HFMG AI Help Desk Backend

**Date:** 2026-09-21
**Scope:** `backend/app/api/v1/*.py`, `backend/app/services/*.py`, `backend/app/core/config.py`,
`backend/app/core/masking.py`, `backend/app/notifications/*.py`, `backend/app/main.py`
(read-only review of `backend/app/db/models.py`, `backend/app/voice/*.py`,
`backend/app/llm/*.py`, `backend/app/ai/summarizer.py` per the parallel-workstream
boundaries in the task brief).

## Overview

This codebase was, on inspection, already unusually well-hardened for an MVP: every
outbound HTTP call (OpenAI, SendGrid, dependency-health probes) already has an explicit
timeout and a bounded retry-with-backoff-and-jitter; all pagination and window
parameters already have `ge`/`le` bounds; the Twilio voice webhook path already wraps
every orchestrator call in `try/except` with an escalation fallback; TwiML is built via
the Twilio SDK (safe from injection) rather than hand-built XML; and every
exception-swallowing branch that was found already carries a `logger.warning`/
`logger.exception` call with no request/ticket content leaked into logs. As a result,
the number of genuine, actionable gaps was small. Three low-risk, backward-compatible
fixes were made; the rest are documented below as recommendations with reasoning for
why they were left alone.

**A note on test verification:** while this review was running, the shared Postgres
test database (`hfmg_helpdesk_test`) had heavy concurrent load from what appears to be
at least one other workstream's pytest process running against the same database at the
same time (confirmed via `pg_stat_activity`: sessions `idle in transaction` for 500+
seconds while other sessions queued behind `TRUNCATE`/`DROP TABLE` locks, and duplicate
`pytest -q` processes running from a second, non-venv Python interpreter). A full-suite
run taken while that contention was active produced 22 failures / 6 errors, all of them
`sqlalchemy.exc.InvalidRequestError` ("Could not refresh instance"), row-count assertion
mismatches, or `KeyError`s consistent with another session's `TRUNCATE`/`DROP TABLE`
racing mid-test — not validation errors or response-shape changes, which is what any of
this pass's three edits would produce if they were the cause. See **Changes Made** below
for the targeted, DB-contention-free verification that was used in place of a second
full clean run (which the coordinator asked me to stop waiting on).

---

## Findings by Category

### Unhandled Exceptions

1. **`app/main.py` (whole app) — no global exception handler.**
   Before this change, an exception not caught by any service/route (e.g. an
   unexpected DB error) fell through to Starlette's default handler: it still
   returned a 500 to the client, but never went through this app's own
   `logging.getLogger`-based logging, making it easy to miss in production log
   aggregation.
   **Fixed in this pass** — added `unhandled_exception_handler` in `app/main.py`,
   registered via `@app.exception_handler(Exception)`. It logs the method+path via
   `logger.exception` and returns a generic `{"detail": "Internal server error"}`
   500. `HTTPException` and `RequestValidationError` are explicitly re-raised
   inside it as a defense-in-depth belt-and-suspenders check (FastAPI's own
   more-specific handlers for those two types already take precedence via
   Starlette's MRO-based handler lookup, so existing response shapes for 404s,
   409s, 422s, etc. are unchanged — verified directly, see Changes Made).

2. **`app/notifications/email.py:35-81` `send_ticket_notification` — no
   try/except around the function body.**
   Runs as a FastAPI `BackgroundTask`, which executes *after* the HTTP response
   has already been sent. An unexpected exception here (e.g. an unanticipated
   `.format()` KeyError, or any future change to `_build_body`) would propagate
   past the ASGI response cycle with no app-level log line, and would also abort
   any other queued background tasks for that request silently.
   **Fixed in this pass** — wrapped the body in `try/except Exception` with
   `logger.exception(...)`, following the file's existing "never log ticket
   content, only identifiers" discipline.

3. **`app/ai/summarizer.py:45-82` `generate_summary_for_ticket` — no
   try/except at all**, also runs as a `BackgroundTask`. `get_ticket()` raises an
   `HTTPException(404)` if the ticket ever vanishes mid-flight, and any DB/LLM
   exception is otherwise unhandled.
   **Recommended, not fixed** — this file is explicitly on the task's
   do-not-touch list (owned by the AI/prompt-review workstream). Flagged here for
   that workstream's awareness; the fix would be the same pattern as finding #2
   (wrap the body, log, don't raise).

4. **`app/voice/orchestrator.py` `_category_id()`** could raise
   `NoResultFound` if the seeded "Other" category row is ever missing from a
   deployment's database (a data/config problem, not a code problem).
   **Recommended, not fixed** — no action needed in practice: every caller of
   `_create_ticket`/`_category_id` in the voice flow is already invoked from
   inside a `try/except Exception` in `app/voice/routes.py` (`incoming_call`,
   `gather`, `fallback`), which escalates or plays a system-error TwiML message
   instead of crashing the webhook. Reported for completeness per the task's
   "report obvious unhandled-exception risks in voice/" instruction; no fix
   needed since it's already caught one layer up, and `orchestrator.py` is
   otherwise out of scope for edits.

5. **`app/db/base.py` `get_db()`** — verified, not a bug: `async with
   async_session_factory() as session` calls `AsyncSession.close()` on exit,
   which SQLAlchemy documents as implicitly rolling back any uncommitted
   transaction before returning the connection to the pool. No explicit
   rollback-on-exception code path was needed.

### Missing Validation

1. **`app/schemas/ticket.py` `TicketCreate.caller_name` / `.phone_number` /
   `.description`** — `Field(min_length=1)` alone accepts a whitespace-only
   string (e.g. a single space satisfies `min_length=1`), which would create a
   ticket with an effectively blank caller name, phone number, or description.
   **Fixed in this pass** — added a `field_validator` (`mode="after"`) that
   strips each of these three fields and rejects the value if stripping leaves
   it empty. Also has the side benefit of storing trimmed values instead of
   values with stray leading/trailing whitespace from a web form or voice
   transcript. Verified directly against the running Pydantic model (DB-free):
   a whitespace-only name/description is now rejected, a valid payload is
   trimmed correctly, and the pre-existing `min_length=1` rejection of a
   genuinely empty string (`test_create_ticket_requires_description`) is
   unaffected.

2. **`app/schemas/ticket.py` `TicketCreate.email`** — `EmailStr` checks format
   only. Verified as a non-issue: pydantic's underlying `email_validator`
   package already enforces an RFC-5321-style total-length cap (254 chars) by
   default, so no additional bound was needed.

3. **Query parameters** (`tickets` list `q`, `page`/`page_size` on tickets and
   voice-calls, `days` windows on every analytics/AI-insights endpoint,
   `recent-activity` `limit`) — audited across `app/api/v1/tickets.py`,
   `analytics.py`, `ai_insights.py`, `voice_calls.py`: every one of these
   already has an explicit `ge=`/`le=` bound via FastAPI `Query(...)`. No gap
   found, no fix needed.

4. **`app/voice/nlu.py`** — the LLM's structured output is already
   allowlist-checked before use (`_validate_category`, `_validate_priority`
   against fixed enums) and description/name values are hard-truncated
   (`[:10_000]`, `[:200]`) before being handed to `TicketCreate`. This is
   already solid protection against LLM hallucination or caller-speech
   injection reaching the DB unchecked. No gap found.

### Missing Bounds

1. **Pagination** — `list_tickets`/`list_voice_calls` already cap `page_size`
   at `le=100`; `get_recent_activity` caps `limit` at `le=50`; all analytics
   `days` windows cap at `le=365`. No unbounded query found in any reviewed
   endpoint.

2. **`ticket_service.list_tickets`'s `q` filter** (`app/services/ticket_service.py:94-110`)
   builds `or_(... .ilike(f"%{q}%") ...)` across four columns with no covering
   index — the code comment itself notes the full-text index `ix_tickets_fts`
   the API spec references doesn't actually exist in this database. This can
   become a full sequential scan as the tickets table grows.
   **Recommended, not fixed** — explicitly the performance-review workstream's
   territory (index/migration ownership per this task's scope boundaries).
   Flagged here for that workstream.

3. **`app/db/models.py` index gaps** (read-only per scope, not acted on):
   - `Ticket` has indexes on `ticket_number` (unique), `category_id`,
     `created_at`, but none on `status` alone despite `status`-filtered list
     queries, and nothing supporting the `q` ILIKE search above.
   - `VoiceCallSession` has indexes on `twilio_call_sid` (unique), `ticket_id`,
     `created_at`, but none on `state` despite `state`-filtered
     list/summary queries in `voice_call_service.py`.
   Flagged for the performance-review workstream; not modified per explicit
   instruction.

### Missing Timeouts

1. **OpenAI (`app/llm/openai_provider.py`)** — already wraps every call in
   `asyncio.wait_for(..., timeout=timeout)`, sourced from
   `settings.openai_timeout_seconds` (default 20s) or the tighter
   voice-specific budget (`voice_nlu_timeout_seconds`, 4s). No gap.

2. **SendGrid (`app/notifications/sendgrid_provider.py`)** — every
   `client.post()` call already passes an explicit `timeout=request_timeout`
   sourced from `settings.sendgrid_timeout_seconds` (default 10s). No gap.

3. **Dependency-health probes (`app/services/dependency_health.py`)** — the
   OpenAI/SendGrid reachability checks already use
   `httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS)` (4s). No gap.

4. **Database connections (`app/db/base.py`)** — `create_async_engine(...)`
   passes no explicit `connect_args` timeout or `statement_timeout`, so a
   hung TCP connection or a runaway query (e.g. an expensive `ILIKE` scan
   under load, see Missing Bounds #2 above) could hold a connection
   indefinitely with only asyncpg's own ~60s default connect timeout as a
   backstop, and no query-level cutoff at all.
   **Recommended, not fixed** — this is a DB engine/pool configuration
   decision (specific timeout values, whether to set `statement_timeout` at
   the session or role level) that overlaps with the performance-review
   workstream's ownership of DB tuning; picking values blind, without
   production load data, risks truncating legitimate slow queries. Flagged
   for a product/ops decision rather than fixed silently.

### Missing Retries

1. **OpenAI / SendGrid** — both already retry the correct transient-failure
   classes (`APIConnectionError`/`APITimeoutError`/`RateLimitError`/
   `InternalServerError` for OpenAI; connection errors and 429/5xx for
   SendGrid) with exponential backoff + jitter, bounded by
   `openai_max_retries`/`sendgrid_max_retries`. No gap.

2. **Database transient errors** — no retry wrapper exists around
   `db.execute`/`db.commit()` anywhere in the reviewed services. A transient
   connection drop mid-request fails the request outright.
   `pool_pre_ping=True` is already set on the engine, which prevents handing
   out an already-dead pooled connection to the *next* request, but doesn't
   help mid-request.
   **Recommended, not fixed** — a generic retry wrapper isn't safe to bolt on
   blindly here: `ticket_service._next_ticket_number()` is a count-based
   sequence generator (not a real DB sequence, by the code's own documented
   design trade-off), and a blind retry-on-failure around ticket creation
   risks double-counting or duplicate side effects (e.g. re-sending the
   ticket-created email) if the retry runs after a commit that actually
   succeeded server-side but timed out on the response. Doing this correctly
   needs per-call-site idempotency judgment, which is beyond what a "low
   risk" pass should change; flagged for a follow-up with narrower,
   dedicated scope.

### Missing Structured Logging

1. **`app/main.py`** — `logging.basicConfig(level=logging.INFO)` had no
   `format=`, so every log line printed with Python's bare default (message
   only, no timestamp/level/logger name), making log lines hard to correlate
   or filter in production output.
   **Fixed in this pass** — added
   `format="%(asctime)s %(levelname)s %(name)s %(message)s"`. This is still
   line-based text, not JSON-structured logging; a genuine move to structured
   (JSON) logs would need a new dependency or a custom formatter and a
   decision on log-aggregation strategy, which is a bigger, more opinionated
   change than this pass's scope — left as a recommendation, not implemented.

2. **No bare `print()` statements** were found anywhere under `app/`
   (verified via repo-wide search). No fix needed.

3. **No silently-swallowed exceptions found** beyond the two now-fixed cases
   above (`app/main.py`, `app/notifications/email.py`) — every other
   `except Exception:` block in scope (`sendgrid_provider.py`,
   `openai_provider.py`, `dependency_health.py`, `voice/routes.py`) already
   logs via a module-level `logging.getLogger(...)` call at an appropriate
   level.

4. **No per-request correlation/request ID** is attached to log lines (no
   middleware assigning a `request_id` that's threaded into every log record
   for that request), which would materially help tracing a single
   ticket-creation or voice-call flow across the log stream.
   **Recommended, not fixed** — a real improvement, but doing it well means
   either a new dependency (e.g. `asgi-correlation-id`) or custom middleware +
   `contextvars` + updating log call sites/formatters, which is more than a
   low-risk, drop-in change and risks conflicting with what the parallel
   workstreams expect from the logging setup. Flagged for a dedicated pass.

---

## Changes Made

1. **`backend/app/main.py`**
   - Added a log format string to `logging.basicConfig` (timestamp/level/logger
     name) instead of Python's bare default.
   - Added a catch-all `@app.exception_handler(Exception)` that logs unhandled
     exceptions via the app's own logger and returns a generic 500, while
     re-raising `HTTPException`/`RequestValidationError` unchanged so existing
     response shapes (404s, 409s, 422s, etc.) are preserved.

2. **`backend/app/notifications/email.py`**
   - Wrapped the body of `send_ticket_notification` (a `BackgroundTask`) in
     `try/except Exception` with `logger.exception(...)`, so an unexpected
     error can no longer propagate past the already-sent HTTP response
     unlogged.

3. **`backend/app/schemas/ticket.py`**
   - Added a `field_validator` on `TicketCreate.caller_name` /
     `.phone_number` / `.description` that strips whitespace and rejects
     values that are blank after stripping (previously only rejected by
     `min_length` if literally empty).

No other files were modified. `backend/alembic/`, `backend/tests/`,
`backend/app/ai/summarizer.py`, `backend/app/llm/*` (prompt logic),
`backend/app/voice/*` (Twilio call-flow logic), and `backend/app/db/models.py`
were left untouched per the task's explicit scope boundaries.

---

## Test Verification

The full suite could not be run to a clean, contention-free completion during
this session: the shared `hfmg_helpdesk_test` database had a concurrent
pytest process (apparently from a separate workstream/session on the same
machine) truncating and dropping tables mid-run, which produced 22
failures / 6 errors in a full run, none of which match the shape of this
pass's changes (no validation-rejection failures, no response-shape/status-code
changes — the observed failures were `sqlalchemy.exc.InvalidRequestError:
Could not refresh instance`, row-count assertion mismatches, and `KeyError`s,
all consistent with another session's `TRUNCATE`/`DROP TABLE` racing against
this run's fixtures). `pg_stat_activity` confirmed a session `idle in
transaction` for 500+ seconds blocking `TRUNCATE`/`DROP TABLE` from other
sessions, and duplicate `pytest -q` processes running from a second Python
interpreter outside this project's venv, both symptoms of an external,
concurrent workload rather than anything caused by these edits. Per the
coordinator's direction, a second full background run was stopped rather than
waited on further.

In place of a second full clean run, each change was verified directly,
independent of the contended shared database:

- **`TicketCreate` validator** (`app/schemas/ticket.py`): verified in-process
  against the live Pydantic model with no DB involved — a valid payload is
  accepted and trimmed, a whitespace-only `caller_name`/`description` is now
  rejected, and a genuinely empty string is still rejected by the
  pre-existing `min_length=1` check (matching
  `test_create_ticket_requires_description`'s expectation).
- **Exception handler + logging format** (`app/main.py`): verified via a
  direct in-process ASGI client (`httpx.AsyncClient` +
  `ASGITransport(app=app)`) — `GET /api/v1/health` returns `200`, a malformed
  UUID path param and a missing-required-field POST both still return `422`
  exactly as before, and a DB-backed 404 (`GET` on a nonexistent ticket)
  still returns the pre-existing `{"detail": "Ticket not found"}` shape with
  status `404`, confirming the new catch-all handler doesn't alter any
  existing HTTPException-based response.
- **`send_ticket_notification` try/except** (`app/notifications/email.py`):
  a targeted, low-contention run of `tests/test_email_sendgrid.py` (which
  exercises this function directly, including its failure-logging
  assertions) completed with no failures attributable to this file.
- A targeted run of `tests/test_tickets_api.py` +
  `tests/test_email_sendgrid.py` together did show 5 failures / 3 errors, but
  every one was an `sqlalchemy`-level state error, a count-mismatch
  assertion, or a `KeyError` on a response body — not a `422` from the new
  validator and not a `500`/shape change from the new exception handler —
  consistent with the same cross-session DB contention described above
  (confirmed live via `pg_stat_activity` showing other sessions still active
  against the same test database during this run).

**Bottom line:** the three changes are individually verified correct and
inert on the paths they don't touch; a fully clean, contention-free run of
the entire suite was not achieved in this session due to a concurrent
workload on the shared test database, which is an environmental condition
outside this pass's control, not a regression introduced by these edits.

# HFMG AI Help Desk — Implementation Plan

This plan sequences delivery from an empty repository to a production-deployed system. `ARCHITECTURE.md`, `DATABASE_DESIGN.md`, and `API_SPEC.md` describe the **target production design**. This plan starts with a deliberately smaller **MVP** that ships the core workflow fast, then layers the rest of the production design on top once the workflow is validated.

## MVP Scope Decision

For the first build, the following pieces of the target architecture are **deferred, not dropped**:

| Deferred for MVP | Target design (later phase) | MVP alternative |
|---|---|---|
| Docker / containerized dev & deploy | `ARCHITECTURE.md` §7 | Run Postgres, FastAPI, and Vite directly on the local machine |
| Redis + Celery/RQ worker | `ARCHITECTURE.md` §5.3–5.4 | FastAPI `BackgroundTasks` for AI summary + email (in-process, fire-and-forget after the response) |
| JWT auth + RBAC (`REQUESTER`/`AGENT`/`ADMIN`) | `ARCHITECTURE.md` §8.2 | No auth — all endpoints open. **Accepted risk**: MVP is only ever run on a trusted internal network, never exposed to the internet, until Phase 3 |
| `users` table, ticket assignment, comments, attachments, audit log | `DATABASE_DESIGN.md` §3.3–3.7 | Only `categories` and `tickets` tables for MVP |
| AI summary as a required, always-on pipeline | `ARCHITECTURE.md` §5.3 | AI summary is **optional and feature-flagged** (`ENABLE_AI_SUMMARY`); ticket workflow never depends on it succeeding or even running |

Everything in `DATABASE_DESIGN.md` and `API_SPEC.md` remains the long-term contract; the MVP schema and endpoint set are a strict subset, so upgrading later (adding `users`, swapping `BackgroundTasks` for a real queue, adding auth) is additive rather than a rewrite.

---

## Phase 1 — MVP: Core Ticketing, Email, Optional AI, Dashboard

**Goal:** a working internal tool — staff can submit a ticket, IT can see and triage the queue, `helpdesk@hfmg.net` gets notified, and an AI summary appears when the feature is turned on.

### 1.1 Environment (no Docker)
- Local PostgreSQL installed directly (Homebrew on macOS), one dev database + role created by hand.
- Python virtual environment for the backend (`venv` + `pip`), Node/npm for the frontend — both run as local processes (`uvicorn`, `npm run dev`), not containers.

### 1.2 Backend (FastAPI)
- Minimal schema: `categories` (lookup) and `tickets` (all 10 required fields + `status`, `ai_summary_status`, timestamps). No `users`, `ticket_comments`, `ticket_attachments`, `audit_log`, `notification_log` tables yet — those arrive in Phase 3 alongside auth.
- Alembic migration for the two tables + Postgres enums (`priority`, `ticket_status`, `ai_summary_status`).
- Seed script for default categories.
- Endpoints (all open, no auth):
  - `POST /api/v1/tickets` — create ticket, returns immediately, kicks off AI summary + email as background tasks.
  - `GET /api/v1/tickets` — list with filter (`status`, `priority`, `category_id`), pagination, sort.
  - `GET /api/v1/tickets/{id}` — detail.
  - `POST /api/v1/tickets/{id}/status` — status transition, kicks off a status-change email as a background task.
  - `GET /api/v1/categories` — for populating the ticket form.
  - `GET /api/v1/health` — liveness check.
- `TicketService` holds the create/list/status-transition logic, kept framework-agnostic so it doesn't need to change when auth/queueing are added in Phase 3.

### 1.3 Email notifications
- **Superseded:** originally sent via SMTP (`smtplib`, standard library). Migrated to Twilio SendGrid's API (`app/notifications/`, async `httpx`, provider abstraction) — see `SENDGRID_SETUP.md`. `notifications/email.py` still owns the subject/body building and fires on ticket creation and status change; only the transport changed.
- Runs via FastAPI `BackgroundTasks`, not a queue — acceptable at MVP volume; the send call has its own timeout so a slow SMTP server can't hang the worker thread indefinitely.
- If SMTP isn't configured (`SMTP_HOST` unset), the sender logs the would-be email instead of failing — lets the app run end-to-end on a laptop with no mail server.

### 1.4 AI summary (optional)
- `ai/summarizer.py` calls the LLM provider only if `ENABLE_AI_SUMMARY=true` **and** `OPENAI_API_KEY` is set; otherwise it's a no-op and `ai_summary` simply stays `NULL`.
- Runs via `BackgroundTasks` after ticket creation. Failure never affects ticket creation or listing — `ai_summary_status` tracks `PENDING`/`COMPLETED`/`FAILED`/`DISABLED`.

### 1.5 Frontend (React + Vite dashboard)
- Ticket list view: table of tickets with status/priority badges, filter by status, link into detail.
- New ticket form: the required fields, posts to `POST /tickets`.
- Ticket detail view: full ticket including AI summary (or "AI summary disabled/pending"), status-change control.
- No login screen — matches the no-auth backend for MVP.

### 1.6 Testing
- Backend: pytest unit tests for `TicketService` (ticket number generation, status transitions, list filtering) against a test Postgres database; smoke test that `POST /tickets` succeeds even with `ENABLE_AI_SUMMARY=false` and `SMTP_HOST` unset.
- Frontend: component test for the ticket form's required-field validation.

**Exit criteria:** on a single machine with local Postgres running, a user can start the backend and frontend, submit a ticket through the dashboard, see it appear in the list, change its status, and (if SMTP is configured) see the notification land in `helpdesk@hfmg.net`. AI summary works when enabled and is invisible/inert when not.

---

## Phase 2 — Twilio Voice Integration

**Detailed design:** [TWILIO_ARCHITECTURE.md](../../TWILIO_ARCHITECTURE.md) (infrastructure), [CALL_FLOW.md](../../CALL_FLOW.md) (conversation state machine), [VOICE_AGENT_DESIGN.md](../../VOICE_AGENT_DESIGN.md) (prompts, NLU, classification). Those three supersede the sketch below where they differ — notably, the design uses a multi-turn `<Gather input="speech">` state machine rather than the single record-then-transcribe flow originally sketched here and in `API_SPEC.md` §8.

**Goal:** callers can report issues by phone; calls become tickets automatically, reusing the Phase 1 ticket pipeline.

**Built:**
- `voice_call_sessions` table (`DATABASE_DESIGN.md` §3.8) + `tickets.source` column, via one Alembic migration.
- Four signature-validated webhooks (`API_SPEC.md` §8) — the first endpoints in the system with any inbound request authentication, ahead of general auth in Phase 3.
- A conversation state machine (`app/voice/orchestrator.py`) collecting problem → name → email, with the callback number taken silently from caller ID and asked for only when unavailable.
- LLM NLU via strict structured outputs: extraction, six-category classification, priority inference, escalation-intent detection — all validated against server-side allowlists.
- Escalation on caller request or three cumulative failures, producing a HIGH-priority callback ticket with the transcript attached.
- Abandoned-call salvage via the status callback.
- 18 automated tests covering the state machine and webhooks, with the NLU mocked so they need neither a Twilio account nor an API key.

**Deliberately not built:** call recording (consent requirements — `TWILIO_ARCHITECTURE.md` §9), live `<Dial>` transfer (callback-only, as approved), Spanish-language support.

Runs on `BackgroundTasks` like the rest of the MVP; revisit if call volume makes that impractical before Phase 3's queue lands.

**Exit criteria:** a test call produces a correctly tagged (`source="PHONE"`) ticket, summarized and notified the same way as a web-submitted ticket. ✅ Verified end-to-end against a running server.

---

## Phase 3 — Production Hardening

**Goal:** everything deferred in the MVP Scope Decision gets built out, and the system becomes safe to run on real HFMG traffic and real caller data.

- **Auth & RBAC**: `users` table, JWT + refresh-cookie auth, `REQUESTER`/`AGENT`/`ADMIN` roles, route-level authorization, per-`ARCHITECTURE.md` §8.2 / `API_SPEC.md` §2.
- **Async infrastructure**: introduce Redis + Celery/RQ, move AI summary and email off `BackgroundTasks` and onto a real worker queue with retries, backoff, and idempotency (`notification_log` table added here).
- **Containerization & deployment**: Dockerize backend/frontend/worker, `docker-compose` for local dev parity, then the production topology in `ARCHITECTURE.md` §7 (managed Postgres, managed Redis, container orchestration, load balancer, CDN).
- **Remaining schema**: `ticket_comments`, `ticket_attachments` (with object storage), `audit_log`, full `notification_log`.
- **Security & compliance**: BAAs with OpenAI and the email provider, log redaction of PHI-adjacent fields, rate limiting, WAF rules, encryption-at-rest configuration, retention policy enforcement.
- **Observability**: structured logging shipped centrally, metrics/dashboards, alerting, Sentry.
- **Frontend polish**: agent dashboard queues, SLA/due-date indicators, full-text search UI, accessibility pass.

**Exit criteria:** matches the exit criteria of the original production plan — live, monitored, backed up, auth-protected, and compliance-signed-off.

---

## Cross-Cutting Concerns

- **Docs stay in sync**: `DATABASE_DESIGN.md` and `API_SPEC.md` describe the full target; as each MVP shortcut is removed in Phase 3, update those docs' "current implementation status" rather than treating them as already-built.
- **Risk register**:
  | Risk | Mitigation |
  |---|---|
  | No auth in MVP means anyone on the network can read/modify tickets | MVP is explicitly restricted to a trusted internal network; Phase 3 auth is a hard prerequisite for any wider rollout |
  | `BackgroundTasks` has no retry/persistence if the process restarts mid-send | Acceptable at MVP volume; SMTP/AI failures are logged, not silently dropped; Phase 3 queue adds durability |
  | AI/email vendor data exposure before BAAs are signed | Keep `ENABLE_AI_SUMMARY` off and use a non-production SMTP relay (e.g., Mailhog/local relay) until BAAs are in place for any real caller data |
  | Twilio scope creep delays MVP | Phase 2 doesn't start until Phase 1's exit criteria are met |

## Suggested Sequencing Summary

```
Phase 1  MVP: ticketing + email + optional AI + dashboard   →  usable internal pilot, trusted network only
Phase 2  Twilio voice intake                                 →  phone parity with web
Phase 3  Production hardening (auth, queue, Docker, deploy)  →  live, compliant, monitored
```

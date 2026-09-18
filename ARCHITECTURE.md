# HFMG AI Help Desk — Architecture

**System:** AI-powered IT Help Desk for Horizon Family Medical Group (HFMG)
**Status:** Design (pre-implementation)
**Audience:** Engineering team building and operating the system

---

## 1. Purpose & Scope

An internal IT help desk system for HFMG staff to report and track IT issues (network, EHR access, hardware, phones, printers, credentials, etc.). Tickets are created via a web form (Phase 1) and, in the future, by phone through a Twilio voice/IVR integration (Phase 2+). Every ticket gets an AI-generated summary to help IT staff triage quickly, and the shared inbox `helpdesk@hfmg.net` receives email notifications on ticket lifecycle events.

Because HFMG is a healthcare organization, this system is treated as **HIPAA-adjacent** even though it is an internal IT tool: callers may reference patient scheduling systems, EHR issues, or describe context that touches PHI in free-text descriptions. The architecture is designed so PHI is minimized, access is controlled and audited, and third-party processors (AI, email, telephony) are used in a compliant configuration.

## 2. Design Goals

- **Production-grade from day one**: authentication, authorization, audit logging, input validation, observability.
- **Decoupled AI and notification work**: ticket creation must never block on an LLM call or an SMTP round trip.
- **HIPAA-aware**: encryption in transit and at rest, least-privilege access, audit trails, BAAs with any vendor that can see PHI-adjacent text (AI provider, email provider).
- **Extensible for voice**: the ticket-intake path is provider-agnostic (web form today, Twilio webhook tomorrow) so voice intake reuses the same domain logic.
- **Operable by a small team**: managed services over self-hosted infrastructure where it reduces operational burden, without giving up control over PHI-adjacent data.

## 3. High-Level System Context

```
                    ┌─────────────────────┐
                    │   HFMG Staff (web)   │
                    └──────────┬───────────┘
                               │ HTTPS
                               ▼
                    ┌─────────────────────┐
                    │  React + Vite SPA    │  (static hosting / CDN)
                    │  (Ticket Portal)     │
                    └──────────┬───────────┘
                               │ HTTPS / REST (JSON), JWT auth
                               ▼
                    ┌─────────────────────────────────────┐
                    │        FastAPI Backend (API)         │
                    │  - Auth & RBAC                       │
                    │  - Ticket CRUD & workflow             │
                    │  - Validation & audit logging         │
                    └───┬──────────────┬──────────────┬────┘
                        │              │              │
             SQL (asyncpg)   Task queue (jobs)   Outbound HTTPS
                        │              │              │
                        ▼              ▼              ▼
             ┌────────────────┐ ┌─────────────┐ ┌──────────────────┐
             │  PostgreSQL     │ │ Redis +     │ │ Anthropic API     │
             │  (primary data) │ │ Worker      │ │ (AI summaries)     │
             │                 │ │ (Celery/RQ) │ └──────────────────┘
             └────────────────┘ │             │ ┌──────────────────┐
                                 │             ├─│ Email provider     │
                                 │             │ │ (SES/SendGrid SMTP)│
                                 │             │ └──────────────────┘
                                 └─────────────┘
                                        ▲
                                        │ (Phase 2+)
                               ┌────────┴─────────┐
                               │  Twilio Voice /   │
                               │  IVR Webhooks      │
                               └────────────────────┘
```

### Components

| Component | Responsibility |
|---|---|
| React + Vite SPA | Staff-facing ticket portal: submit, view, filter, and manage tickets; agent dashboard. |
| FastAPI backend | REST API, business logic, auth, validation, orchestration of async work. |
| PostgreSQL | System of record for tickets, users, audit log. |
| Redis + worker (Celery or RQ) | Async job execution: AI summarization, email delivery, future call-transcript processing. Decouples slow/unreliable external calls from the request path. |
| Anthropic API (Claude) | Generates a structured summary of each ticket's description for agent triage. |
| Email provider (Amazon SES or SendGrid, via SMTP/API) | Delivers notifications to `helpdesk@hfmg.net` and optionally to the caller. |
| Twilio (future) | Inbound voice/IVR; call recording + transcription feeds ticket creation via a webhook into the same ticket pipeline. |

## 4. Why This Stack

- **FastAPI**: async-native (matters for I/O-bound calls to Postgres, the AI API, and email), automatic OpenAPI schema generation (keeps `API_SPEC.md` and the live docs at `/docs` in sync), strong typing via Pydantic which pairs well with a strict ticket schema.
- **PostgreSQL**: relational integrity for tickets/users/audit trail, native `ENUM`/`JSONB` support (used for status/priority and for storing raw AI response metadata), mature encryption-at-rest and row-level security options, easy to run on managed services (RDS/Cloud SQL) with HIPAA-eligible configurations.
- **React + Vite**: fast dev/build cycle, SPA suits an internal dashboard-style tool, easy to statically host and serve behind the same TLS boundary as the API.
- **Async job queue (Redis + Celery/RQ)**: AI calls and outbound email are the two least reliable, highest-latency dependencies in the system. They must not sit in the HTTP request/response cycle for ticket creation. A ticket is created synchronously and returns immediately; summarization and notification happen asynchronously and update the ticket when done.
- **Twilio (future)**: industry-standard for programmable voice/IVR; webhook model maps cleanly onto "create ticket from external event," so it can reuse the same `TicketService` used by the web form.

## 5. Backend Architecture (FastAPI)

### 5.1 Layering

```
app/
  api/            # routers (HTTP layer only: parsing, status codes, auth deps)
    v1/
      tickets.py
      auth.py
      users.py
      webhooks/
        twilio.py        # future
  core/           # config, security, settings, logging
  domain/         # business logic, framework-agnostic
    tickets/
      service.py  # TicketService: create, transition, assign, close
      rules.py    # priority/category validation, SLA rules
  ai/             # AI summarization client + prompt templates
    summarizer.py
  notifications/  # email templates + send logic
    email.py
  db/             # SQLAlchemy models, session management, Alembic migrations
    models.py
    session.py
  workers/        # Celery/RQ task definitions
    tasks.py
  main.py         # app factory, middleware, router registration
```

Rationale: `api/` stays thin (HTTP concerns only); `domain/` holds logic that is unit-testable without spinning up FastAPI or a database; `ai/` and `notifications/` are isolated so their providers can be swapped (e.g., SES → SendGrid, Anthropic → another model) without touching ticket logic.

### 5.2 Request Flow — Ticket Creation (synchronous path)

1. Client `POST /api/v1/tickets` with ticket payload.
2. FastAPI validates payload via Pydantic schema.
3. Auth dependency resolves the authenticated user (or validates a service token for the future Twilio webhook).
4. `TicketService.create_ticket()` persists the ticket (`status=NEW`), generates the human-readable ticket number (see `DATABASE_DESIGN.md` §3.1), and writes an audit log entry — all in one DB transaction.
5. API enqueues two async jobs: `generate_ai_summary(ticket_id)` and `send_ticket_notification(ticket_id, event="created")`.
6. API returns `201 Created` with the ticket resource (AI summary field is `null`/pending at this point).

### 5.3 Async Flow — AI Summary

1. Worker picks up `generate_ai_summary(ticket_id)`.
2. Worker loads the ticket, builds a prompt from category/priority/description (template in `ai/summarizer.py`).
3. Worker calls the Anthropic API with a timeout and retry policy (exponential backoff, max 3 attempts).
4. On success: worker updates `tickets.ai_summary`, `ai_summary_generated_at`; on repeated failure: ticket keeps `ai_summary = NULL`, an entry is logged and surfaced on an internal "AI summary failed" dashboard filter so no ticket silently loses triage support.
5. Worker never blocks ticket visibility — agents can view/act on a ticket before its summary is ready; the frontend polls or the ticket detail view shows a "Generating summary…" state.

### 5.4 Async Flow — Email Notification

1. Worker picks up `send_ticket_notification(ticket_id, event)`.
2. Renders an HTML/text email template (ticket number, caller, category, priority, description, AI summary if available, link to ticket in the portal).
3. Sends via the email provider's transactional API/SMTP to `helpdesk@hfmg.net` (and optionally CC's the caller's email if provided and they opted in).
4. Delivery result is logged to `notification_log` (see `DATABASE_DESIGN.md`) for auditability and retry-on-failure.
5. Retries with backoff on transient provider errors; permanent failures are surfaced to an ops alert channel.

### 5.5 Future — Twilio Voice Intake

- Inbound call hits a Twilio number → Twilio IVR (or Twilio Studio flow) collects/records caller info and issue description.
- Twilio posts a webhook to `POST /api/v1/webhooks/twilio/call-completed` with call metadata + recording URL.
- Endpoint validates the Twilio request signature (`X-Twilio-Signature`), enqueues `process_voice_ticket(call_sid)`.
- Worker fetches the recording, transcribes it (Twilio's own transcription or a dedicated speech-to-text step), maps the transcript into the same `TicketCreate` schema used by the web form, and calls the same `TicketService.create_ticket()` — guaranteeing voice-originated tickets flow through identical validation, AI summarization, and notification logic.
- This is why the domain layer must not import anything from `api/` — the webhook router is just another caller of `TicketService`, same as the tickets router.

## 6. Frontend Architecture (React + Vite)

```
src/
  api/            # typed API client (generated or hand-written from OpenAPI schema)
  features/
    tickets/
      TicketList.tsx
      TicketDetail.tsx
      TicketForm.tsx
      hooks/ (useTickets, useCreateTicket, ...)
    auth/
  components/     # shared UI primitives
  routes/         # route definitions (React Router)
  lib/            # query client (TanStack Query), auth context
```

- **Data fetching**: TanStack Query for server state (caching, polling for AI summary completion, optimistic status updates).
- **Auth**: JWT stored in memory + httpOnly refresh cookie (see §8.2); route guards for agent-only views (e.g., ticket assignment, closing tickets) vs. general-staff views (submit + view own tickets).
- **Build/deploy**: Vite production build served as static assets from a CDN/static host (e.g., S3+CloudFront, Netlify, or served by Nginx alongside the API); API base URL injected via environment-specific `.env` at build time.

## 7. Deployment Architecture

### 7.1 Environments

`dev` → `staging` → `production`, each with isolated database, isolated secrets, and separate Anthropic/Twilio/email credentials where the provider supports it (never share production PHI-adjacent data into lower environments; use synthetic seed data in dev/staging).

### 7.2 Topology (production)

```
                     ┌───────────────────────────┐
Internet ── TLS ───► │  Load Balancer / Reverse   │
                     │  Proxy (ALB / Nginx)        │
                     └──────────┬────────────────┘
                                │
                 ┌──────────────┴───────────────┐
                 ▼                               ▼
      ┌────────────────────┐          ┌────────────────────┐
      │ Static SPA (CDN)     │          │ FastAPI containers  │
      │ (Vite build output)  │          │ (2+ replicas, ASG/  │
      └────────────────────┘          │  ECS/K8s)            │
                                        └──────────┬──────────┘
                                                    │
                              ┌─────────────────────┼─────────────────────┐
                              ▼                     ▼                     ▼
                    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
                    │ PostgreSQL (RDS)   │  │ Redis (ElastiCache)│  │ Worker containers │
                    │ Multi-AZ, private   │  │ private subnet     │  │ (Celery/RQ,        │
                    │ subnet, encrypted   │  └──────────────────┘  │ 1+ replicas)        │
                    └──────────────────┘                            └──────────────────┘
```

- API and worker containers run in private subnets; only the load balancer is internet-facing.
- Database and Redis are not publicly reachable; access only from the API/worker security group.
- All external calls (Anthropic, email provider, Twilio) go outbound through a NAT gateway; inbound Twilio webhooks terminate at the load balancer like any other API route, with signature validation at the application layer.
- Container images built in CI, pushed to a private registry, deployed via rolling update (zero-downtime).

### 7.3 Configuration & Secrets

- All secrets (DB credentials, Anthropic API key, email provider API key/SMTP credentials, Twilio auth token, JWT signing key) come from a managed secrets store (AWS Secrets Manager / GCP Secret Manager / Vault) — never committed, never in plain environment files in the repo.
- Per-environment config via 12-factor environment variables, loaded through a typed settings module (`pydantic-settings`).

## 8. Security & Compliance

### 8.1 Data classification

Treat `caller_name`, `phone_number`, `email`, and free-text `description`/`ai_summary` fields as sensitive/PHI-adjacent. This drives:
- Encryption at rest for the database (managed disk encryption) and in transit (TLS everywhere, including DB connections).
- BAAs (Business Associate Agreements) required with: the AI provider (Anthropic, via their enterprise/BAA-eligible offering), the email provider, and Twilio, before any production PHI-adjacent traffic flows through them.
- No PHI-adjacent data in application logs; structured logging must redact/exclude ticket free-text fields (log ticket ID/status transitions, not descriptions).

### 8.2 AuthN/AuthZ

- Staff authenticate via the SPA against `POST /api/v1/auth/login` (username/password against internal directory, or SSO/OIDC against HFMG's identity provider if available — recommended for a healthcare org to keep a single credential source).
- JWT access token (short-lived, ~15 min) + refresh token (httpOnly, secure cookie, longer-lived, rotated on use).
- Roles: `requester` (submit/view own tickets), `agent` (view/assign/update all tickets), `admin` (manage users/categories, view audit log). Enforced via FastAPI dependencies checking role claims on every protected route.
- Twilio webhook route is authenticated via Twilio request-signature validation, not JWT (it's a machine-to-machine callback).

### 8.3 Audit & Compliance

- Every ticket status change, assignment, and field edit is written to an append-only `audit_log` table (actor, action, before/after, timestamp).
- Access logs retained per HFMG's compliance retention policy.
- Rate limiting and basic WAF rules at the load balancer to reduce abuse of public-facing endpoints (ticket submission form, Twilio webhook).

## 9. Observability

- **Logging**: structured JSON logs (request ID, user ID, route, latency, status) shipped to a central log store (CloudWatch/Datadog/ELK). PHI-adjacent fields excluded (see §8.1).
- **Metrics**: request rate/latency/error rate per route; job queue depth and failure rate; AI call latency and failure rate; email delivery success rate.
- **Tracing**: request ID propagated from API → worker job → external API calls, so a single ticket's lifecycle (create → summarize → notify) can be traced end to end.
- **Alerting**: on elevated 5xx rate, on worker queue backlog, on AI/email failure rate exceeding threshold, on DB connection saturation.
- **Error tracking**: Sentry (or equivalent) for both frontend and backend exceptions.

## 10. Reliability & Scaling

- API is stateless — scales horizontally behind the load balancer.
- Worker pool scales independently based on queue depth (AI summarization and email are the variable-latency parts of the system).
- Postgres connection pooling via PgBouncer or SQLAlchemy's pool, sized to `(API replicas + worker replicas) × pool_size < Postgres max_connections`.
- Idempotency: AI summarization and email jobs are safe to retry (keyed by `ticket_id` + `event`); duplicate delivery is prevented by checking `notification_log` before send.
- Backpressure: if the AI provider is down, tickets still get created and notified (email doesn't depend on AI summary being present); the summary backfills once the provider recovers.

## 11. Open Decisions for the Team

| Decision | Recommendation | Notes |
|---|---|---|
| Identity provider | OIDC SSO with HFMG's existing directory if one exists | Avoids a second credential store for clinical/IT staff |
| Job queue | Redis + Celery | RQ is a lighter alternative if the team wants less operational surface |
| Hosting | AWS (RDS + ECS/Fargate + ElastiCache + SES) | Matches HIPAA-eligible managed service availability; GCP is an equally valid alternative |
| AI provider contract | Anthropic API under a BAA | Required before processing real caller data |

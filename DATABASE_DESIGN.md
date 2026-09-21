# HFMG AI Help Desk — Database Design

**Engine:** PostgreSQL 15+
**Migration tool:** Alembic
**ORM:** SQLAlchemy (async, via `asyncpg`)

---

## 1. Design Principles

- Every table has a surrogate `UUID` primary key (`gen_random_uuid()` via `pgcrypto`/`pgcrypto` extension or `uuid-ossp`), so IDs are never guessable or sequential and are safe to expose in the API.
- The human-facing **ticket number** is a separate, sequential, formatted identifier — never the primary key — so it can follow a business format (e.g., `HFMG-2026-000482`) without constraining storage.
- All tables have `created_at` / `updated_at` timestamps (`timestamptz`, UTC).
- Soft state transitions (status, priority, category) use Postgres `ENUM` types for integrity, not free-text.
- No destructive deletes on tickets — tickets are closed/archived, never dropped, to preserve the audit trail (`deleted_at` nullable column instead, unused in normal ticket flow but present for admin data-hygiene needs).
- PHI-adjacent free text (`description`, `ai_summary`) lives in normal encrypted-at-rest columns; no separate PHI vault is required for an internal IT help desk, but column-level encryption is a documented upgrade path (§7) if HFMG's compliance team requires it.

## 2. Entity-Relationship Overview

```
 users ──────────< assigned_tickets >────────── tickets ──────< ticket_comments
   │                                               │  │
   │                                               │  └──────< ticket_attachments
   │                                               │
   └──────────────< audit_log >───────────────────┘
                                                     │
 categories ─────────────────────────────────────────┘
                                                     │
 notification_log ───────────────────────────────────┘

 voice_call_sessions ────────────────────────────────┘  (1 call → 0..1 ticket)
```

- `tickets.category` references `categories` (lookup table, admin-managed).
- `tickets.assigned_agent_id` references `users` (nullable — unassigned tickets allowed).
- `tickets.requester_user_id` references `users` (nullable — a caller may not have an internal account, e.g., voice intake).
- `ticket_comments`, `ticket_attachments`, `notification_log`, `audit_log` all reference `tickets` (cascade-restricted, not cascade-deleted).
- `voice_call_sessions` (Phase 2) optionally links to the `ticket` a call produced.

## 3. Tables

### 3.1 `tickets`

The core entity. Maps directly to the required ticket fields, extended with fields needed for production workflow.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | `UUID` | PK, default `gen_random_uuid()` | Internal identifier, used in API URLs. |
| `ticket_number` | `TEXT` | UNIQUE, NOT NULL | Human-facing. Format `HFMG-YYYY-NNNNNN`, generated from a Postgres sequence per year (§3.1.1). This is the **Ticket Number** field. |
| `caller_name` | `TEXT` | NOT NULL | **Caller Name**. |
| `phone_number` | `TEXT` | NOT NULL, CHECK format | **Phone Number**. Stored normalized (E.164, e.g. `+15551234567`) via application-layer validation. |
| `email` | `CITEXT` | NULL | **Email**. Nullable — phone-only callers (voice intake) may not have email captured. `CITEXT` for case-insensitive matching. |
| `category_id` | `UUID` | FK → `categories.id`, NOT NULL | **Category**. |
| `priority` | `priority_enum` | NOT NULL, default `MEDIUM` | **Priority**. `LOW`, `MEDIUM`, `HIGH`, `URGENT`. |
| `description` | `TEXT` | NOT NULL | **Description**. Free text from caller/agent. |
| `ai_summary` | `TEXT` | NULL | **AI Summary**. Populated asynchronously after creation. |
| `ai_summary_status` | `ai_summary_status_enum` | NOT NULL, default `PENDING` | `PENDING`, `COMPLETED`, `FAILED`. Drives the "Generating summary…" UI state. |
| `ai_summary_generated_at` | `TIMESTAMPTZ` | NULL | When the AI summary completed. |
| `ai_model` | `TEXT` | NULL | Model identifier used (e.g., `gpt-5-nano`), for auditability/reproducibility. Not implemented in the MVP schema. |
| `status` | `ticket_status_enum` | NOT NULL, default `NEW` | **Status**. `NEW`, `OPEN`, `IN_PROGRESS`, `ON_HOLD`, `RESOLVED`, `CLOSED`, `CANCELLED`. |
| `source` | `ticket_source_enum` | NOT NULL, default `WEB` | `WEB`, `PHONE`, `EMAIL`, `WALK_IN`. Supports future Twilio intake without schema change. |
| `requester_user_id` | `UUID` | FK → `users.id`, NULL | Set if the caller is an authenticated staff member submitting via the portal. |
| `assigned_agent_id` | `UUID` | FK → `users.id`, NULL | IT staff member currently owning the ticket. |
| `resolved_at` | `TIMESTAMPTZ` | NULL | Set when status transitions to `RESOLVED`. |
| `closed_at` | `TIMESTAMPTZ` | NULL | Set when status transitions to `CLOSED`. |
| `due_at` | `TIMESTAMPTZ` | NULL | SLA target, computed from `priority` + `created_at` at creation time. |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, default `now()` | **Created Date**. |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, default `now()`, updated via trigger | Last modification time. |
| `deleted_at` | `TIMESTAMPTZ` | NULL | Soft-delete marker; unused in normal flow, present for admin cleanup. |

**Enums**

```sql
CREATE TYPE priority_enum AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'URGENT');
CREATE TYPE ticket_status_enum AS ENUM (
  'NEW', 'OPEN', 'IN_PROGRESS', 'ON_HOLD', 'RESOLVED', 'CLOSED', 'CANCELLED'
);
CREATE TYPE ticket_source_enum AS ENUM ('WEB', 'PHONE', 'EMAIL', 'WALK_IN');
CREATE TYPE ai_summary_status_enum AS ENUM ('PENDING', 'COMPLETED', 'FAILED');
```

**Indexes**

This block is the target design. **Verified against the running database (`\d tickets`) as of Tier 5 documentation alignment (see `docs/archive/BACKEND_GAP_ANALYSIS.md`), only `ix_tickets_ticket_number` and `ix_tickets_created_at` actually exist.** `ix_tickets_status`, `ix_tickets_priority`, `ix_tickets_category_id`, `ix_tickets_assigned_agent_id`, `ix_tickets_email`, and `ix_tickets_fts` were never migrated in, despite this document previously implying `ix_tickets_fts` specifically was "already exists in the schema, unused" — it does not exist. `GET /api/v1/tickets`'s `status`/`priority`/`category_id`/`source`/`q` filters (`API_SPEC.md` §3) all run as full table scans today; acceptable at this system's documented volume (§6 below), worth revisiting only if `tickets` grows enough for it to matter.

```sql
CREATE UNIQUE INDEX ux_tickets_ticket_number ON tickets (ticket_number);          -- ✅ exists
CREATE INDEX ix_tickets_status ON tickets (status) WHERE deleted_at IS NULL;      -- ⬜ not migrated
CREATE INDEX ix_tickets_priority ON tickets (priority) WHERE deleted_at IS NULL;  -- ⬜ not migrated
CREATE INDEX ix_tickets_category_id ON tickets (category_id);                    -- ⬜ not migrated
CREATE INDEX ix_tickets_assigned_agent_id ON tickets (assigned_agent_id);         -- ⬜ not migrated (column doesn't exist yet either)
CREATE INDEX ix_tickets_created_at ON tickets (created_at DESC);                  -- ✅ exists
CREATE INDEX ix_tickets_email ON tickets (email);                                 -- ⬜ not migrated
-- Full text search over description + ai_summary for agent search
CREATE INDEX ix_tickets_fts ON tickets USING GIN (                               -- ⬜ not migrated -- q search uses ILIKE instead, see API_SPEC.md §3
  to_tsvector('english', coalesce(description, '') || ' ' || coalesce(ai_summary, ''))
);
```

#### 3.1.1 Ticket number generation

A per-year sequence avoids one giant global counter and keeps numbers meaningful:

```sql
CREATE SEQUENCE ticket_number_seq_2026 START 1;
-- ticket_number generated in application code (TicketService) as:
--   f"HFMG-{year}-{next_val:06d}"
-- using nextval('ticket_number_seq_' || current_year) resolved dynamically,
-- with a new sequence created (idempotently) via a scheduled job each January.
```

Generation happens inside the same DB transaction as the insert, so the ticket number and row creation are atomic; `nextval()` is never reused even if the transaction rolls back (acceptable/expected gap behavior for Postgres sequences).

### 3.2 `categories`

Admin-managed lookup table (e.g., "Network", "EHR / Clinical Systems", "Hardware", "Phones", "Account & Access", "Printers", "Other").

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `name` | `TEXT` | UNIQUE, NOT NULL |
| `description` | `TEXT` | NULL |
| `default_priority` | `priority_enum` | NULL — used to pre-fill the form |
| `is_active` | `BOOLEAN` | NOT NULL, default `true` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

Seeded at deploy time; editable by `admin` role via API.

### 3.3 `users`

Internal staff accounts (requesters, agents, admins). Does **not** store patient data — this table is HFMG staff only.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `email` | `CITEXT` | UNIQUE, NOT NULL |
| `full_name` | `TEXT` | NOT NULL |
| `role` | `user_role_enum` | NOT NULL, default `REQUESTER` |
| `hashed_password` | `TEXT` | NULL (null if SSO-only auth) |
| `is_active` | `BOOLEAN` | NOT NULL, default `true` |
| `last_login_at` | `TIMESTAMPTZ` | NULL |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

```sql
CREATE TYPE user_role_enum AS ENUM ('REQUESTER', 'AGENT', 'ADMIN');
```

### 3.4 `ticket_comments`

Internal notes and agent/requester communication thread on a ticket.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `ticket_id` | `UUID` | FK → `tickets.id`, NOT NULL |
| `author_user_id` | `UUID` | FK → `users.id`, NULL (NULL = system-generated comment) |
| `body` | `TEXT` | NOT NULL |
| `is_internal` | `BOOLEAN` | NOT NULL, default `true` — internal-only note vs. one visible to the requester |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, default `now()` |

```sql
CREATE INDEX ix_ticket_comments_ticket_id ON ticket_comments (ticket_id, created_at);
```

### 3.5 `ticket_attachments`

Metadata only — binary content lives in object storage (S3/GCS), never in Postgres.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `ticket_id` | `UUID` | FK → `tickets.id`, NOT NULL |
| `uploaded_by_user_id` | `UUID` | FK → `users.id`, NULL |
| `file_name` | `TEXT` | NOT NULL |
| `content_type` | `TEXT` | NOT NULL |
| `storage_key` | `TEXT` | NOT NULL, UNIQUE — object storage key/path |
| `size_bytes` | `BIGINT` | NOT NULL |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, default `now()` |

### 3.6 `notification_log`

Auditable record of every outbound email attempt (delivery to `helpdesk@hfmg.net` and any caller CC).

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `ticket_id` | `UUID` | FK → `tickets.id`, NOT NULL |
| `event` | `notification_event_enum` | NOT NULL |
| `recipient` | `TEXT` | NOT NULL |
| `status` | `notification_status_enum` | NOT NULL, default `PENDING` |
| `provider_message_id` | `TEXT` | NULL — ID returned by SES/SendGrid, for delivery tracing |
| `error_message` | `TEXT` | NULL |
| `attempt_count` | `SMALLINT` | NOT NULL, default `0` |
| `sent_at` | `TIMESTAMPTZ` | NULL |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, default `now()` |

```sql
CREATE TYPE notification_event_enum AS ENUM (
  'TICKET_CREATED', 'TICKET_ASSIGNED', 'TICKET_STATUS_CHANGED',
  'TICKET_RESOLVED', 'TICKET_COMMENT_ADDED'
);
CREATE TYPE notification_status_enum AS ENUM ('PENDING', 'SENT', 'FAILED');
CREATE INDEX ix_notification_log_ticket_id ON notification_log (ticket_id);
```

Used both for audit and to make notification jobs idempotent (check for an existing `SENT` row for the same `ticket_id` + `event` before resending on retry).

### 3.7 `audit_log`

Append-only trail of state changes across the system (tickets, users, categories).

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `entity_type` | `TEXT` | NOT NULL — e.g. `'ticket'`, `'user'` |
| `entity_id` | `UUID` | NOT NULL |
| `actor_user_id` | `UUID` | FK → `users.id`, NULL (NULL = system/worker action) |
| `action` | `TEXT` | NOT NULL — e.g. `'created'`, `'status_changed'`, `'assigned'` |
| `before_value` | `JSONB` | NULL |
| `after_value` | `JSONB` | NULL |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, default `now()` |

```sql
CREATE INDEX ix_audit_log_entity ON audit_log (entity_type, entity_id, created_at);
```

No `UPDATE`/`DELETE` grants on this table for the application role — insert-only, enforced at the database role level.

### 3.8 `voice_call_sessions` (Phase 2 — implemented)

Per-call conversation state for the Twilio voice agent, plus the link to the ticket the call produced. This **replaces** the `voice_calls` sketch in earlier drafts, which assumed a record-then-transcribe design; the implemented agent is a multi-turn conversation, so it needs live state rather than a post-call recording reference.

| Column | Type | Constraints |
|---|---|---|
| `id` | `UUID` | PK |
| `twilio_call_sid` | `TEXT` | UNIQUE, NOT NULL — natural idempotency key for webhook retries |
| `from_number` | `TEXT` | NOT NULL — caller ID; may be `anonymous`/blocked |
| `to_number` | `TEXT` | NOT NULL |
| `state` | `voice_call_state_enum` | NOT NULL — position in the state machine (`CALL_FLOW.md` §2) |
| `collected` | `JSONB` | NOT NULL, default `'{}'` — slots gathered so far |
| `turns` | `JSONB` | NOT NULL, default `'[]'` — ordered transcript, also the call's audit trail |
| `misunderstanding_count` | `SMALLINT` | NOT NULL, default `0` — drives the 3-strikes escalation |
| `email_attempt_count` | `SMALLINT` | NOT NULL, default `0` — email is optional and capped separately |
| `escalated` | `BOOLEAN` | NOT NULL, default `false` |
| `escalation_reason` | `escalation_reason_enum` | NULL — `CALLER_REQUESTED`, `REPEATED_MISUNDERSTANDING`, `SYSTEM_ERROR` |
| `ticket_id` | `UUID` | FK → `tickets.id` ON DELETE SET NULL, NULL — also the create-ticket idempotency guard |
| `ended_at` | `TIMESTAMPTZ` | NULL — set by the status callback |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

State lives in Postgres rather than process memory because each webhook turn is an independent request that may land on any API replica, and the app may restart mid-call.

No call recordings are stored — recording stays off pending compliance sign-off (`TWILIO_ARCHITECTURE.md` §9). The `turns` transcript is PHI-adjacent and carries the same handling rules as `tickets.description`.

## 4. Constraints & Data Integrity Rules

- `tickets.email` and `tickets.phone_number`: format validated at the application layer (Pydantic) before insert; a lightweight `CHECK` constraint enforces non-empty phone number as a defense-in-depth backstop.
- `tickets.resolved_at IS NOT NULL` only permitted when `status IN ('RESOLVED', 'CLOSED')` — enforced in `TicketService`, optionally backed by a `CHECK` constraint or trigger if stricter DB-level enforcement is desired.
- Foreign keys use `ON DELETE RESTRICT` for `category_id` (a category in use cannot be deleted, only deactivated via `is_active`) and `ON DELETE SET NULL` for `assigned_agent_id`/`requester_user_id` (a deactivated user doesn't orphan historical tickets).
- `updated_at` maintained via a standard `BEFORE UPDATE` trigger (`set_updated_at()`), applied to every mutable table.

## 5. Migrations

- Alembic manages all schema changes; no manual DDL against production.
- Every migration is additive/backward-compatible where possible (add nullable column → backfill → make non-null in a follow-up migration) to support zero-downtime rolling deploys.
- Seed data (default categories, admin bootstrap user) applied via a dedicated seed script, not baked into schema migrations.

## 6. Sizing & Retention

- Expected volume: internal IT help desk for a mid-size medical group — low thousands of tickets/year. No partitioning needed initially; `created_at` indexing is sufficient. Revisit partitioning (by year) only if `tickets` exceeds ~5M rows.
- Retention: tickets and audit log retained per HFMG's compliance policy (commonly 6–7 years for healthcare-adjacent records); no automatic hard deletion — `deleted_at` is reserved for exceptional admin corrections (e.g., true duplicate/spam submissions), not routine retention management.
- Attachments in object storage follow the same retention policy via bucket lifecycle rules, not database TTLs.

## 7. Future Hardening Options (not required for v1)

- Column-level encryption (`pgcrypto`) for `description`/`ai_summary` if HFMG's compliance review requires encryption beyond disk-level, at the cost of losing native full-text search (would require a separate search index, e.g., re-encrypting into an external search service).
- Row-level security (RLS) policies if the requester-facing view needs DB-enforced isolation (a requester can only `SELECT` their own tickets) in addition to API-layer authorization.
- Read replica for reporting/analytics once agent dashboards grow beyond simple filtered lists.

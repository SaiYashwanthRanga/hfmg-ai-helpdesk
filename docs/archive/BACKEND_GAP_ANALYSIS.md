# HFMG AI Help Desk — Backend Gap Analysis

**Version:** 1.1 — see the status update below; the body of this document is left as originally written (v1.0) since it's still an accurate record of what was true *then* and why each tier was sequenced the way it was.
**Status:** Tiers 0–4 implemented. See `WORK_LOG.md` for what shipped in each, `DOCS_GAP_REPORT.md` for documentation corrections made along the way, and `REMAINING_PRODUCT_DECISIONS.md` for what's still genuinely blocked.

## Status Update (post Tier 0–4 implementation)

| Tier | Status |
|---|---|
| Tier 0 (ticket API fixes) | ✅ Done — `source` serialized, `regenerate-summary` built, `priority`/`source`/`q` filters added, `/health/ready` is real |
| Tier 1 (Dashboard/Settings foundation) | ✅ Done — `/health/dependencies`, `/settings/status`, `/analytics/kpis`, `/analytics/recent-activity` |
| Tier 2 (Voice Calls) | ✅ Done — `/voice-calls`, `/voice-calls/{id}`, `/voice-calls/summary` |
| Tier 3 (Analytics) | ✅ Done — 6 aggregation endpoints; `calls_today`/`escalations` unblocked in `/analytics/kpis` now that voice-call data is queryable |
| Tier 4 (AI Insights) | 🔶 Done, partial by design — only Category Breakdown returns data; Trending Issues, Repeated Problems, High Risk Alerts, Recommendations remain explicit blocked fields (see `REMAINING_PRODUCT_DECISIONS.md`) |
| Tier 5 (Documentation Alignment) | ✅ Done — `API_SPEC.md`, `DATABASE_DESIGN.md`, `TWILIO_ARCHITECTURE.md`, `CALL_FLOW.md` corrected against running code |

**What's genuinely still open**, not because it wasn't attempted but because it requires a decision this codebase can't make for itself: AI Resolution Rate's definition, the Trending-vs-Most-Common distinction, a caller-identity concept for Repeated Problems, a "normal rate" baseline for High Risk Alerts, and a defined output shape for Recommendations. All five are product decisions, tracked in `REMAINING_PRODUCT_DECISIONS.md`, not backend gaps.

**Original v1.0 analysis below, unchanged:**

**Status (original):** Analysis (no code changed by this document)
**Method:** Direct inspection of the running backend source (`backend/app/**`), not just the design docs — every claim below is checked against actual router/schema/model code, not assumed from `API_SPEC.md`/`WIREFRAMES.md` alone. Several findings contradict what those docs assumed; each is called out explicitly.

**Inputs read:** `API_SPEC.md`, `DESIGN.md`, `WIREFRAMES.md`, `FRONTEND_IMPLEMENTATION_PLAN.md`, plus `backend/app/api/v1/*.py`, `backend/app/db/models.py`, `backend/app/schemas/ticket.py`, `backend/app/services/ticket_service.py`, `backend/app/voice/*.py`, `backend/app/ai/summarizer.py`, `backend/app/core/config.py`, `backend/seed.py`, `backend/requirements.txt`, `backend/alembic/versions/*`.

---

## What actually exists today (ground truth)

Registered routes (`backend/app/api/v1/router.py`): `health`, `tickets`, `categories`, and the four Twilio voice webhooks. That's the entire API surface.

| Endpoint | Real? | Notes |
|---|---|---|
| `GET /api/v1/health` | ✅ | Static `{"status": "ok"}` — no DB check, despite the name suggesting one |
| `POST /api/v1/tickets` | ✅ | |
| `GET /api/v1/tickets` | 🔶 | Accepts `page`, `page_size`, `status`, `category_id` only |
| `GET /api/v1/tickets/{id}` | ✅ | |
| `POST /api/v1/tickets/{id}/status` | ✅ | Body is `{status}` only |
| `GET /api/v1/categories` | 🔶 | List (active-only) exists; no create/update/delete |
| `POST/PATCH/DELETE /api/v1/categories` | ❌ | Not built |
| `POST /api/v1/webhooks/twilio/voice{,/gather,/status,/fallback}` | ✅ | Full state machine, signature-validated |
| Everything under Auth, Comments, Attachments, Assign, Users, Audit Log | ❌ | Not built — consistent with the MVP scope decision (no auth, no `users` table) |

Three tables exist: `categories`, `tickets`, `voice_call_sessions`. No `users`, `ticket_comments`, `ticket_attachments`, `notification_log`, or `audit_log` — matches `IMPLEMENTATION_PLAN.md`'s MVP Scope Decision exactly. No Redis/Celery in `requirements.txt` — `BackgroundTasks` only, also as documented.

---

## 1. Missing Endpoints

### Documented in API_SPEC.md but not built
| Endpoint | Blocks | Priority |
|---|---|---|
| `POST /api/v1/tickets/{id}/regenerate-summary` | `AISummaryPanel`'s Regenerate button, currently shipped disabled (frontend Phase 3) | **High** — cheap; `ticket_service.set_ai_summary()` + `generate_summary_for_ticket()` already do the real work, this is a thin route |
| `GET /api/v1/health/ready` | Nothing yet built against it, but `API_SPEC.md` §9 promises it and Settings (Phase 8) plans to reuse it for `DatabaseStatusCard` | Medium |
| `POST/PATCH/DELETE /api/v1/categories` | Nothing frontend-facing yet (no admin UI planned before Phase 3 auth) | Low |
| Auth (`/auth/*`), Comments, Attachments, Assign, Users, Audit Log | Ticket Timeline, assignment, RBAC | Low for now — correctly deferred to backend Phase 3 per `IMPLEMENTATION_PLAN.md`, not blocking any frontend phase through Phase 8 |

### Required by WIREFRAMES.md / FRONTEND_IMPLEMENTATION_PLAN.md, not in API_SPEC.md at all (⬜ new, by design)
| Endpoint | Frontend phase blocked |
|---|---|
| `GET /api/v1/health/dependencies` | Phase 1 (StatusBar), Phase 4 (SystemHealthPanel), Phase 8 (Settings) |
| `GET /api/v1/voice-calls` (list) | Phase 5 |
| `GET /api/v1/voice-calls/{id}` (detail incl. `turns`) | Phase 5, Live Call Monitor |
| `GET /api/v1/voice-calls/summary` | Phase 5 |
| `GET /api/v1/analytics/kpis` | Phase 4 |
| `GET /api/v1/analytics/recent-activity` | Phase 4 |
| `GET /api/v1/analytics/tickets-by-category` | Phase 6 |
| `GET /api/v1/analytics/tickets-by-priority` | Phase 6 |
| `GET /api/v1/analytics/tickets-by-source` | Phase 6 |
| `GET /api/v1/analytics/calls-by-day` | Phase 6 |
| `GET /api/v1/analytics/escalation-rate` | Phase 6 |
| `GET /api/v1/analytics/ai-summary-usage` | Phase 6 |
| `GET /api/v1/ai-insights` | Phase 7 (partially — see §3) |
| `GET /api/v1/settings/status` | Phase 8 |

---

## 2. Missing Schema Fields

| Field | Where it should live | Impact |
|---|---|---|
| `source` on `TicketListItem`/`TicketRead` | `backend/app/schemas/ticket.py` | **Confirmed bug, not a documentation gap.** The `tickets.source` column exists and is populated correctly by both the web and voice intake paths — it's just never serialized. `SourceBadge` on the frontend renders "Unknown" for every ticket today because of this one omission. Fixing it is a one-line schema change, no migration needed. |
| `ai_model` on `Ticket` | `backend/app/db/models.py` + schema | Not persisted anywhere; `summarizer.py` never records which model produced a given summary. `AI Analysis` panel shows "not recorded" — accurate today, but cheap to fix going forward (new nullable column + one line in `set_ai_summary`) |
| `transcript` on `Ticket` | `backend/app/db/models.py` + schema | Open decision from `WIREFRAMES.md` §8. Phone-ticket transcripts are currently concatenated into `description` by the voice orchestrator. Recommended fix: add the column, have the orchestrator write to it directly instead of concatenating |
| `resolution_note` on the status-update request | `TicketStatusUpdate` schema | `API_SPEC.md`'s target contract shows `{status, resolution_note}`; the real schema only accepts `{status}`. Low priority — no frontend phase currently sends this field |
| `assigned_agent_id`, `resolved_at`, `closed_at`, `due_at`, `requester_user_id` | `Ticket` model | All in `DATABASE_DESIGN.md`'s target schema, none in the MVP model. Correctly deferred — nothing through Phase 8 needs them |
| Per-turn `confidence` reliability in `voice_call_sessions.turns` | Orchestrator write path | `CategoryPredictionCard`/`ConfidenceMeter` (Live Call Monitor) assume this is populated on every turn. **Needs verification against the running orchestrator before that component ships** — the column shape allows it, but nothing in this analysis confirms every code path fills it in |

---

## 3. Missing Aggregations

None exist. Not "some are missing" — there is currently **zero** aggregation query anywhere in the codebase reachable via an API. `OPERATIONS_RUNBOOK.md`'s escalation-rate SQL is explicitly ad hoc (an operator runs it by hand), not wired to anything.

Every one of these needs to be built from scratch:
- Open Tickets / Tickets Today / Calls Today / Escalations counts (Dashboard KPIs)
- **"AI Resolution Rate"** — blocked on a product decision before any query is written (three plausible, non-equivalent definitions exist per `DESIGN.md` §20; do not pick one silently)
- Tickets by Category / Priority / Source breakdowns
- Calls per day trend
- Escalation rate over time
- AI summary usage breakdown (bucketed by `ai_summary_status` — this one's cheap, the column already exists and is populated)
- Recent-activity feed (a `tickets` ∪ `voice_call_sessions` union, ordered by time)
- AI Insights' "trending issues" / "most common categories" (needs the "trending vs. most common" distinction defined first, per `WIREFRAMES.md` §8)

---

## 4. Missing Database Objects

| Object | Blocks | Notes |
|---|---|---|
| `audit_log` table | `TicketTimeline` (permanently blocked state today, correctly) | Phase 3 backend scope per `IMPLEMENTATION_PLAN.md` — not urgent |
| `users` table | Assignment, RBAC, `assigned_agent_id` | Same — Phase 3 backend scope |
| `notification_log` table | Delivery-status visibility beyond app logs | Not blocking any frontend phase directly |
| `ticket_comments`, `ticket_attachments` tables | Comment thread, attachments | Not in any frontend phase through 8 |
| Call-state history (currently only the *current* `state` is stored) | `CallTimeline` (permanently blocked today, correctly) | Would need either a history table or an append-only JSONB column on `voice_call_sessions`, populated by the orchestrator on every transition |
| `tickets.transcript` column | Ticket Drawer transcript section | See §2 |
| `tickets.ai_model` column | AI Analysis panel | See §2 |
| Any persisted "insight" concept | `InsightFeed` component | Doesn't need to exist if AI Insights stays live-computed rather than a stored feed — recommend **not** building this table unless a real need for historical insight tracking emerges |
| A cache/table for dependency health | `GET /health/dependencies` | Not strictly required — see §5, this can be a request-time check with an in-process TTL cache instead of a stored table |

---

## 5. Missing Background Jobs

| Job | Needed for | Recommendation |
|---|---|---|
| Dependency health-check refresh (OpenAI/Twilio/Email reachability) | `GET /health/dependencies` | `DESIGN.md` §6.1 explicitly calls for this to be cached, not a live call on every dashboard load. A lightweight in-process cache with a short TTL (e.g. 30s) is sufficient at this volume — a full scheduled job/worker is not needed given there's still no Redis/Celery in this stack |
| Call-state history recording | `CallTimeline` | Would be a code change in `app/voice/orchestrator.py` (append to a JSONB history array on every state transition), not a scheduled job |
| Notification retry/backoff | Reliability of email delivery | Currently fire-and-forget via `BackgroundTasks`; a failure is logged, not retried. Acceptable per `IMPLEMENTATION_PLAN.md`'s risk register at MVP volume — flagged, not urgent |
| Analytics pre-aggregation | Dashboard/Analytics performance | Not needed yet — `DATABASE_DESIGN.md` §6 sizing note says live queries are fine until `tickets` approaches millions of rows. Building a pre-aggregation job now would be solving a problem that doesn't exist |

No job is missing that blocks correctness today — the gaps in this section are about the *endpoints* those jobs would eventually support (§1, §3), not about a job that's actively needed and absent.

---

## 6. Dependency Graph

```
tickets.source not serialized ──────────► SourceBadge shows "Unknown" (frontend Phase 3, already shipped defensively)
regenerate-summary endpoint missing ────► AISummaryPanel's Regenerate button (shipped disabled)

GET /health/dependencies ──────┬───────► StatusBar (Phase 1)
                                ├───────► SystemHealthPanel (Phase 4)
                                └───────► Settings status cards (Phase 8)

GET /voice-calls* ──────────────────────► Calls page, Voice Ops Center, Live Call Monitor (Phase 5)
        │
        └── depends on nothing new in the DB — voice_call_sessions is fully populated already

GET /analytics/kpis ────────────────────► Dashboard KPI cards (Phase 4)
        │
        └── AI Resolution Rate sub-metric BLOCKED on a product decision
                (this decision also blocks Analytics' equivalent chart, Phase 6)

GET /analytics/* (6 chart endpoints) ───► Analytics page (Phase 6)
        │
        └── depends on AI Resolution Rate decision (partial — 5 of 6 charts don't need it)

GET /ai-insights ────────────────────────► AI Insights page (Phase 7)
        │
        ├── "Trending Issues" / "Most Common" — needs a definition decision, otherwise buildable now
        ├── "Repeated Problems" — BLOCKED on a caller-identity concept that doesn't exist in the schema at all
        └── "Recommendations" — BLOCKED on an undefined output shape

audit_log table ─────────────────────────► TicketTimeline (Phase 3, already shipped as permanently-blocked)
call-state history ───────────────────────► CallTimeline (Phase 5, will ship as permanently-blocked without this)
```

Reading order: `source` serialization and the regenerate-summary endpoint are standalone, zero-dependency fixes. Everything under `health/dependencies` is one shared endpoint feeding three different frontend phases — build it once. The Analytics/AI Insights branches both hang off the same unresolved "AI Resolution Rate" and "trending vs. most common" product decisions — resolving those two definitions unblocks the most work per decision made.

---

## 7. Recommended Backend Implementation Order

Ordered as requested — ticket-related APIs first, then Dashboard, then Calls, then Analytics, then AI Insights — with the zero-risk fixes pulled to the very front regardless of category, since they unblock already-shipped frontend code for free.

### Tier 0 — Ticket API fixes (small, no migration, unblocks Phase 3 today)
1. Add `source` to `TicketListItem` and `TicketRead` (`backend/app/schemas/ticket.py`) — one line each, no migration, the column is already populated correctly.
2. Build `POST /api/v1/tickets/{id}/regenerate-summary` — reuses `set_ai_summary()` (reset to `PENDING`) + `generate_summary_for_ticket()` as a background task, matching the create-ticket pattern already in `tickets.py`.
3. Add `priority` and `source` query params to `GET /api/v1/tickets` — same shape as the existing `category_id` filter.
4. Add `q` full-text search param, using the existing unused `ix_tickets_fts` GIN index.
5. Make `GET /api/v1/health/ready` real — an actual `SELECT 1` against the DB, per `API_SPEC.md` §9's intent (today's `/health` is a static stub).

### Tier 1 — Dashboard & Settings foundation
6. `GET /api/v1/health/dependencies` — cached (short TTL, in-process) reachability checks for OpenAI, Twilio, Email, DB. Build once, reused by Header's `StatusBar`, Dashboard's `SystemHealthPanel`, and Settings.
7. `GET /api/v1/settings/status` — masked-key/configured-or-not per provider, built on top of #6 plus `app/core/config.py`'s settings object. **Never returns an unmasked secret.**
8. `GET /api/v1/analytics/kpis` — Open Tickets, Tickets Today are trivial counts; Calls Today/Escalations need Tier 2's data; AI Resolution Rate is blocked until its definition is resolved (flag it, ship the rest).
9. `GET /api/v1/analytics/recent-activity` — a `tickets` ∪ `voice_call_sessions` union ordered by `created_at`.

### Tier 2 — Calls / Voice Operations Center
10. `GET /api/v1/voice-calls` — paginated list, same shape as `GET /api/v1/tickets`, filterable by `state`/`escalated`.
11. `GET /api/v1/voice-calls/{id}` — detail including `turns`; verify per-turn `confidence` is actually populated by the orchestrator before Live Call Monitor's `ConfidenceMeter` is wired to it.
12. `GET /api/v1/voice-calls/summary` — counts by state/escalated, same query family as #8's call metrics.

### Tier 3 — Analytics
13. **Resolve the "AI Resolution Rate" definition** — a product decision, not an engineering task, but it blocks part of #8 and one Analytics chart. Do this before writing the query, not after.
14. Build the five aggregation endpoints that don't depend on that decision: tickets-by-category, tickets-by-priority, tickets-by-source, calls-by-day, ai-summary-usage.
15. Build escalation-rate, formalizing the ad hoc SQL already in `OPERATIONS_RUNBOOK.md` §3.3.

### Tier 4 — AI Insights (last, deliberately — per WIREFRAMES.md §8's own recommendation)
16. Resolve the "trending vs. most common" definition, then build that aggregation (data source is ready: `tickets.category_id` + `created_at`).
17. Leave "Repeated Problems" and "Recommendations" unbuilt until their respective blockers (a caller-identity concept; a defined recommendation output shape) are resolved at the product level — building either now would mean shipping fabricated logic behind a real-looking endpoint, which is worse than the honest "not yet available" state the frontend already renders.

---

## Stopping Point (original, superseded)

This section described the state before implementation began. Tiers 0–4 are now implemented — see the Status Update near the top of this document, `WORK_LOG.md` for the detailed record, and `REMAINING_PRODUCT_DECISIONS.md` for what's left.

# HFMG AI Help Desk — Performance Review

Scope: `backend/app/services/{analytics_service,ticket_service,ai_insights_service,voice_call_service}.py`,
`backend/app/db/models.py` + `backend/alembic/versions/*`, `backend/app/api/v1/*.py`,
`frontend/src/api/*.ts` + `frontend/src/pages/*.tsx`. Twilio has no live credentials yet, so
voice-call query/schema completeness was reviewed but no Twilio-blocked work was chased.

Live evidence (`EXPLAIN`, `EXPLAIN ANALYZE`) was gathered against the local `hfmg_helpdesk` dev
DB via psql. That DB currently holds **3 tickets, 10 categories, 0 voice_call_sessions** — far
too small for the Postgres planner to ever prefer an index over a sequential scan on its own.
Per the review brief, plan *shape* (seq scan vs. index scan, join strategy, nested-loop counts)
is treated as signal; raw millisecond timings and even the planner's scan choice on this tiny
dataset are not treated as evidence that something is fine at production scale.

## Overview

The backend is in good shape overall. `analytics_service.py` does all aggregation in SQL
(`func.count()`, `GROUP BY`) rather than pulling rows into Python — no full-table-scan-into-Python
aggregation anti-pattern anywhere. `ticket_service.py` and `voice_call_service.py` use
`selectinload` for the one relationship that's actually accessed (`Ticket.category`), pagination
is real (`LIMIT`/`OFFSET` plus a real `COUNT`), and no API route or service loops issuing a
per-row query was found anywhere in `backend/app/api/v1/*.py` or `backend/app/voice/*.py`. The
one genuine gap is schema-level: `tickets.category_id` is a foreign key with no index, which
Postgres does not create automatically (unlike the primary key). That gap is fixed below via a
new Alembic migration.

The frontend is similarly disciplined: `refetchOnWindowFocus` is globally disabled
(`frontend/src/lib/queryClient.ts`), dashboard panels fetch independently in parallel rather than
waterfalling, and the two places that intentionally poll (`StatusBar`/`SystemHealthPanel` on a
shared `["health","dependencies"]` key, `KPIGrid`) dedupe correctly and pause when the tab is
backgrounded (React Query's `refetchIntervalInBackground` defaults to `false`). The one real find
is `NewTicketPage.tsx`, which fetches categories via a raw `useEffect` + local state instead of
the existing `useCategoriesQuery()` hook, so it never benefits from the cache every other category
consumer shares.

## N+1 Risks

**None found in the reviewed backend code.** Specifically checked and clear:

- `backend/app/api/v1/tickets.py`, `voice_calls.py`, `analytics.py`, `ai_insights.py`,
  `categories.py` — every route calls exactly one service function and iterates only over an
  already-fully-loaded, in-memory list (e.g. `analytics.py:37-49` builds `ActivityItem`s from
  `ticket.category.name` inside a list comprehension, but `analytics_service.get_recent_activity`
  (`analytics_service.py:84-86`) already eager-loads `Ticket.category` via
  `selectinload(Ticket.category)` before the session closes, so this is one query, not N+1).
- `backend/app/services/ticket_service.py:66-71` (`get_ticket`) and `:112` (`list_tickets`) both
  eager-load `Ticket.category` the same way, so `TicketRead`/`TicketListItem` Pydantic
  serialization (which reads `ticket.category.name`) never triggers a lazy load after the session
  context — the exact pattern the review brief called out to look for.
- `backend/app/voice/orchestrator.py` and `session.py` — each webhook turn does a small, fixed
  number of point lookups (`db.get(Ticket, ...)`, one `SELECT ... WHERE twilio_call_sid = ...`,
  one `SELECT ... WHERE name = ...`), never a per-item loop over a result set.

## Missing Indexes

| Column | Query that needs it | Status |
|---|---|---|
| `tickets.category_id` | `ticket_service.list_tickets` filter (`Ticket.category_id == category_id`, `ticket_service.py:88-89`) and `analytics_service.get_tickets_by_category`'s join (`Ticket.category_id == Category.id`, `analytics_service.py:96`, also reused by `ai_insights_service.get_ai_insights`) | **Added** — migration `dd3a82a4a05a_add_index_on_tickets_category_id.py` |
| `tickets.status` | `ticket_service.list_tickets` equality filter; `analytics_service.get_kpis`'s `Ticket.status.notin_(_CLOSED_TICKET_STATUSES)` (`analytics_service.py:49`) | Recommended, not added |
| `tickets.priority`, `tickets.source` | `ticket_service.list_tickets` equality filters | Not recommended (see reasoning below) |
| `voice_call_sessions.escalated`, `.state` | `voice_call_service.list_voice_calls` filters; `get_escalation_rate`/`get_summary` | Not recommended yet (table is empty pending Twilio) |

### `tickets.category_id` — added via migration

`category_id` is a `ForeignKey("categories.id")` with no `index=True` and no explicit `Index(...)`
in the original schema (`backend/alembic/versions/34903ab29078_...py`). Postgres does **not**
auto-index FK columns (only the referenced side's primary key is indexed automatically), so this
was a real gap, not a false positive. Confirmed directly:

```
EXPLAIN SELECT * FROM tickets WHERE category_id = (SELECT id FROM categories LIMIT 1);
 Seq Scan on tickets  (cost=0.08..11.33 rows=1 width=750)
   Filter: (category_id = $0)
```

— a `Seq Scan` with a `Filter:` (not `Index Cond:`) confirms no index was in play. This is the
exact filter `list_tickets` runs on every `GET /tickets?category_id=...` (department/category
queue filtering, a normal UI action) and the exact join column `get_tickets_by_category` uses for
every `/analytics/tickets-by-category` and `/ai-insights` call. At the "low thousands of
tickets/year" volume `DATABASE_DESIGN.md` §6 sizes this system for, every one of those becomes a
full-table scan that grows linearly with total ticket count.

**Implemented:**
- New migration `backend/alembic/versions/dd3a82a4a05a_add_index_on_tickets_category_id.py`
  (`op.create_index('ix_tickets_category_id', 'tickets', ['category_id'])`, with a matching
  `op.drop_index` in `downgrade()`).
- Additive metadata-only change in `backend/app/db/models.py`: added `index=True` to
  `Ticket.category_id`'s `mapped_column(...)` so the ORM's own table metadata matches the DB and
  a future `alembic revision --autogenerate` won't propose dropping it. No query logic in
  `models.py` or any service file was touched.
- Verified clean: `alembic upgrade head` (index created), `alembic downgrade -1` (index dropped,
  confirmed via `\d tickets`), `alembic upgrade head` again (index recreated, confirmed via
  `\d tickets`) — both directions applied without error.
- Verified the index is well-formed and actually usable (not just present) by forcing the
  planner's hand, since 3 rows isn't enough for it to choose an index scan on its own:
  ```
  SET enable_seqscan = off;
  EXPLAIN SELECT * FROM tickets WHERE category_id = (SELECT id FROM categories LIMIT 1);
   Index Scan using ix_tickets_category_id on tickets  (cost=0.61..8.63 rows=1 width=750)
     Index Cond: (category_id = $0)
  ```
  At current row counts the planner correctly still prefers a seq scan when `enable_seqscan` is
  left on (a 3-row table fits in one page — an index lookup would cost more than the scan) — this
  is the planner being correct, not evidence the index is unnecessary. The index exists and will
  be picked automatically once ticket volume grows past that crossover point.

### `tickets.status` — recommended, not added

`get_kpis`'s open-ticket count uses `status.notin_(...)` over three terminal statuses, and
`list_tickets` filters on `status` equality. Not implemented because: (a) status has only 7
values and Postgres's planner is unlikely to use a plain single-column btree for a `NOT IN`
predicate effectively regardless (it would need either a partial index specifically on
non-terminal statuses, or the planner would still favor a scan once "open" stops being a small
minority of all tickets); and (b) there's no live evidence yet — the table has 3 rows, and the
review brief scopes migrations to "genuinely missing" indexes backing a real, evidenced need, not
speculative ones. If ticket volume grows and `get_kpis`/`list_tickets` show up as slow in real
`EXPLAIN ANALYZE` output, the right fix is a **partial** index
(`CREATE INDEX ... ON tickets (status) WHERE status NOT IN ('RESOLVED','CLOSED','CANCELLED')`),
not a plain one — worth flagging now so whoever adds it later doesn't reach for the default.

### `tickets.priority` / `tickets.source` — not recommended

Both are low-cardinality enums (4 and 4 values respectively) used only as optional equality
filters alongside `category_id`/`status`/`q` in `list_tickets`. A single-column index on either is
unlikely to ever be more selective than a sequential scan at this system's stated volume ("low
thousands of tickets/year" per `DATABASE_DESIGN.md` §6) — Postgres will keep preferring the scan.
Not worth the write-amplification cost of maintaining them.

### `voice_call_sessions.escalated` / `.state` — not recommended yet

`voice_call_sessions` has **0 rows** in this dev DB (Twilio has no credentials yet, so nothing has
ever written to this table outside of tests). `list_voice_calls`/`get_escalation_rate`/
`get_summary` filter and group by these columns, but there is no live data to evidence a real
access pattern against, and per the review's instructions this is explicitly Twilio-blocked work
not worth chasing now. Worth revisiting once the voice agent is live and this table has real
volume — `state` has 11 values and is read via `GROUP BY` in `get_summary` (which always needs a
full scan regardless of indexing), while `escalated` is a boolean filtered alongside
`created_at >= since` in `get_escalation_rate`, where a composite `(created_at, escalated)` index
would be the more useful shape than a plain index on `escalated` alone.

## Slow Query Risks

- **No unbounded scans found in the reviewed services.** Every list endpoint
  (`list_tickets`, `list_voice_calls`) has real `LIMIT`/`OFFSET` pagination backed by a genuine
  `SELECT count(*)` rather than fetching everything and slicing in Python.
- **All analytics aggregates are computed in SQL**, not Python: `get_kpis`, `get_tickets_by_category`,
  `get_tickets_by_priority`, `get_tickets_by_source`, `get_calls_by_day`, `get_escalation_rate`,
  and `get_ai_summary_usage` (`analytics_service.py`) all use `func.count()` /
  `GROUP BY` at the database level. `get_tickets_by_priority`/`get_tickets_by_source`/
  `get_ai_summary_usage` do zero-fill missing buckets in Python (`analytics_service.py:112-114`,
  `:123-124`, `:176-181`), but that's a fixed-size loop over 4-7 enum values, not a scan-avoidance
  problem.
- **`list_tickets`'s free-text search (`q`) uses `ILIKE '%...%'` across four columns**
  (`ticket_service.py:102-109`, including `Ticket.description.ilike(...)` and
  `Ticket.ai_summary.ilike(...)`, both `Text` columns). A leading-wildcard `ILIKE` cannot use a
  plain btree index at all — this is already called out explicitly in the code's own comment
  (`ticket_service.py:95-101`: the full-text `to_tsvector`/GIN index API_SPEC.md describes was
  never actually migrated in, so ILIKE is used as a "correctness-equivalent stand-in" for now).
  This is a real full-table-scan-per-search risk once ticket volume grows past "low thousands,"
  but it's already a documented, deliberate gap rather than an oversight, and adding a GIN/
  `pg_trgm` index is a bigger schema change than this review's scope covers (it's a new index
  *type* + extension, not a straightforward `create_index`) — **flagged as a recommendation, not
  implemented** (see below).
- **`ticket_service._next_ticket_number`** (`ticket_service.py:24-36`) computes the next human
  ticket number by `COUNT(*) WHERE ticket_number LIKE 'HFMG-<year>-%'` on every ticket creation.
  This is a `count()` against an indexed prefix (`ix_tickets_ticket_number` is a btree on the full
  column, and `startswith()` compiles to a `LIKE 'prefix%'`, which *can* use a leading-anchor
  btree index) so it isn't a sequential scan, but it does mean ticket creation cost grows (mildly)
  with tickets-per-year, and — as the code's own docstring already flags — it's a count-based
  scheme with a documented future migration path to a real Postgres sequence once concurrent
  creation volume makes it a contention risk. Not re-flagged as new since it's already tracked.
- **`AnalyticsPage`/`AIInsightsPage` charts all default to a `days` window** (`DaysWindow = Query(30, ...)`,
  `analytics.py:22`) bounded by `ge=1, le=365`, so even the worst case (`days=365`) is a bounded
  window scan, not unbounded — fine as-is.

## Frontend Over-fetching / Unnecessary API Calls

- **`NewTicketPage.tsx` bypasses TanStack Query's cache for categories.** `TicketsPage`/
  `TicketFilters` load categories via `useCategoriesQuery()` (`frontend/src/api/tickets.ts:5-7`,
  cached under `["categories"]`), but `NewTicketPage.tsx:25-35` instead calls
  `api.listCategories()` directly inside a raw `useEffect` with local `useState`. Every visit to
  "New Ticket" issues a fresh, uncached `GET /categories` even if the same list was already fetched
  (and is sitting in cache) from the Tickets page moments earlier, and it gets none of React
  Query's built-in retry/error/loading state handling that every other list on this frontend has.
  **Recommended, not implemented** (frontend query logic is out of this review's implementation
  scope) — swap the `useEffect`/`useState` pair for `useCategoriesQuery()` and read `.data`.
- **No global `staleTime`** is set (`frontend/src/lib/queryClient.ts:3-10` only sets `retry: 1` and
  `refetchOnWindowFocus: false`). With the React Query default `staleTime: 0`, every remount of a
  page's query hooks (e.g. clicking Dashboard → Tickets → Dashboard) is treated as stale
  immediately and refetches on mount even though `refetchOnWindowFocus` is off and nothing
  changed. This is not currently a *storm* (the window-focus source of most refetch storms is
  already disabled, and cross-tab background polling correctly pauses per React Query's
  `refetchIntervalInBackground: false` default), but it does mean every routine nav-away-and-back
  during a shift refetches KPIs, recent activity, recent tickets/calls, and AI insights from
  scratch rather than serving from cache for a few seconds. **Recommended, not implemented**: a
  modest shared `staleTime` (e.g. 15-30s, matching the existing 30s poll intervals) on
  `queryClient`'s `defaultOptions.queries` would cut this without adding any staleness risk beyond
  what the poll intervals already accept.
- **`AIInsightsPreview` fetches the full `/ai-insights` payload just to show the top 3 categories**
  (`frontend/src/components/dashboard/AIInsightsPreview.tsx:17,39`: `data.category_breakdown.items.slice(0, 3)`).
  At the current category count (10 in this dev DB) this is trivial, but the backend endpoint has
  no `limit`/`top` parameter, so the preview always pulls every category's count to show three.
  Low priority given `DATABASE_DESIGN.md`'s expected category cardinality, but noted since it's
  the one spot fetching a full list where a summary would do. **Recommended, not implemented**
  (would require a backend endpoint change, out of this review's scope).

**Not flagged (checked and found fine):**
- Dashboard panels (`SystemHealthPanel`, `KPIGrid`, `RecentTicketsPanel`, `RecentCallsPanel`,
  `AIInsightsPreview`, `ActivityFeed`) each use independent `useQuery` hooks and fetch in parallel
  — the page's own comment (`DashboardPage.tsx:8-11`) states this is intentional so one slow
  endpoint doesn't block the rest, and it's true in the code, not just the comment.
  `AnalyticsPage`'s `MetricsGrid` reuses the same `useTicketsByCategoryQuery`/`useCallsByDayQuery`
  hooks (identical query keys) that `CategoryChart`/`CallsPerDayChart` already use on the same
  page — React Query dedupes these to one request each, not a duplicate fetch.
- `StatusBar` (always-visible header) and `SystemHealthPanel` (Dashboard-only) share the literal
  same query key `["health", "dependencies"]` and are explicitly commented as doing so
  (`SystemHealthPanel.tsx:7-9`) — confirmed they will never double-fetch when both are mounted.
- No sequential/waterfalled `useQuery` chains (`enabled: query1.data && ...`) were found anywhere
  in `frontend/src/pages` or `frontend/src/components` — the only two `enabled:` usages
  (`api/tickets.ts:38`, `api/voiceCalls.ts:58`) gate on a route param being present, not on
  another query's result.
- `useTicketQuery`'s 3s `refetchInterval` while `ai_summary_status === "PENDING"`
  (`tickets.ts:41`) is scoped correctly: it only polls while a drawer for that specific ticket is
  open and only while a summary is actually pending, not globally.

## Implemented Changes

1. `backend/alembic/versions/dd3a82a4a05a_add_index_on_tickets_category_id.py` (new migration) —
   adds `ix_tickets_category_id` (btree on `tickets.category_id`), with a matching `downgrade()`.
2. `backend/app/db/models.py` — added `index=True` to `Ticket.category_id`'s `mapped_column(...)`
   (metadata-only; no query logic touched).
3. Applied and verified against the local dev DB: `alembic upgrade head` →
   `alembic downgrade -1` → `alembic upgrade head`, all clean; confirmed the index appears/
   disappears correctly at each step via `\d tickets`, and confirmed via
   `SET enable_seqscan = off; EXPLAIN ...` that the index is well-formed and actually selectable
   by the planner (not just present in the catalog).

No service, API route, or frontend query-logic files were modified — only the one migration and
the one additive model-metadata line, per this review's scope.

## Recommendations Not Implemented (with reasoning)

- **Partial index on `tickets.status`** for the non-terminal-status filter in `get_kpis`/
  `list_tickets` — deferred because there's no live evidence yet (3-row table) and a plain index
  wouldn't actually be the right shape for a `NOT IN` predicate; a partial index is, but that's a
  bigger, more speculative change than this review's "genuinely missing, evidenced" bar.
- **GIN/`pg_trgm` index (or real `to_tsvector` full-text index) for `list_tickets`'s `q` search** —
  the current `ILIKE '%...%'` scan is already a known, documented gap in the code itself
  (`ticket_service.py:95-101`), not something this review discovered. Fixing it properly means
  adding a Postgres extension and a non-trivial index type, which is a bigger schema decision than
  a narrow "add the missing index" migration — left as a recommendation for a dedicated follow-up.
- **Indexes on `voice_call_sessions.state`/`.escalated`** — the table is empty pending Twilio
  credentials; per this review's instructions, Twilio-blocked work wasn't chased. Revisit once the
  voice agent is live and real access patterns exist to evidence against.
- **Frontend: `NewTicketPage.tsx` category fetch → `useCategoriesQuery()`, and a shared
  `staleTime` on `queryClient`** — both are real, low-risk improvements, but the review's
  instructions scope implementation to backend index migrations only; frontend query logic and
  API/service business logic are report-only findings here, left for the frontend
  owner/production-hardening workstream to apply.
- **`AIInsightsPreview` over-fetch (full category breakdown for a top-3 view)** — would need a
  backend endpoint/parameter change (e.g. `?limit=3` on `/ai-insights` or a dedicated preview
  endpoint), which is service/API logic and out of this review's implementation scope; noted for
  the backend owner.

# HFMG AI Help Desk — Component Architecture

**Version:** 1.0
**Status:** Design Phase (companion to `DESIGN.md`, `WIREFRAMES.md`, `DESIGN_SYSTEM.md`)
**Audience:** Frontend engineers building the React/TypeScript app

No code in this document — component contracts only (purpose, props, data, state, hierarchy, endpoints), so implementation can start without re-deriving UX decisions. Every component carries a build-status tag inherited from `WIREFRAMES.md`:

**Status note (final pre-production review pass):** this document's per-component `[✅]`/`[🔶]`/`[⬜]` tags were written before Frontend Phases 1–8 shipped and are now historical — nearly every component named here exists and is wired to real data (see `FRONTEND_WORK_LOG.md` for the actual component inventory built in each phase, which occasionally differs in naming/structure from what was speculatively planned here, e.g. `TicketRow` was implemented as inline `DataTable` column definitions rather than a standalone component). Treat this document as the original design contract and `FRONTEND_WORK_LOG.md`/the `src/` tree itself as the current source of truth for what actually exists.

| Tag | Meaning |
|---|---|
| ✅ **Built** | Backend data + endpoint exist; component can be wired to real data today |
| 🔶 **Partial** | Some backend support exists (data OR endpoint, not both), or an open decision blocks it |
| ⬜ **Vision** | No backend support exists; component renders against a future endpoint |

A component's tag describes its **data readiness**, not whether the component's UI shell is worth building — every component in this document should be built with its loading/empty/error states regardless of tag, so the shell is ready the moment its endpoint ships.

---

## Application Structure

```
App
 └─ AppShell
     ├─ Sidebar
     ├─ Header
     │   ├─ StatusBar
     │   └─ NotificationCenter
     └─ PageContainer (routed)
         ├─ DashboardPage        /
         ├─ TicketsPage          /tickets
         ├─ CallsPage            /calls
         │   └─ LiveCallMonitor  /calls/live/:callId
         ├─ AnalyticsPage        /analytics
         ├─ AIInsightsPage       /ai-insights
         └─ SettingsPage         /settings
```

---

## Root Layout

### AppShell
- **Purpose:** top-level frame — mounts Sidebar, Header, and the routed page; owns the theme provider and query client context.
- **Props:** `children: ReactNode` (routed page content, injected by the router, not passed manually).
- **Data Source:** none directly — provides React context (theme, query client, auth stub) to descendants.
- **Dependencies:** `ThemeProvider` (dark/light token switch, `DESIGN_SYSTEM.md` §2.6), TanStack Query `QueryClientProvider`, React Router outlet.
- **States:** n/a (structural).
- **Child Components:** `Sidebar`, `Header`, `PageContainer`.
- **Related API Endpoints:** none.
- **Status:** ✅ — pure structural component, no backend dependency.

### Sidebar
- **Purpose:** persistent navigation across the six top-level sections (`DESIGN.md` §4).
- **Props:** `activeRoute: string`, `collapsed?: boolean` (tablet breakpoint).
- **Data Source:** current route (React Router), no API call.
- **Dependencies:** `NavItem` (internal), Lucide icons.
- **States:** expanded (desktop) / icon-rail collapsed (tablet) / full-screen overlay (mobile, triggered by Header's hamburger).
- **Child Components:** `NavItem` ×6 (Dashboard, Tickets, Calls, Analytics, AI Insights, Settings).
- **Related API Endpoints:** none.
- **Status:** ⬜ — the shell itself doesn't exist yet; current frontend has no sidebar (`DESIGN.md` §3).

### Header
- **Purpose:** product identity, environment indicator, and the always-visible System Status strip.
- **Props:** `environment: "dev" | "staging" | "production"`.
- **Data Source:** `StatusBar`'s dependency data (see below); environment from build-time env var.
- **Dependencies:** `StatusBar`, `NotificationCenter`.
- **States:** default / degraded (visually flags when any `StatusBar` dependency is not operational, e.g. a subtle top border in Warning/Danger color).
- **Child Components:** `EnvBadge`, `StatusBar`, `NotificationCenter`.
- **Related API Endpoints:** none directly (delegates to `StatusBar`).
- **Status:** ⬜.

### StatusBar
- **Purpose:** the four-way OpenAI/Twilio/Database/Email health strip — the single most important trust signal in the app, always visible, never a click away (`DESIGN.md` §5).
- **Props:** none (self-fetching) or `refetchIntervalMs?: number` (default 30000).
- **Data Source:** dependency health aggregation.
- **Dependencies:** TanStack Query (polling), `StatusIndicator`.
- **States:** loading (skeleton dots) / loaded (4 live dots) / stale (last successful check >2 intervals old — shown with a muted "stale" hint on hover).
- **Child Components:** `StatusIndicator` ×4.
- **Related API Endpoints:** `GET /api/v1/health/dependencies` — ⬜ **new endpoint, does not exist today**. `GET /api/v1/health` ✅ exists but is liveness-only and cannot back this component as drawn.
- **Status:** ⬜.

### NotificationCenter
- **Purpose:** toast notification stack (`DESIGN_SYSTEM.md` §16) — surfaces client-side events (save succeeded, status change failed, etc.), not a persisted notification inbox.
- **Props:** none — subscribes to a global toast event bus.
- **Data Source:** none (ephemeral, client-triggered by mutations elsewhere in the app).
- **Dependencies:** Framer Motion (enter/exit), toast state store (e.g. Zustand or a simple context — implementer's choice).
- **States:** empty (nothing rendered) / 1–3 visible toasts / overflow ("+N more").
- **Child Components:** `Toast` (internal, one per variant: success/warning/error/info).
- **Related API Endpoints:** none — triggered by the result of other components' mutations (e.g. `POST /tickets/{id}/status`).
- **Status:** ✅ — purely client-side, no backend dependency; can be built and used immediately by any mutation.

### PageContainer
- **Purpose:** the scrollable content region; owns max-width, side padding, and section spacing (`DESIGN_SYSTEM.md` §5).
- **Props:** `children: ReactNode`.
- **Data Source:** none.
- **Dependencies:** none beyond layout tokens.
- **States:** n/a.
- **Child Components:** the routed page component.
- **Related API Endpoints:** none.
- **Status:** ✅.

---

## Dashboard Components

### 1. Component Tree
```
DashboardPage
 ├─ SystemHealthPanel
 │   └─ StatusIndicator ×4
 ├─ KPIGrid
 │   └─ KPICard ×5
 ├─ RecentTicketsPanel
 │   └─ TicketRow (compact variant) ×10
 ├─ RecentCallsPanel
 │   └─ CallRow (compact variant) ×10
 ├─ AIInsightsPreview
 │   └─ AIInsightWidget ×1–3
 └─ ActivityFeed
```

### 2. Data Flow
Each panel fetches independently (parallel queries, not one waterfall) so a slow endpoint (e.g. AI Insights) never blocks the KPI cards from rendering. `SystemHealthPanel` and `StatusBar` (Header) should share one query key so they don't double-poll the same endpoint.

### 3. API Dependencies
`GET /api/v1/health/dependencies` ⬜, `GET /api/v1/analytics/kpis` ⬜, `GET /api/v1/tickets?page_size=10&sort=-created_at` ✅, `GET /api/v1/voice-calls?page_size=10&sort=-created_at` ⬜, `GET /api/v1/analytics/recent-activity` ⬜, `GET /api/v1/ai-insights?limit=3` ⬜.

### 4. Loading States
Each panel renders its own skeleton independently (card-shaped shimmer blocks matching final layout) — never a single full-page spinner, since panels resolve at different speeds.

### 5. Empty States
`RecentTicketsPanel`/`RecentCallsPanel`: "No tickets/calls yet" (`WIREFRAMES.md` §11). `AIInsightsPreview`: "No notable patterns today." `ActivityFeed`: "No recent activity."

### 6. Error States
Per-panel error banners (not a page-level crash) — e.g. if `analytics/kpis` fails, the KPI grid shows an inline retry state while the rest of the dashboard renders normally.

### 7. Responsive Behavior
Desktop: KPI cards in a 5-column row, two-column panel grid below. Tablet: KPI cards wrap to 2–3 columns, panels stack to one column. Mobile: everything stacks single-column; `ActivityFeed` and `AIInsightsPreview` may collapse behind a "show more" toggle to keep initial scroll length reasonable.

---

#### DashboardPage
- **Purpose:** executive-overview route composing all dashboard panels.
- **Props:** none (route-level).
- **Data Source:** none directly — delegates to children.
- **Dependencies:** all panels below.
- **States:** loading / loaded / partial-error (see §6 above).
- **Child Components:** `SystemHealthPanel`, `KPIGrid`, `RecentTicketsPanel`, `RecentCallsPanel`, `AIInsightsPreview`, `ActivityFeed`.
- **Related API Endpoints:** aggregate of all children's endpoints.
- **Status:** ⬜ — page does not exist today.

#### SystemHealthPanel
- **Purpose:** larger, dashboard-specific rendering of the same four dependency statuses shown compactly in the Header's `StatusBar` — gives room for last-checked timestamps inline rather than on hover.
- **Props:** none.
- **Data Source:** same as `StatusBar`.
- **Dependencies:** `StatusIndicator`.
- **States:** loading / loaded / stale.
- **Child Components:** `StatusIndicator` ×4.
- **Related API Endpoints:** `GET /api/v1/health/dependencies` ⬜.
- **Status:** ⬜.

#### KPIGrid
- **Purpose:** lays out the five KPI cards (`DESIGN.md` §6.2).
- **Props:** `metrics: KpiMetric[]` (from parent query).
- **Data Source:** `GET /api/v1/analytics/kpis`.
- **Dependencies:** `KPICard`.
- **States:** loading (5 skeleton cards) / loaded / error.
- **Child Components:** `KPICard` ×5 (Open Tickets, Tickets Today, Calls Today, Escalations, AI Resolution Rate).
- **Related API Endpoints:** `GET /api/v1/analytics/kpis` ⬜ new; Open Tickets/Tickets Today are individually derivable from `GET /api/v1/tickets` ✅ today if the aggregate endpoint isn't ready yet — acceptable interim wiring, not a substitute for building the real endpoint.
- **Status:** 🔶 — mixed per-card readiness, see §2 KPI honesty note in `WIREFRAMES.md`.

#### KPICard
- **Purpose:** single at-a-glance metric.
- **Props:** `label: string`, `value: number | string | null`, `delta?: { value: number; direction: "up" | "down" }`, `status: "ready" | "pending-definition" | "loading" | "error"`.
- **Data Source:** passed via props from `KPIGrid`.
- **Dependencies:** none beyond design tokens.
- **States:** loading (shimmer) / ready / **pending-definition** (renders `—` with a tooltip, used specifically for AI Resolution Rate until its definition is resolved, per `WIREFRAMES.md` §2) / error.
- **Child Components:** none (leaf).
- **Related API Endpoints:** none directly.
- **Status:** ✅ for the component shell; per-metric data readiness varies (🔶/⬜, see `KPIGrid`).

#### RecentTicketsPanel
- **Purpose:** latest 10 tickets, minimal columns, links into `TicketsPage`.
- **Props:** none (self-fetching) or `limit?: number` (default 10).
- **Data Source:** `GET /api/v1/tickets?page=1&page_size=10&sort=-created_at`.
- **Dependencies:** `TicketRow` (compact variant).
- **States:** loading / loaded / empty ("No tickets yet").
- **Child Components:** `TicketRow` ×N.
- **Related API Endpoints:** `GET /api/v1/tickets` ✅ exists.
- **Status:** ✅.

#### RecentCallsPanel
- **Purpose:** latest 10 calls, mirrors `RecentTicketsPanel`.
- **Props:** `limit?: number` (default 10).
- **Data Source:** `GET /api/v1/voice-calls?page=1&page_size=10&sort=-created_at`.
- **Dependencies:** `CallRow` (compact variant).
- **States:** loading / loaded / empty ("No calls yet").
- **Child Components:** `CallRow` ×N.
- **Related API Endpoints:** `GET /api/v1/voice-calls` ⬜ new (`WIREFRAMES.md` §5).
- **Status:** ⬜.

#### AIInsightsPreview
- **Purpose:** dashboard widget sharing one aggregation endpoint with the full AI Insights page, requesting a smaller page size (`DESIGN.md` §6.4).
- **Props:** `limit?: number` (default 3).
- **Data Source:** `GET /api/v1/ai-insights?limit=3`.
- **Dependencies:** `AIInsightWidget`.
- **States:** loading / loaded / empty / blocked (renders "AI Insights coming soon" if the endpoint doesn't exist yet, rather than an error).
- **Child Components:** `AIInsightWidget` ×1–3.
- **Related API Endpoints:** `GET /api/v1/ai-insights` ⬜ new.
- **Status:** ⬜ — build last per `WIREFRAMES.md` §8's own recommendation.

#### ActivityFeed
- **Purpose:** merged, reverse-chronological feed of ticket and call events.
- **Props:** `limit?: number` (default 15).
- **Data Source:** `GET /api/v1/analytics/recent-activity`.
- **Dependencies:** none beyond icon/timestamp formatting.
- **States:** loading / loaded / empty.
- **Child Components:** `ActivityFeedItem` (internal, one per event type: ticket-created, status-changed, call-completed, call-escalated).
- **Related API Endpoints:** `GET /api/v1/analytics/recent-activity` ⬜ new — no merged event source exists; would need to union `tickets` and `voice_call_sessions` server-side.
- **Status:** ⬜.

---

## Tickets Components

### 1. Component Tree
```
TicketsPage
 ├─ TicketFilters
 ├─ TicketSearch
 ├─ TicketTable
 │   └─ TicketRow ×N
 │       ├─ StatusBadge
 │       ├─ PriorityBadge
 │       └─ SourceBadge
 ├─ Pagination
 └─ TicketDrawer (conditionally mounted on row click)
     ├─ CallerInfoPanel
     ├─ AISummaryPanel
     ├─ TicketTimeline
     └─ (Status Controls — inline, not a separate component; see StatusBadge interactive mode)
```

### 2. Data Flow
`TicketsPage` owns filter/search/pagination state (URL query params, so the view is shareable/refresh-safe) and passes the resolved query to `TicketTable` via a single `GET /api/v1/tickets` call. Clicking a row sets a `selectedTicketId` in URL state (e.g. `?ticket=uuid`), which mounts `TicketDrawer` and triggers its own `GET /api/v1/tickets/{id}` detail fetch — the drawer never receives full ticket data from the row click alone, since the list response intentionally omits `description`/`ai_summary` (`API_SPEC.md` §3).

### 3. API Dependencies
`GET /api/v1/tickets` (list, with filters) ✅ base / 🔶 extended params, `GET /api/v1/tickets/{id}` ✅, `POST /api/v1/tickets/{id}/status` ✅, `POST /api/v1/tickets/{id}/regenerate-summary` ✅ (documented, verify built).

### 4. Loading States
`TicketTable`: skeleton rows (8, matching default page size) on first load; subsequent filter/page changes show a subtle top-of-table progress bar rather than replacing the table with skeletons (avoids layout jump during triage).

### 5. Empty States
No tickets at all → `WIREFRAMES.md` §11's "No Tickets" state with "+ New Ticket" CTA. No results for the current filter/search combination → a distinct, narrower message ("No tickets match these filters" + "Clear filters" action) — do not reuse the zero-data empty state for a zero-results-under-filter state; they mean different things to the user.

### 6. Error States
Table-level error banner with retry — filters/search remain interactive so the user can adjust and retry rather than being stuck.

### 7. Responsive Behavior
Desktop: full table. Tablet: table becomes horizontally scrollable (`DESIGN_SYSTEM.md` §13), filters collapse into a single "Filters" button opening a popover. Mobile: `TicketTable` swaps to a stacked `TicketCard` list (`DESIGN_SYSTEM.md` §12); `TicketDrawer` becomes full-screen.

---

#### TicketsPage
- **Purpose:** ticket management home — the most-built page today, extended per `WIREFRAMES.md` §3.
- **Props:** none (route-level; reads/writes filter state via URL search params).
- **Data Source:** delegates to children.
- **Dependencies:** TanStack Query, React Router (URL state).
- **States:** loading / loaded / filtered-empty / error.
- **Child Components:** `TicketFilters`, `TicketSearch`, `TicketTable`, `Pagination`, `TicketDrawer`.
- **Related API Endpoints:** `GET /api/v1/tickets`.
- **Status:** 🔶.

#### TicketFilters
- **Purpose:** Status / Priority / Category / Source filter controls.
- **Props:** `value: TicketFilterState`, `onChange: (next: TicketFilterState) => void`, `categories: Category[]`.
- **Data Source:** `categories` from `GET /api/v1/categories` (for the Category filter's option list); filter values themselves come from parent (URL state).
- **Dependencies:** `Select` (Category/Priority/Status/Source, each a multi-select per `API_SPEC.md` §3's repeatable params).
- **States:** default / active-filters-applied (shows removable chips).
- **Child Components:** `Select` ×4.
- **Related API Endpoints:** `GET /api/v1/categories` ✅; filter application via `GET /api/v1/tickets?status=&priority=&category_id=&source=` — status ✅, others 🔶 (columns exist, query params don't yet, per `WIREFRAMES.md` §3).
- **Status:** 🔶.

#### TicketSearch
- **Purpose:** free-text search across caller name, ticket number, description.
- **Props:** `value: string`, `onChange: (q: string) => void`, `debounceMs?: number` (default 300).
- **Data Source:** none directly — emits a debounced query string to the parent.
- **Dependencies:** `SearchBar` (common component).
- **States:** empty / typing (debouncing) / active query.
- **Child Components:** `SearchBar`.
- **Related API Endpoints:** `GET /api/v1/tickets?q=` — 🔶: the full-text GIN index (`ix_tickets_fts`) already exists in the schema (`DATABASE_DESIGN.md` §3.1) but the API has no `q` param yet.
- **Status:** 🔶.

#### TicketTable
- **Purpose:** primary ticket list surface — Linear-density, not ServiceNow-density.
- **Props:** `tickets: TicketListItem[]`, `isLoading: boolean`, `onRowClick: (id: string) => void`, `sort: SortState`, `onSortChange`.
- **Data Source:** parent-provided (`GET /api/v1/tickets` result).
- **Dependencies:** `TicketRow`, `EmptyState`, `LoadingState`.
- **States:** loading / loaded / empty / filtered-empty.
- **Child Components:** `TicketRow` ×N.
- **Related API Endpoints:** none directly (data via props).
- **Status:** ✅ base functionality (list rendering); 🔶 for the AI Summary column specifically (open decision, `WIREFRAMES.md` §3/§14).

#### TicketRow
- **Purpose:** one ticket in the table (or, in compact variant, the Dashboard's `RecentTicketsPanel`).
- **Props:** `ticket: TicketListItem`, `onClick: () => void`, `variant?: "default" | "compact"`.
- **Data Source:** props only.
- **Dependencies:** `StatusBadge`, `PriorityBadge`, `SourceBadge`.
- **States:** default / hover / (compact variant omits AI summary excerpt and category columns).
- **Child Components:** `StatusBadge`, `PriorityBadge`, `SourceBadge`.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### StatusBadge
- **Purpose:** ticket status pill, per `DESIGN_SYSTEM.md` §9.2.
- **Props:** `status: TicketStatus`, `interactive?: boolean` (true inside the drawer's Status Controls, false in table rows).
- **Data Source:** props.
- **Dependencies:** none.
- **States:** static (table) / interactive dropdown (drawer, triggers `POST /tickets/{id}/status` on change).
- **Child Components:** none.
- **Related API Endpoints:** `POST /api/v1/tickets/{ticket_id}/status` ✅ (interactive mode only).
- **Status:** ✅.

#### PriorityBadge
- **Purpose:** priority pill with severity icon (`DESIGN_SYSTEM.md` §9.4).
- **Props:** `priority: Priority`.
- **Data Source:** props.
- **Dependencies:** Lucide icon per level.
- **States:** static.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### SourceBadge
- **Purpose:** WEB/PHONE/EMAIL/WALK_IN indicator, ties `PHONE` to the AI accent color (`DESIGN_SYSTEM.md` §2.5).
- **Props:** `source: TicketSource`.
- **Data Source:** props.
- **Dependencies:** none.
- **States:** static.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅ — `source` column exists on `Ticket` (added Phase 2).

#### Pagination
- **Purpose:** page navigation footer for `TicketTable` (and reused by `CallTable`, `AnalyticsTable`).
- **Props:** `page: number`, `pageSize: number`, `total: number`, `onPageChange`, `onPageSizeChange`.
- **Data Source:** parent-provided from the paginated envelope (`API_SPEC.md` §1.1: `page`, `page_size`, `total`, `total_pages`).
- **Dependencies:** none.
- **States:** default / first-page (prev disabled) / last-page (next disabled).
- **Child Components:** none.
- **Related API Endpoints:** none directly.
- **Status:** ✅.

#### TicketDrawer
- **Purpose:** ticket intelligence slide-over (`WIREFRAMES.md` §4) — replaces `TicketDetailPage.tsx`'s full-page layout.
- **Props:** `ticketId: string | null` (null = closed), `onClose: () => void`.
- **Data Source:** `GET /api/v1/tickets/{ticket_id}`.
- **Dependencies:** `Drawer` (common), `CallerInfoPanel`, `AISummaryPanel`, `TicketTimeline`, `StatusBadge` (interactive).
- **States:** closed / loading / loaded / error.
- **Child Components:** `CallerInfoPanel`, `AISummaryPanel`, `TicketTimeline`, `StatusBadge`.
- **Related API Endpoints:** `GET /api/v1/tickets/{id}` ✅, `POST /api/v1/tickets/{id}/status` ✅, `POST /api/v1/tickets/{id}/regenerate-summary` ✅.
- **Status:** 🔶 — all data exists; today it's a full page (`TicketDetailPage.tsx`), not a drawer, and the Transcript section depends on the `tickets.transcript` migration decision (`WIREFRAMES.md` §4).

#### TicketTimeline
- **Purpose:** Activity Timeline section of the drawer.
- **Props:** `ticketId: string`.
- **Data Source:** would be `audit_log` entries filtered by `entity_id = ticketId`.
- **Dependencies:** none.
- **States:** blocked (renders "Activity history requires Phase 3" rather than an empty list — an empty list implies the feature works and there's simply no history, which is false here).
- **Child Components:** `TimelineItem` (internal).
- **Related API Endpoints:** `GET /api/v1/audit-log?entity_id=` — ⬜ `audit_log` table is Phase 3 scope, not in the MVP schema (`IMPLEMENTATION_PLAN.md`).
- **Status:** ⬜.

#### AISummaryPanel
- **Purpose:** AI Summary section — the `AI Summary Card` design component (`DESIGN_SYSTEM.md` §18) wired to a specific ticket.
- **Props:** `summary: string | null`, `status: "PENDING" | "COMPLETED" | "FAILED" | "DISABLED"`, `onRegenerate: () => void`.
- **Data Source:** parent (`TicketDrawer`'s ticket detail fetch).
- **Dependencies:** `AI Summary Card` visual pattern, `Button` (Regenerate, ghost variant).
- **States:** `PENDING` (shimmer + "Generating summary…"), `COMPLETED`, `FAILED` (retry action), `DISABLED`.
- **Child Components:** none.
- **Related API Endpoints:** `POST /api/v1/tickets/{id}/regenerate-summary` ✅.
- **Status:** ✅ — carries over `TicketDetailPage.tsx`'s existing `AI_SUMMARY_COPY` handling unchanged (`DESIGN.md` §8).

#### CallerInfoPanel
- **Purpose:** Caller Information section — name/phone/email.
- **Props:** `callerName: string`, `phoneNumber: string`, `email: string | null`.
- **Data Source:** parent (ticket detail).
- **Dependencies:** none.
- **States:** static; email renders "Not provided" when null (common for voice intake).
- **Child Components:** none.
- **Related API Endpoints:** none directly.
- **Status:** ✅.

---

## Calls Components

### 1. Component Tree
```
CallsPage
 ├─ CallStatsPanel
 ├─ CallTable
 │   └─ CallRow ×N (→ CallStateBadge)
 ├─ Pagination
 └─ CallDrawer (conditionally mounted)
     ├─ TranscriptViewer
     └─ CallTimeline
```

### 2. Data Flow
Mirrors `TicketsPage`: `CallsPage` owns filter/pagination URL state, `CallTable` renders from one list query, row click opens `CallDrawer` with its own detail fetch (needed because `turns` — the full transcript — is not in the list payload, same reasoning as tickets' `description`/`ai_summary`).

### 3. API Dependencies
`GET /api/v1/voice-calls` ⬜ new, `GET /api/v1/voice-calls/{id}` ⬜ new, `GET /api/v1/voice-calls/summary` ⬜ new.

### 4. Loading States
Same skeleton-row pattern as `TicketTable`.

### 5. Empty States
"No calls yet" (`WIREFRAMES.md` §11) — buildable today with no backend work once the empty-array response is confirmed shape-compatible with the new endpoint.

### 6. Error States
Table-level banner; additionally, if the new `/voice-calls` endpoint doesn't exist yet in a given deploy, the whole page should render a single "Calls page is not yet available" state rather than a broken table — this is a page that legitimately doesn't exist on the backend today, and the frontend should say so plainly rather than showing a perpetual error.

### 7. Responsive Behavior
Same pattern as Tickets: horizontal scroll on tablet, `CallCard` stack on mobile, full-screen `CallDrawer` on mobile.

---

#### CallsPage
- **Purpose:** Voice Operations Center home (`WIREFRAMES.md` §5).
- **Props:** none (route-level).
- **Data Source:** delegates to children.
- **Dependencies:** all children below.
- **States:** loading / loaded / unavailable (see §6).
- **Child Components:** `CallStatsPanel`, `CallTable`, `Pagination`, `CallDrawer`.
- **Related API Endpoints:** `GET /api/v1/voice-calls`, `GET /api/v1/voice-calls/summary`.
- **Status:** ⬜.

#### CallStatsPanel
- **Purpose:** Active/Completed/Escalated call counts + AI Agent Status card.
- **Props:** none (self-fetching).
- **Data Source:** `GET /api/v1/voice-calls/summary`.
- **Dependencies:** `KPICard` (reused), `AgentStatusCard`.
- **States:** loading / loaded / error.
- **Child Components:** `KPICard` ×3, `AgentStatusCard`.
- **Related API Endpoints:** `GET /api/v1/voice-calls/summary` ⬜ new.
- **Status:** ⬜ (data exists in `voice_call_sessions`, no aggregation endpoint).

#### CallTable
- **Purpose:** primary call list.
- **Props:** `calls: VoiceCallListItem[]`, `isLoading`, `onRowClick`.
- **Data Source:** parent (`GET /api/v1/voice-calls`).
- **Dependencies:** `CallRow`.
- **States:** loading / loaded / empty.
- **Child Components:** `CallRow` ×N.
- **Related API Endpoints:** none directly.
- **Status:** ⬜.

#### CallRow
- **Purpose:** one call in the table — Caller, Duration, Issue, Priority, Outcome (`WIREFRAMES.md` §5).
- **Props:** `call: VoiceCallListItem`, `onClick`, `variant?: "default" | "compact"`.
- **Data Source:** props.
- **Dependencies:** `CallStateBadge`, `PriorityBadge` (reused from Tickets), `EscalationReasonBadge`.
- **States:** default / hover.
- **Child Components:** `CallStateBadge`, `PriorityBadge`.
- **Related API Endpoints:** none.
- **Status:** ⬜ (component buildable now against a mock shape; wire once the endpoint ships).

#### CallDrawer
- **Purpose:** call detail slide-over — full transcript, state history, linked ticket.
- **Props:** `callId: string | null`, `onClose`.
- **Data Source:** `GET /api/v1/voice-calls/{id}`.
- **Dependencies:** `Drawer`, `TranscriptViewer`, `CallTimeline`.
- **States:** closed / loading / loaded / error.
- **Child Components:** `TranscriptViewer`, `CallTimeline`.
- **Related API Endpoints:** `GET /api/v1/voice-calls/{id}` ⬜ new.
- **Status:** ⬜.

#### TranscriptViewer
- **Purpose:** renders the ordered `turns` JSONB transcript (`{role, text, confidence, at}`) as a chat-style log.
- **Props:** `turns: CallTurn[]`, `isLive?: boolean` (true when used inside `LiveCallMonitor`, enabling auto-scroll-to-bottom on new turns).
- **Data Source:** parent (call detail or live-poll response).
- **Dependencies:** none beyond `Framer Motion` for the live-append fade-in (respecting `prefers-reduced-motion`).
- **States:** empty (call has no turns yet, e.g. `GREETING`) / populated / live-appending.
- **Child Components:** `TranscriptTurn` (internal, styled by `role`: agent vs. caller).
- **Related API Endpoints:** none directly — data via props.
- **Status:** 🔶 — `turns` column exists (✅) and is populated by the orchestrator today; no API exposes it yet (⬜ endpoint).

#### CallTimeline
- **Purpose:** state-machine history for one call (`CALL_FLOW.md` §2's states, in the order they were entered).
- **Props:** `stateHistory: { state: VoiceCallState; enteredAt: string }[]`.
- **Data Source:** would require the orchestrator to log per-state timestamps, which is not currently a separate stored field — `voice_call_sessions` stores only the *current* `state`, not a history of prior states.
- **Dependencies:** none.
- **States:** blocked (renders "State history is not recorded — only the current state is stored" rather than fabricating a history from `turns` timestamps, which would be an approximation the UI shouldn't silently present as exact).
- **Child Components:** `TimelineItem` (shared with `TicketTimeline`).
- **Related API Endpoints:** none exist for this; would require a schema addition beyond `WIREFRAMES.md`'s scoped work.
- **Status:** ⬜.

---

## Live Call Monitoring Components

### 1. Component Tree
```
LiveCallMonitor
 ├─ CurrentCallerCard
 ├─ CallStateProgress (state machine step indicator)
 ├─ TranscriptStream (= TranscriptViewer, isLive=true)
 ├─ CategoryPredictionCard
 │   └─ ConfidenceMeter
 ├─ PriorityPredictionCard
 ├─ TicketPreviewCard
 ├─ AgentStatusCard
 └─ DecisionLogPanel
```

### 2. Data Flow
`LiveCallMonitor` polls `GET /api/v1/voice-calls/{id}` on a 2–5s interval **only** while `state NOT IN (COMPLETED, ESCALATED, ABANDONED)`; polling stops automatically once a terminal state is reached, at which point the page offers a "View in Call Drawer" link instead of continuing to poll a call that's over. This is the honesty constraint from `WIREFRAMES.md` §6: **no WebSocket exists**; "live" means short-interval polling, and the component must not imply otherwise (no "connected" indicator suggesting a persistent socket — the correct indicator is "polling every 3s," stated plainly).

### 3. API Dependencies
`GET /api/v1/voice-calls/{id}` ⬜ new (same endpoint as `CallDrawer`'s detail fetch, reused here at a faster poll interval).

### 4. Loading States
Initial load: full-page skeleton matching the final layout. Subsequent polls: no skeleton — sections update in place; a subtle "updated Xs ago" timestamp confirms freshness without visual disruption.

### 5. Empty States
Not applicable in the traditional sense — this page only renders when a specific `callId` is active; navigating here with no active call redirects to `CallsPage`.

### 6. Error States
A failed poll shows a small inline "connection issue, retrying…" indicator rather than clearing the last-known-good state off the screen — a stale-but-present transcript is more useful mid-call than a blank error page.

### 7. Responsive Behavior
Two-column layout (Transcript left, predictions/status right) on desktop collapses to a single stacked column, Transcript first, on tablet/mobile — the transcript is the highest-value content and should not be pushed below the fold on narrow viewports.

---

#### LiveCallMonitor
- **Purpose:** the AI command-center screen — what's happening on an in-progress call right now (`WIREFRAMES.md` §6).
- **Props:** `callId: string` (route param).
- **Data Source:** `GET /api/v1/voice-calls/{id}`, polled.
- **Dependencies:** TanStack Query (`refetchInterval`, conditional on non-terminal state), all children below.
- **States:** loading / live-polling / terminal (call ended — offers link to `CallDrawer`) / error/stale.
- **Child Components:** `CurrentCallerCard`, `CallStateProgress`, `TranscriptStream`, `CategoryPredictionCard`, `PriorityPredictionCard`, `TicketPreviewCard`, `AgentStatusCard`, `DecisionLogPanel`.
- **Related API Endpoints:** `GET /api/v1/voice-calls/{id}` ⬜ new.
- **Status:** ⬜ — page and endpoint don't exist; underlying data (`voice_call_sessions` columns) is ✅ ready.

#### CurrentCallerCard
- **Purpose:** caller identity as known so far (number, and name once `COLLECT_NAME` has completed).
- **Props:** `fromNumber: string`, `callerName?: string`.
- **Data Source:** parent poll response (`from_number`, `collected.name`).
- **Dependencies:** none.
- **States:** name unknown yet (pre-`COLLECT_NAME`) / name known.
- **Child Components:** none.
- **Related API Endpoints:** none directly.
- **Status:** ✅ data / ⬜ endpoint.

#### CallStateProgress
- **Purpose:** visual step indicator mapping the current `voice_call_state_enum` value to its position in `CALL_FLOW.md` §2's flow.
- **Props:** `currentState: VoiceCallState`.
- **Data Source:** parent poll response (`state`).
- **Dependencies:** a static state→step-index lookup table (matches `CALL_FLOW.md` §2 exactly — do not invent a different step order in the frontend).
- **States:** one visual state per enum value, grouped by phase per `DESIGN_SYSTEM.md` §2.5.
- **Child Components:** none.
- **Related API Endpoints:** none directly.
- **Status:** ✅ data / ⬜ endpoint.

#### CategoryPredictionCard
- **Purpose:** the AI's current category classification and confidence.
- **Props:** `category: string | null`, `confidence: "high" | "medium" | "low" | null`.
- **Data Source:** parent poll response (`collected.category`, and confidence **if** the orchestrator persists it per-turn — verify before wiring, per `WIREFRAMES.md` §6's caveat).
- **Dependencies:** `ConfidenceMeter`.
- **States:** not-yet-classified (pre-description-capture) / classified.
- **Child Components:** `ConfidenceMeter`.
- **Related API Endpoints:** none directly.
- **Status:** 🔶 — category value is stored; confidence persistence needs verification against the running orchestrator, not assumed from the documented column shape.

#### PriorityPredictionCard
- **Purpose:** the AI's inferred priority, plus the announced-out-loud phrase if High/Critical (`VOICE_AGENT_DESIGN.md` §5).
- **Props:** `priority: Priority | null`.
- **Data Source:** parent poll response (`collected.priority`).
- **Dependencies:** `PriorityBadge` (reused).
- **States:** not-yet-assessed / assessed.
- **Child Components:** `PriorityBadge`.
- **Related API Endpoints:** none directly.
- **Status:** ✅ data / ⬜ endpoint. The "why" phrase (e.g. "multi-user impact detected") is **not stored** — only the resulting priority value is; do not fabricate a reason string.

#### ConfidenceMeter
- **Purpose:** the `AI Confidence Indicator` design component (`DESIGN_SYSTEM.md` §18), used inside `CategoryPredictionCard`.
- **Props:** `level: "high" | "medium" | "low"`.
- **Data Source:** props.
- **Dependencies:** none.
- **States:** three fixed visual states, no loading/error (parent handles absence).
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅ component / 🔶 backing data (see `CategoryPredictionCard`).

#### TicketPreviewCard
- **Purpose:** shows the ticket being assembled before `CREATING_TICKET` completes, and links to the real ticket once it exists.
- **Props:** `collected: { callerName?: string; category?: string; priority?: Priority }`, `ticketId?: string`.
- **Data Source:** parent poll response (`collected`, `ticket_id`).
- **Dependencies:** `TicketDrawer` link-out once `ticketId` is set.
- **States:** drafting (no `ticket_id` yet) / created (links to the real ticket).
- **Child Components:** none.
- **Related API Endpoints:** none directly; links to `GET /api/v1/tickets/{id}` ✅ once created.
- **Status:** 🔶.

#### AgentStatusCard
- **Purpose:** whether the **AI voice orchestrator** is healthy — explicitly not a human-staffing/queue view (`WIREFRAMES.md` §5's relabeling note: no human-agent-availability concept exists in the schema).
- **Props:** `status: "healthy" | "degraded" | "unavailable"`, `model: string`.
- **Data Source:** would derive from the same dependency-health check as `StatusBar`'s OpenAI/Twilio indicators, scoped to voice specifically.
- **Dependencies:** `StatusIndicator`.
- **States:** healthy / degraded / unavailable.
- **Child Components:** `StatusIndicator`.
- **Related API Endpoints:** `GET /api/v1/health/dependencies` ⬜ (shared with `StatusBar`).
- **Status:** ⬜.

#### DecisionLogPanel
- **Purpose:** intended to show *why* the AI made a classification/priority call.
- **Props:** `callId: string`.
- **Data Source:** none exists — the model's reasoning is not persisted anywhere; only its structured tool-call output (category, priority, confidence) is stored (`VOICE_AGENT_DESIGN.md` §3).
- **Dependencies:** none.
- **States:** blocked-only — renders "Decision reasoning is not stored; only the resulting classification is available" (`WIREFRAMES.md` §6). **This component must never fabricate a plausible-sounding reasoning string** — that would misrepresent what the system actually knows.
- **Child Components:** none.
- **Related API Endpoints:** none — would require a new "decision log" concept added to the schema, out of scope for current architecture.
- **Status:** ⬜ (structurally blocked, not just unbuilt).

---

## Analytics Components

### 1. Component Tree
```
AnalyticsPage
 ├─ MetricsGrid
 ├─ CategoryChart
 ├─ PriorityChart
 ├─ SourceChart
 ├─ CallsPerDayChart
 └─ EscalationChart
```

### 2. Data Flow
Each chart fetches its own pre-aggregated series independently (`GET /api/v1/analytics/*`) — none of them fetch raw `tickets`/`voice_call_sessions` rows and aggregate client-side (`DESIGN.md` §10, `DESIGN_SYSTEM.md` §17).

### 3. API Dependencies
`GET /api/v1/analytics/tickets-by-category`, `.../tickets-by-priority`, `.../tickets-by-source`, `.../calls-by-day`, `.../escalation-rate`, `.../ai-summary-usage` — all ⬜ new.

### 4. Loading States
Each chart shows a skeleton matching its shape (bar-shaped bars, line-shaped placeholder curve) rather than a generic spinner — reduces layout shift on load.

### 5. Empty States
Per `WIREFRAMES.md` §11: if the aggregation endpoints don't exist in a given deploy, the whole page renders "Analytics is not yet available," not per-chart "no data" states that would wrongly imply the feature is live but empty.

### 6. Error States
Per-chart retry — one failed chart shouldn't block the others from rendering.

### 7. Responsive Behavior
2-column chart grid on desktop, single column on tablet/mobile; `CallsPerDayChart`'s line chart gets full width at all breakpoints (better use of a wide, short chart shape).

---

#### AnalyticsPage
- **Purpose:** trended operational metrics (`WIREFRAMES.md` §7).
- **Props:** `dateRange?: { from: string; to: string }` (URL state, default last 30 days).
- **Data Source:** delegates to children.
- **Dependencies:** `DateRangePicker` (common), all chart components below.
- **States:** loading / loaded / unavailable.
- **Child Components:** `MetricsGrid`, `CategoryChart`, `PriorityChart`, `SourceChart`, `CallsPerDayChart`, `EscalationChart`.
- **Related API Endpoints:** all `/api/v1/analytics/*` endpoints.
- **Status:** ⬜.

#### MetricsGrid
- **Purpose:** small summary stats above the charts (e.g. total tickets in range, total calls in range) — distinct from the Dashboard's `KPIGrid`, which is "right now," not "over this range."
- **Props:** `dateRange`.
- **Data Source:** `GET /api/v1/analytics/kpis?from=&to=` (range-scoped variant of the Dashboard's KPI endpoint, or a separate summary endpoint).
- **Dependencies:** `KPICard` (reused).
- **States:** loading / loaded.
- **Child Components:** `KPICard` ×N.
- **Related API Endpoints:** ⬜ new.
- **Status:** ⬜.

#### CategoryChart
- **Purpose:** Tickets by Category bar chart.
- **Props:** `dateRange`.
- **Data Source:** `GET /api/v1/analytics/tickets-by-category`.
- **Dependencies:** Recharts `BarChart`.
- **States:** loading / loaded / empty (no tickets in range) / error.
- **Child Components:** none (leaf chart).
- **Related API Endpoints:** ⬜ new — aggregates `tickets.category_id`, data source (`tickets`) is ✅.
- **Status:** ⬜.

#### PriorityChart
- **Purpose:** Tickets by Priority bar chart.
- **Props:** `dateRange`.
- **Data Source:** `GET /api/v1/analytics/tickets-by-priority`.
- **Dependencies:** Recharts `BarChart`.
- **States:** loading / loaded / empty / error.
- **Child Components:** none.
- **Related API Endpoints:** ⬜ new.
- **Status:** ⬜.

#### SourceChart
- **Purpose:** Tickets by Source — WEB vs. PHONE (vs. EMAIL/WALK_IN, currently unused sources).
- **Props:** `dateRange`.
- **Data Source:** `GET /api/v1/analytics/tickets-by-source`.
- **Dependencies:** Recharts `PieChart` (≤4 categories, per `DESIGN_SYSTEM.md` §17's pie-chart rule) or `BarChart` if source count ever exceeds 4.
- **States:** loading / loaded / empty / error.
- **Child Components:** none.
- **Related API Endpoints:** ⬜ new.
- **Status:** ⬜.

#### CallsPerDayChart
- **Purpose:** call volume trend, single-series line chart.
- **Props:** `dateRange`.
- **Data Source:** `GET /api/v1/analytics/calls-by-day`.
- **Dependencies:** Recharts `LineChart`.
- **States:** loading / loaded / empty / error.
- **Child Components:** none.
- **Related API Endpoints:** ⬜ new — aggregates `voice_call_sessions.created_at`, data source ✅.
- **Status:** ⬜.

#### EscalationChart
- **Purpose:** escalation rate over time or as a single stat + trend (implementer's call between a sparkline and a single number — `WIREFRAMES.md` §7 draws it as a single stat).
- **Props:** `dateRange`.
- **Data Source:** `GET /api/v1/analytics/escalation-rate`.
- **Dependencies:** none beyond a stat display, or a small Recharts sparkline if trended.
- **States:** loading / loaded / error.
- **Child Components:** none.
- **Related API Endpoints:** ⬜ new — `OPERATIONS_RUNBOOK.md` §3.3 already has the underlying SQL as an ad hoc query; this endpoint formalizes it.
- **Status:** ⬜.

---

## AI Insights Components

### 1. Component Tree
```
AIInsightsPage
 ├─ TrendingIssuesCard
 ├─ RepeatedProblemsCard
 ├─ RiskAlertsCard
 ├─ RecommendationsCard
 └─ InsightFeed
```

### 2. Data Flow
Single `GET /api/v1/ai-insights` call returning all sections in one payload (mirrors the Dashboard preview's shared-endpoint design, `DESIGN.md` §6.4) — do not build five separate endpoints for one page.

### 3. API Dependencies
`GET /api/v1/ai-insights` ⬜ new — and note per `WIREFRAMES.md` §8, several of this endpoint's *concepts* (Repeated Problems, Recommendations) have no defined output shape yet; the endpoint itself cannot be fully specified until those product decisions land.

### 4. Loading States
Full-page skeleton — this page has no partial-render value the way Dashboard's independent panels do, since it's one payload.

### 5. Empty States
"No notable patterns yet" if the endpoint returns successfully with empty sections.

### 6. Error States
Whole-page "AI Insights is not yet available" — per `WIREFRAMES.md` §8's recommendation to build this page last, it is expected to be unavailable for a long stretch of the rollout; the empty/unavailable state should read as normal, not broken.

### 7. Responsive Behavior
Single-column card stack at all breakpoints — this page is read-heavy prose/lists, not tabular, so it doesn't need the same table-scroll/card-swap treatment as Tickets/Calls.

---

#### AIInsightsPage
- **Purpose:** executive intelligence layer (`WIREFRAMES.md` §8).
- **Props:** none.
- **Data Source:** `GET /api/v1/ai-insights`.
- **Dependencies:** all children below.
- **States:** loading / loaded / unavailable.
- **Child Components:** `TrendingIssuesCard`, `RepeatedProblemsCard`, `RiskAlertsCard`, `RecommendationsCard`, `InsightFeed`.
- **Related API Endpoints:** `GET /api/v1/ai-insights` ⬜.
- **Status:** ⬜ — build last, per its own page's recommendation.

#### TrendingIssuesCard
- **Purpose:** time-windowed spike detection (this week vs. last), distinct from all-time frequency.
- **Props:** `issues: { label: string; deltaPct: number; count: number }[]`.
- **Data Source:** parent payload.
- **Dependencies:** `AIInsightWidget`-style rows.
- **States:** loaded / empty.
- **Child Components:** none.
- **Related API Endpoints:** none directly.
- **Status:** ⬜ — depends on "trending vs. most common" distinction being explicitly defined first (`WIREFRAMES.md` §8 open decision #4).

#### RepeatedProblemsCard
- **Purpose:** intended to surface repeated callers/issues.
- **Props:** none defined yet.
- **Data Source:** blocked — needs a caller-identity concept the schema doesn't have (`VOICE_AGENT_DESIGN.md` §8: duplicate-caller detection isn't built; matching is informal, by phone number, today).
- **Dependencies:** none.
- **States:** blocked-only — renders "Needs caller-identity design before this is more than a name."
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ⬜ (structurally blocked).

#### RiskAlertsCard
- **Purpose:** surfaces anomalies (e.g. "3 escalated calls in the last hour — above normal rate").
- **Props:** `alerts: { severity: "high" | "medium"; message: string }[]`.
- **Data Source:** parent payload — would require a defined "normal rate" baseline to compare against, itself an open product decision.
- **Dependencies:** none.
- **States:** loaded / empty ("No risk alerts").
- **Child Components:** none.
- **Related API Endpoints:** none directly.
- **Status:** ⬜.

#### RecommendationsCard
- **Purpose:** intended to surface "AI Recommendations."
- **Props:** none defined yet.
- **Data Source:** blocked — "recommendations to whom, about what" is undefined (`WIREFRAMES.md` §8 open decision #4).
- **Dependencies:** none.
- **States:** blocked-only.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ⬜ (structurally blocked).

#### InsightFeed
- **Purpose:** chronological log of insights as they're generated (if insights become a persisted, timestamped concept rather than a live-computed view).
- **Props:** none defined yet.
- **Data Source:** would require an `insights` table or equivalent — does not exist anywhere in `DATABASE_DESIGN.md`.
- **Dependencies:** none.
- **States:** blocked-only.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ⬜ (no stored concept at all — the most speculative component in this document).

---

## Settings Components

### 1. Component Tree
```
SettingsPage
 ├─ OpenAIStatusCard
 ├─ TwilioStatusCard
 ├─ DatabaseStatusCard
 ├─ EmailStatusCard
 ├─ EnvironmentStatusCard
 └─ ReadOnlyConfigPanel (wraps all of the above)
```

### 2. Data Flow
Single `GET /api/v1/settings/status` call returns masked-key + configured/not + last-verified per provider — one request, five display cards.

### 3. API Dependencies
`GET /api/v1/settings/status` ⬜ new. **No mutation endpoint exists or should exist for this page** (`DESIGN.md` §12) — there is deliberately no `PATCH`/`POST` counterpart until Phase 3 auth ships.

### 4. Loading States
Skeleton cards matching final layout.

### 5. Empty States
Not applicable — this page always has content (either configured or not-configured per provider); "not configured" is a state, not an empty state.

### 6. Error States
If the status endpoint fails, show a single page-level error — there's no partial value in showing 3 of 5 provider cards on a diagnostics page.

### 7. Responsive Behavior
Single-column card stack on mobile/tablet, 2-column grid on desktop — same non-interactive card pattern at every breakpoint.

---

#### SettingsPage
- **Purpose:** read-only visibility into provider configuration (`WIREFRAMES.md` §9). **Must never render an edit control or unmasked secret, under any circumstance, until Phase 3 auth ships** — this is a security property of the page, not a styling choice.
- **Props:** none.
- **Data Source:** `GET /api/v1/settings/status`.
- **Dependencies:** all status cards below, `ReadOnlyConfigPanel`.
- **States:** loading / loaded / error.
- **Child Components:** `OpenAIStatusCard`, `TwilioStatusCard`, `DatabaseStatusCard`, `EmailStatusCard`, `EnvironmentStatusCard`, `ReadOnlyConfigPanel`.
- **Related API Endpoints:** `GET /api/v1/settings/status` ⬜ new.
- **Status:** ⬜.

#### OpenAIStatusCard
- **Purpose:** OpenAI configuration status — masked key, model name, last-verified.
- **Props:** `configured: boolean`, `maskedKey?: string`, `model?: string`, `lastVerified?: string`.
- **Data Source:** parent payload.
- **Dependencies:** `StatusIndicator`, `MaskedSecret` (design token component, `DESIGN_SYSTEM.md` §9's status card pattern).
- **States:** configured / not-configured.
- **Child Components:** `StatusIndicator`.
- **Related API Endpoints:** none directly.
- **Status:** ⬜.

#### TwilioStatusCard
- **Purpose:** Twilio configuration status — masked auth token, phone number.
- **Props:** `configured: boolean`, `maskedToken?: string`, `phoneNumber?: string`.
- **Data Source:** parent payload.
- **Dependencies:** `StatusIndicator`, `MaskedSecret`.
- **States:** configured / not-configured.
- **Child Components:** `StatusIndicator`.
- **Related API Endpoints:** none directly.
- **Status:** ⬜.

#### DatabaseStatusCard
- **Purpose:** DB connectivity status.
- **Props:** `connected: boolean`, `version?: string`.
- **Data Source:** parent payload.
- **Dependencies:** `StatusIndicator`.
- **States:** connected / disconnected.
- **Child Components:** `StatusIndicator`.
- **Related API Endpoints:** none directly (underlying check is `GET /api/v1/health/ready` ✅, which the new settings endpoint should reuse rather than duplicate).
- **Status:** 🔶 — the readiness check itself exists (`API_SPEC.md` §9); the settings-page presentation of it does not.

#### EmailStatusCard
- **Purpose:** SendGrid configuration status — masked key, from-address.
- **Props:** `configured: boolean`, `maskedKey?: string`, `fromAddress?: string`.
- **Data Source:** parent payload.
- **Dependencies:** `StatusIndicator`, `MaskedSecret`.
- **States:** configured / not-configured.
- **Child Components:** `StatusIndicator`.
- **Related API Endpoints:** none directly.
- **Status:** ⬜.

#### EnvironmentStatusCard
- **Purpose:** which environment this is, and key feature flags (e.g. `ENABLE_AI_SUMMARY`).
- **Props:** `environment: string`, `flags: Record<string, boolean>`.
- **Data Source:** parent payload.
- **Dependencies:** none.
- **States:** static.
- **Child Components:** none.
- **Related API Endpoints:** none directly.
- **Status:** ⬜.

#### ReadOnlyConfigPanel
- **Purpose:** the structural wrapper enforcing the read-only contract — a deliberate architectural guard, not just a layout component. Its component tree contains **no** `EditableField`, `SaveButton`, or form-submission logic anywhere beneath it; absence, not disablement, is the safety property (`WIREFRAMES.md` §9).
- **Props:** `children: ReactNode`.
- **Data Source:** none.
- **Dependencies:** none.
- **States:** n/a.
- **Child Components:** the five status cards above, and nothing else.
- **Related API Endpoints:** none — and this component should be the place a future code reviewer checks first if a write control ever gets added here by mistake.
- **Status:** ⬜ (wrapper for an otherwise-unbuilt page).

---

## Common Components

These are shared, page-agnostic primitives used throughout the sections above. Each is design-token-driven (`DESIGN_SYSTEM.md`) and carries no page-specific logic.

#### Badge
- **Purpose:** base pill primitive underlying `StatusBadge`, `PriorityBadge`, `SourceBadge`, `CallStateBadge`.
- **Props:** `label: string`, `color: SemanticColor`, `icon?: LucideIcon`, `size?: "sm" | "md"`.
- **Data Source:** props only.
- **Dependencies:** none.
- **States:** static (badges don't have interactive states themselves — interactivity, e.g. `StatusBadge`'s dropdown, wraps a `Badge` rather than the `Badge` handling it).
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### Button
- **Purpose:** base action primitive (`DESIGN_SYSTEM.md` §10).
- **Props:** `variant: "primary" | "secondary" | "danger" | "ghost" | "icon"`, `loading?: boolean`, `disabled?: boolean`, `icon?: LucideIcon`, `onClick`.
- **Data Source:** none.
- **Dependencies:** none.
- **States:** default / hover / loading / disabled.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### Input
- **Purpose:** base text field primitive (`DESIGN_SYSTEM.md` §11).
- **Props:** `value`, `onChange`, `placeholder?`, `error?: string`, `type?`.
- **Data Source:** none.
- **Dependencies:** none.
- **States:** default / focus / error / disabled.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### Textarea
- **Purpose:** multi-line text primitive, auto-growing to a cap (`DESIGN_SYSTEM.md` §11).
- **Props:** same shape as `Input` plus `maxHeight?`.
- **Data Source:** none.
- **Dependencies:** none.
- **States:** default / focus / error / disabled.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### Select
- **Purpose:** dropdown/multi-select primitive underlying `TicketFilters`.
- **Props:** `options: { label; value }[]`, `value`, `onChange`, `multiple?: boolean`.
- **Data Source:** options passed by parent.
- **Dependencies:** shadcn/ui `Select` primitive.
- **States:** default / open / selected / disabled.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### SearchBar
- **Purpose:** base search input with leading icon + clear button, underlying `TicketSearch`.
- **Props:** `value`, `onChange`, `placeholder?`.
- **Data Source:** none.
- **Dependencies:** none.
- **States:** empty / typing / active.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### DataTable
- **Purpose:** generic sortable/paginated table shell underlying `TicketTable` and `CallTable` — shared so both tables inherit identical density, hover, and sort behavior rather than diverging over time.
- **Props:** `columns: ColumnDef[]`, `rows: T[]`, `sort`, `onSortChange`, `onRowClick`, `isLoading`, `emptyState: ReactNode`.
- **Data Source:** rows passed by parent.
- **Dependencies:** `EmptyState`, `LoadingState`.
- **States:** loading / loaded / empty.
- **Child Components:** row renderer (parent-supplied via `columns`).
- **Related API Endpoints:** none directly.
- **Status:** ✅.

#### Card
- **Purpose:** base surface primitive underlying every card variant in `DESIGN_SYSTEM.md` §12.
- **Props:** `children`, `padding?: "sm" | "md"`, `clickable?: boolean`.
- **Data Source:** none.
- **Dependencies:** none.
- **States:** default / hover (if `clickable`).
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### Modal
- **Purpose:** base overlay primitive underlying Confirmation/Delete/Escalation/Error modals (`DESIGN_SYSTEM.md` §15).
- **Props:** `open: boolean`, `onClose`, `variant: "confirmation" | "delete" | "escalation" | "error"`, `children`.
- **Data Source:** none.
- **Dependencies:** Framer Motion (open/close transition), focus-trap utility.
- **States:** closed / open.
- **Child Components:** none (content supplied by caller).
- **Related API Endpoints:** none.
- **Status:** ✅.

#### Drawer
- **Purpose:** base slide-over primitive underlying `TicketDrawer`, `CallDrawer`, `DetailsDrawer` (`DESIGN_SYSTEM.md` §14).
- **Props:** `open: boolean`, `onClose`, `width?: number` (default 480), `children`.
- **Data Source:** none.
- **Dependencies:** Framer Motion, focus-trap utility.
- **States:** closed / open / (full-screen on mobile, per responsive rule).
- **Child Components:** none (content supplied by caller).
- **Related API Endpoints:** none.
- **Status:** ✅.

#### LoadingState
- **Purpose:** shared skeleton/shimmer primitive.
- **Props:** `variant: "table-rows" | "card" | "chart" | "page"`, `count?: number`.
- **Data Source:** none.
- **Dependencies:** none.
- **States:** n/a (it *is* a loading state).
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### EmptyState
- **Purpose:** shared empty-state primitive (`WIREFRAMES.md` §11 pattern: icon + sentence + optional action).
- **Props:** `icon: LucideIcon`, `title: string`, `description?: string`, `action?: { label; onClick }`.
- **Data Source:** none.
- **Dependencies:** none.
- **States:** n/a.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### ErrorState
- **Purpose:** shared error-state primitive (`WIREFRAMES.md` §12 pattern).
- **Props:** `severity: "full-outage" | "degraded"`, `title`, `description`, `retry?: () => void`.
- **Data Source:** none.
- **Dependencies:** none.
- **States:** n/a.
- **Child Components:** none.
- **Related API Endpoints:** none.
- **Status:** ✅.

#### StatusIndicator
- **Purpose:** the dot+label primitive underlying `StatusBar`, `SystemHealthPanel`, all Settings status cards (`DESIGN_SYSTEM.md` §9.1).
- **Props:** `status: "operational" | "degraded" | "down" | "unknown"`, `label: string`, `lastChecked?: string`.
- **Data Source:** props.
- **Dependencies:** none.
- **States:** four fixed visual states per `status`.
- **Child Components:** none.
- **Related API Endpoints:** none directly.
- **Status:** ✅ component / ⬜ backing data for most current uses (dependency-health endpoint doesn't exist yet).

---

## Implementation Section — Frontend Build Order

Sequencing rationale mirrors `DESIGN.md` §18, expressed as concrete component-build phases so a team can pick up a phase and know exactly what's in scope.

### Phase 1 — Foundation
**Scope:** Tailwind config + shadcn/ui install + theme tokens (`DESIGN_SYSTEM.md` §§2–9) wired as CSS variables; Inter font load; all **Common Components** (`Badge`, `Button`, `Input`, `Textarea`, `Select`, `SearchBar`, `Card`, `Modal`, `Drawer`, `LoadingState`, `EmptyState`, `ErrorState`, `StatusIndicator`).
**Rationale:** every later phase composes these primitives. Building a page before this phase means rebuilding it once the design system lands — `DESIGN.md` §15 is explicit that adopting shadcn/Tailwind/Recharts/Framer Motion piecemeal per page leaves the product visually incoherent mid-migration. This phase produces zero user-visible pages but de-risks every phase after it.

### Phase 2 — Layout
**Scope:** `AppShell`, `Sidebar`, `Header`, `NotificationCenter`, `PageContainer`. `StatusBar` built as a shell against mock/stubbed data (real endpoint likely not ready yet).
**Rationale:** applied to the *existing* three pages first (list/detail/new-ticket) per `DESIGN.md` §18 step 1 — proves the shell works under real page content before any new page is built on top of it.

### Phase 3 — Tickets
**Scope:** `TicketsPage`, `TicketFilters`, `TicketSearch`, `TicketTable`, `TicketRow`, `StatusBadge`, `PriorityBadge`, `SourceBadge`, `Pagination`, `TicketDrawer`, `CallerInfoPanel`, `AISummaryPanel`. `TicketTimeline` built as a shell rendering its blocked state (Phase 3 backend, not this frontend phase).
**Rationale:** highest-value, closest to what exists today (`DESIGN.md` §18 step 2) — extends a working backend rather than waiting on new endpoints, and resolves the single biggest interaction change (page → drawer) early, before it's copied into Calls.

### Phase 4 — Dashboard
**Scope:** `DashboardPage`, `SystemHealthPanel`, `KPIGrid`, `KPICard`, `RecentTicketsPanel`, `ActivityFeed` (ticket-only events until Calls exists). `RecentCallsPanel` and `AIInsightsPreview` built as shells (blocked/unavailable states) since their backends land in later phases.
**Rationale:** `DESIGN.md` §18 sequences Dashboard after Tickets+Calls because the KPI cards mostly compose from those endpoints — but per that same section, only Tickets is done at this point, so Dashboard ships with real ticket KPIs and honest placeholders for call/AI metrics rather than waiting for every dependency to be ready.

### Phase 5 — Calls
**Scope:** new `GET /api/v1/voice-calls` + `/summary` + `/{id}` endpoints (backend), then `CallsPage`, `CallStatsPanel`, `CallTable`, `CallRow`, `CallDrawer`, `TranscriptViewer`. `CallTimeline` stays in its blocked state (no state-history storage exists). `RecentCallsPanel` and Dashboard's call KPIs get wired to real data as part of this phase.
**Rationale:** `voice_call_sessions` data has been ready since Phase 2 of the backend (`DESIGN.md` §18 step 3) — this phase is "write the missing API," not new frontend invention.

### Phase 6 — Analytics
**Scope:** `AnalyticsPage`, `MetricsGrid`, `CategoryChart`, `PriorityChart`, `SourceChart`, `CallsPerDayChart`, `EscalationChart`, plus the six backend aggregation endpoints.
**Rationale:** blocked until the "AI Resolution Rate" definition is resolved (`DESIGN.md` §6.2/§20, `WIREFRAMES.md` §14) — that decision gates both this phase and Phase 4's fifth KPI card, so resolving it should happen before or during this phase, not deferred further.

### Phase 7 — AI Insights
**Scope:** `AIInsightsPage` and all its cards, most of which render blocked states (`RepeatedProblemsCard`, `RecommendationsCard`, `InsightFeed`) until their underlying product/schema decisions land.
**Rationale:** built last per the page's own recommendation (`DESIGN.md` §11, `WIREFRAMES.md` §8) — every other page has a concrete data source by this point; this one still needs product decisions, not just engineering time.

### Phase 8 — Settings
**Scope:** `SettingsPage`, all five status cards, `ReadOnlyConfigPanel`, and the `GET /api/v1/settings/status` endpoint.
**Rationale:** sequenced late despite being simple, because it has zero urgency and zero auth dependency for its read-only form (`DESIGN.md` §18 step 5) — nothing else in the product blocks on it, so it fills a low-risk gap late in the build rather than competing for early attention with higher-value pages. Its write path is explicitly out of this entire build order, gated on Phase 3 backend authentication landing separately.

### Phase 9 — Polish
**Scope:** Framer Motion pass across all pages (`DESIGN_SYSTEM.md` §19), full accessibility audit against the §20 checklist, responsive verification at all three breakpoints for every page, empty/error/loading state review end-to-end, dark/light theme parity check (light mode is the deliberate second pass per `DESIGN.md` §2.4/§14).
**Rationale:** motion and accessibility are cross-cutting concerns that are cheapest to verify once, across a finished set of pages, rather than re-checked piecemeal after every phase — but they are not optional or "nice to have last": this phase is a gate before the product is considered done, not a buffer to cut if the schedule slips.

## Changelog

- **1.0** (2026-09-20) — Initial version, mapped against `WIREFRAMES.md` v1.0 and `DESIGN_SYSTEM.md` v1.0.

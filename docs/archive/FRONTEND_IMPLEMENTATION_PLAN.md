# HFMG AI Help Desk — Frontend Implementation Plan

**Version:** 1.0
**Status:** ✅ All eight phases complete (final pre-production review pass). This document is now a historical build plan — every phase below shipped substantially as sequenced, several with more scope than originally planned because backend work (Tiers 2–4) landed ahead of the frontend phases that consume it. For current, verified status, see `FRONTEND_WORK_LOG.md` (what actually shipped, phase by phase) and `FINAL_PROJECT_STATUS.md`. This plan's per-phase "API Endpoints" tables (marking things ⬜/🔶) describe the state *before* implementation and are left unedited below as an accurate record of what was known at planning time — do not treat them as current.
**Audience:** Frontend engineers, engineering lead sequencing the build (historical reference — the build is done)

This document sequences the frontend build into eight shippable phases. It does not re-specify components — `COMPONENTS.md` is the contract for every component named here (props, states, data source). This document answers a different question per phase: **what ships, against which endpoints, and how do we know it's done.**

No code. No component API redesign. Where a phase's endpoints don't exist yet, the phase still ships — against an honest blocked/pending state, per the discipline established in `WIREFRAMES.md` and `COMPONENTS.md` — rather than waiting for the backend to catch up.

**Legend** (inherited from `WIREFRAMES.md` / `COMPONENTS.md`, unchanged): ✅ Built · 🔶 Partial · ⬜ Vision.

---

## Phase 1 — App Shell

**Goal:** the structural skeleton and design-token foundation every later phase renders inside. Nothing user-facing about ticket/call/analytics data ships in this phase — this phase is infrastructure.

### Components
| Component | Notes |
|---|---|
| `AppShell` | Theme provider (dark default, per `DESIGN_SYSTEM.md` §1/§2.6), TanStack Query client, React Router outlet |
| `Header` | 64px fixed, env badge, mounts `StatusBar` + `NotificationCenter` |
| `StatusBar` | Four-way OpenAI/Twilio/DB/Email indicator — built against real data if `GET /api/v1/health/dependencies` exists, otherwise against a stubbed "unknown" state (never fake "operational") |
| `NotificationCenter` | Toast stack — fully functional immediately, since it's client-side only |
| `PageContainer` | Max-width 1440px, side padding, section spacing per `DESIGN_SYSTEM.md` §5 |
| Common primitives | `Badge`, `Button`, `Input`, `Textarea`, `Select`, `SearchBar`, `Card`, `Modal`, `Drawer`, `DataTable`, `LoadingState`, `EmptyState`, `ErrorState`, `StatusIndicator`, `Pagination` |
| Tailwind config + shadcn/ui theme tokens | Wires every value in `DESIGN_SYSTEM.md` §§2–9 as CSS variables; Inter font loaded |

### API Endpoints
| Endpoint | Status | Used by |
|---|---|---|
| `GET /api/v1/health` | ✅ exists | Smoke-test only — confirms the shell can reach the backend at all |
| `GET /api/v1/health/dependencies` | ⬜ new | `StatusBar` — if not built yet, `StatusBar` ships rendering all four indicators as "unknown" (muted, per `DESIGN_SYSTEM.md` §2.5), not hidden and not faked |

### Acceptance Criteria
- [ ] Dark theme renders by default; every color in `Header`/`PageContainer`/common primitives matches a `DESIGN_SYSTEM.md` §2 token — no ad hoc hex values in component code.
- [ ] `StatusBar` renders four indicators at all times; each shows "unknown" (not a false "operational") when its endpoint is unavailable.
- [ ] `NotificationCenter` can display and auto-dismiss a success/warning/info toast and requires manual dismissal for an error toast (verified with a manually triggered test toast, since no real mutation exists yet).
- [ ] Every common primitive (`Button`, `Input`, `Card`, `Modal`, `Drawer`, `Badge`, `DataTable`, `LoadingState`, `EmptyState`, `ErrorState`, `StatusIndicator`, `Pagination`) is implemented and visually reviewable in isolation (e.g. a component playground/storybook route), each in its documented states from `COMPONENTS.md`.
- [ ] `PageContainer` content caps at 1440px and never stretches full-bleed on an ultra-wide monitor.
- [ ] `prefers-reduced-motion` is respected by every primitive that has a transition (`Modal`, `Drawer`, `NotificationCenter`).
- [ ] No page routes exist yet beyond a placeholder — this phase has no navigable pages, only the shell they'll mount into.

---

## Phase 2 — Navigation

**Goal:** the six-route navigation structure and the routing shell that lets every later phase "just add a page" without re-touching layout.

### Components
| Component | Notes |
|---|---|
| `Sidebar` | Fixed 240px desktop, icon-rail or hamburger-overlay on tablet (pick one, per `DESIGN_SYSTEM.md` §5.2), full-screen overlay on mobile |
| `NavItem` | Active-state indicator by color **and** a leading marker, never color alone (`DESIGN.md` §5) |
| Route definitions | `/`, `/tickets`, `/calls`, `/calls/live/:callId`, `/analytics`, `/ai-insights`, `/settings` — all six top-level routes registered, five of them initially rendering a placeholder page body until their own phase lands |
| Skip-to-content link | Precedes `Sidebar` in tab order (`DESIGN_SYSTEM.md` §20.2) |

### API Endpoints
None. This phase is pure routing/navigation state — no data dependency.

### Acceptance Criteria
- [ ] All six nav items are present, in the fixed order from `DESIGN.md` §4, with no nested/grouped menus.
- [ ] Clicking a nav item routes to its page and updates the active-state indicator (color + marker) correctly; browser back/forward navigates between them correctly.
- [ ] Deep-linking directly to any of the six routes (e.g. pasting `/analytics` into the address bar) renders that route's shell, not a redirect to `/`.
- [ ] Sidebar collapses to icon-rail (or hamburger overlay) at the tablet breakpoint and to a full-screen overlay at the mobile breakpoint, per `DESIGN_SYSTEM.md` §5.2/§5.3.
- [ ] Every nav item is reachable and activatable via keyboard alone (Tab + Enter), and the skip-to-content link is the first stop in tab order.
- [ ] Placeholder pages for Dashboard/Calls/Analytics/AI Insights/Settings render an explicit "not yet built" state, not a blank white screen or a 404 — this is the same honesty discipline as every other blocked state in this project.

---

## Phase 3 — Tickets

**Goal:** ship the most-built page in the product as a real, usable upgrade over `TicketDetailPage.tsx`/`TicketListPage.tsx` — search, filters, and the drawer conversion.

### Components
| Component | Notes |
|---|---|
| `TicketsPage` | Owns filter/search/pagination as URL state |
| `TicketFilters` | Status ✅, Priority/Category/Source 🔶 (params not built yet — ship the UI, disable/hide the filter chip until its param lands, per endpoint table below) |
| `TicketSearch` | Ships disabled with a "search coming soon" affordance if `q` param isn't live yet — never a search box that silently does nothing |
| `TicketTable`, `TicketRow`, `StatusBadge`, `PriorityBadge`, `SourceBadge`, `Pagination` | Full table per `DESIGN_SYSTEM.md` §13 |
| `TicketDrawer` | Slide-over replacing the current full-page detail view — the single biggest UX change in this phase |
| `CallerInfoPanel`, `AISummaryPanel` | Full functionality, data already exists |
| `TicketTimeline` | Ships rendering its blocked state ("Activity history requires Phase 3 backend auth/audit_log") — not hidden, not fabricated |

### API Endpoints
| Endpoint | Status | Notes |
|---|---|---|
| `GET /api/v1/tickets?status=` | ✅ exists | List + status filter |
| `GET /api/v1/tickets?q=` | 🔶 needs new param | Ship `TicketSearch` disabled until this lands, or coordinate with backend to land it in this phase (recommended — `DESIGN.md` §7 notes the GIN index already exists, unused) |
| `GET /api/v1/tickets?priority=&category_id=&source=` | 🔶 needs new params | Same treatment as search |
| `GET /api/v1/tickets/{id}` | ✅ exists | Drawer detail fetch |
| `POST /api/v1/tickets/{id}/status` | ✅ exists | Status Controls in drawer |
| `POST /api/v1/tickets/{id}/regenerate-summary` | ✅ documented | `AISummaryPanel` retry action — verify built against the running backend before wiring |
| `GET /api/v1/categories` | ✅ exists | Populates the Category filter's option list |

### Acceptance Criteria
- [ ] Ticket list loads, paginates, and sorts correctly against real data; Status filter narrows results correctly.
- [ ] Clicking a ticket row opens `TicketDrawer` as a slide-over **without a route navigation** — the underlying list scroll position and applied filters are preserved when the drawer closes.
- [ ] `TicketDrawer` renders Caller Information, AI Summary (correctly reflecting `PENDING`/`COMPLETED`/`FAILED`/`DISABLED`), and Status Controls, and a status change via the drawer updates the row in the underlying table without a full page refetch.
- [ ] "Regenerate summary" triggers the retrigger endpoint and the panel transitions to `PENDING` state immediately, then to `COMPLETED`/`FAILED` on the next poll/refetch.
- [ ] `TicketTimeline` renders its explicit blocked message — it does not render an empty list (which would imply "no history exists" rather than "history isn't tracked yet").
- [ ] If Search/Priority/Category/Source filter params are not live by this phase's ship date, their controls are visibly disabled with an explanatory tooltip, not silently non-functional.
- [ ] On tablet, the table becomes horizontally scrollable (not column-reflowed); on mobile, rows render as `TicketCard`s and the drawer becomes full-screen.
- [ ] Empty states are distinct for "no tickets exist" vs. "no tickets match the current filter," per `COMPONENTS.md`'s Tickets §5.

---

## Phase 4 — Dashboard

**Goal:** the executive-overview screen — ship it with real ticket data and honest placeholders for everything else, rather than waiting for every dependency (Calls, Analytics, AI Insights) to exist first.

### Components
| Component | Notes |
|---|---|
| `DashboardPage`, `SystemHealthPanel`, `KPIGrid`, `KPICard` ×5, `RecentTicketsPanel`, `ActivityFeed` | Built for real |
| `RecentCallsPanel` | Ships rendering its "not yet available" state until Phase 5 lands the calls endpoint |
| `AIInsightsPreview` | Ships rendering its "AI Insights coming soon" state until Phase 7 lands |

### API Endpoints
| Endpoint | Status | Notes |
|---|---|---|
| `GET /api/v1/health/dependencies` | ⬜ new | `SystemHealthPanel` — same endpoint as Phase 1's `StatusBar`, reused, not duplicated |
| `GET /api/v1/analytics/kpis` | ⬜ new | Ideal source for all 5 KPI cards; if not ready, wire Open Tickets/Tickets Today directly from `GET /api/v1/tickets` (✅) as an interim measure and leave Calls Today/Escalations/AI Resolution Rate in their pending state |
| `GET /api/v1/tickets?page=1&page_size=10&sort=-created_at` | ✅ exists | `RecentTicketsPanel` |
| `GET /api/v1/analytics/recent-activity` | ⬜ new | `ActivityFeed` — if not ready, feed ships showing ticket-only events sourced from the tickets list, clearly not claiming to include call events |

### Acceptance Criteria
- [ ] `SystemHealthPanel` matches `StatusBar`'s live state exactly (same query, no drift between Header and Dashboard).
- [ ] Open Tickets and Tickets Today KPI cards show real, correct counts.
- [ ] Calls Today, Escalations, and AI Resolution Rate KPI cards render their `pending-definition` or `loading` state honestly if their endpoints/definitions aren't ready — **never a fabricated or zero-by-default number that could be mistaken for real data.**
- [ ] `RecentTicketsPanel` shows the 10 most recent tickets, links correctly into `TicketsPage` with the ticket pre-selected (drawer opens).
- [ ] `RecentCallsPanel` and `AIInsightsPreview` each render their explicit unavailable/coming-soon state — not an empty state, since "empty" would incorrectly imply the feature works and there's simply no data.
- [ ] The whole page loads without one slow panel (e.g. an unfinished `analytics/kpis` call) blocking the others — each panel fetches and renders independently.
- [ ] Page is legible — health, ticket volume, and any urgent signal — within 10 seconds of load, per `DESIGN.md` §2.2, verified by a walkthrough with someone unfamiliar with the page.

---

## Phase 5 — Calls

**Goal:** stand up the Voice Operations Center — this phase pairs directly with new backend work (`voice_call_sessions` already has the data; no API exposes it yet).

### Components
| Component | Notes |
|---|---|
| `CallsPage`, `CallStatsPanel`, `CallTable`, `CallRow`, `CallStateBadge`, `EscalationReasonBadge` | Voice Ops Center core |
| `CallDrawer`, `TranscriptViewer` | Call detail + transcript rendering (`turns` JSONB) |
| `AgentStatusCard` | AI orchestrator health, explicitly **not** human staffing — no such concept exists in the schema |
| `CallTimeline` | Ships in its blocked state — no state-history storage exists (`voice_call_sessions` stores only current `state`) |
| *(Stretch, if capacity allows within this phase)* `LiveCallMonitor`, `CurrentCallerCard`, `CallStateProgress`, `CategoryPredictionCard`, `PriorityPredictionCard`, `ConfidenceMeter`, `TicketPreviewCard`, `DecisionLogPanel` | Polling-based live view — explicitly labeled "polling every Ns," never implying a live socket. `DecisionLogPanel` ships in its permanently-blocked state (model reasoning isn't stored). If this doesn't fit in Phase 5, it becomes Phase 5.5 rather than blocking Calls' ship date |

### API Endpoints
| Endpoint | Status | Notes |
|---|---|---|
| `GET /api/v1/voice-calls` (list, paginated, filterable) | ⬜ **new backend work required this phase** | Same pagination/filter shape as `GET /api/v1/tickets` |
| `GET /api/v1/voice-calls/{id}` (detail incl. `turns`) | ⬜ new | `CallDrawer` and, if built, `LiveCallMonitor` (polled) |
| `GET /api/v1/voice-calls/summary` | ⬜ new | `CallStatsPanel` counts |
| `GET /api/v1/health/dependencies` | ⬜ (Phase 1) | `AgentStatusCard`, scoped to voice |

### Acceptance Criteria
- [ ] `GET /api/v1/voice-calls` exists, is paginated identically to `GET /api/v1/tickets`, and supports filtering by `state`/`escalated`.
- [ ] `CallsPage` lists real calls with correct Caller, Duration, Issue, Priority, and derived Outcome (per `WIREFRAMES.md` §5's derivation rule: completed-with-ticket / escalated-caller-requested / escalated-misunderstood / abandoned).
- [ ] Clicking a call row opens `CallDrawer`, which renders the full `turns` transcript via `TranscriptViewer` in correct chronological order, correctly distinguishing agent vs. caller turns.
- [ ] Escalated calls visibly surface their `escalation_reason` (via `EscalationReasonBadge`) without needing to open the drawer.
- [ ] `CallTimeline` renders its blocked message, not an empty timeline.
- [ ] **No audio player, playback control, or recording-related UI exists anywhere on this page** — this is a hard constraint, not a preference (`TWILIO_ARCHITECTURE.md` §9).
- [ ] If `LiveCallMonitor` ships in this phase: it polls only while a call is in a non-terminal state, stops polling on completion, and its UI explicitly states "polling every Ns" rather than implying a persistent connection.
- [ ] "No calls yet" empty state renders correctly for a fresh/test environment with zero call sessions.

---

## Phase 6 — Analytics

**Goal:** trended metrics — blocked, appropriately, on one product decision that should be resolved at the start of this phase rather than mid-build.

### Components
| Component | Notes |
|---|---|
| `AnalyticsPage`, `MetricsGrid` | Page shell + summary stats |
| `CategoryChart`, `PriorityChart`, `SourceChart` | Bar/pie per `DESIGN_SYSTEM.md` §17 rules (pie only ≤4 categories) |
| `CallsPerDayChart` | Single-series line chart |
| `EscalationChart` | Single stat or sparkline |
| `DateRangePicker` (common) | Default last-30-days range, shared across all charts on this page |

### API Endpoints
| Endpoint | Status | Notes |
|---|---|---|
| `GET /api/v1/analytics/tickets-by-category` | ⬜ new | — |
| `GET /api/v1/analytics/tickets-by-priority` | ⬜ new | — |
| `GET /api/v1/analytics/tickets-by-source` | ⬜ new | — |
| `GET /api/v1/analytics/calls-by-day` | ⬜ new | — |
| `GET /api/v1/analytics/escalation-rate` | ⬜ new | Formalizes the ad hoc SQL already in `OPERATIONS_RUNBOOK.md` §3.3 |
| `GET /api/v1/analytics/ai-summary-usage` | ⬜ new | Buckets by `ai_summary_status` |

### Acceptance Criteria
- [ ] **Before any chart is built, the "AI Resolution Rate" definition is resolved and documented** (`DESIGN.md` §20, `WIREFRAMES.md` §14) — this phase does not start chart implementation against an undefined metric.
- [ ] All six aggregation endpoints return pre-aggregated series — verified by confirming no chart component fetches raw `tickets`/`voice_call_sessions` rows and aggregates client-side (`DESIGN.md` §10 hard rule).
- [ ] Each chart renders with a visible title and legend/labels sufficient to read the data without hovering (Executive Friendly principle extended to this page).
- [ ] Changing the date range updates all charts on the page consistently.
- [ ] Each chart has an independent loading/error state — one failed endpoint doesn't blank the whole page.
- [ ] If the six endpoints aren't all ready by this phase's ship date, the whole page renders "Analytics is not yet available" rather than a mix of populated and broken charts.
- [ ] Chart colors are drawn from the categorical palette defined in `DESIGN_SYSTEM.md` §17 — no chart introduces a one-off color.

---

## Phase 7 — AI Insights

**Goal:** the executive intelligence layer — built last, deliberately, because most of its content depends on product decisions this plan does not make.

### Components
| Component | Notes |
|---|---|
| `AIInsightsPage`, `TrendingIssuesCard` | Buildable once "trending vs. most common" is explicitly defined |
| `RepeatedProblemsCard` | Ships permanently blocked — no caller-identity concept exists in the schema (`VOICE_AGENT_DESIGN.md` §8) |
| `RiskAlertsCard` | Buildable once a "normal rate" baseline for anomaly detection is defined |
| `RecommendationsCard` | Ships permanently blocked — "recommend what, to whom" is undefined |
| `InsightFeed` | Ships permanently blocked — no persisted "insight" concept exists anywhere in the schema |

### API Endpoints
| Endpoint | Status | Notes |
|---|---|---|
| `GET /api/v1/ai-insights` | ⬜ new, **partially unspecifiable** | Trending Issues and Most Common Categories are aggregatable today from `tickets.category_id` + `created_at`; Repeated Problems and Recommendations cannot be specified as an endpoint until their product definitions exist |

### Acceptance Criteria
- [ ] `TrendingIssuesCard` and a "Most Common Categories" view (if distinguished per the resolved definition) render real, correct aggregations once the endpoint ships.
- [ ] `RepeatedProblemsCard` and `RecommendationsCard` render their explicit blocked-state copy ("needs caller-identity design" / "recommendation logic not yet defined") — **this plan does not ask engineering to invent a definition to unblock these; that is a product decision to be made separately, and shipping a fabricated version would misrepresent what the system knows.**
- [ ] `RiskAlertsCard` either renders real alerts against a documented baseline or its own honest "not yet available" state — never a hardcoded/sample alert.
- [ ] Page loads as a single request (`GET /api/v1/ai-insights`) — no five separate endpoint calls for five cards.
- [ ] If the endpoint doesn't exist at all when this phase is reached, the entire page ships with a single "AI Insights is not yet available" state, and this is treated as an acceptable, expected outcome for this phase — not a blocked release.

---

## Phase 8 — Settings

**Goal:** read-only configuration visibility. Simple, low-urgency, and carries one hard constraint that overrides normal "ship fast" instincts: **no write path, ever, until Phase 3 backend authentication lands.**

### Components
| Component | Notes |
|---|---|
| `SettingsPage`, `ReadOnlyConfigPanel` | `ReadOnlyConfigPanel`'s component subtree must contain zero editable-field or save-button components — this is checked as part of the acceptance criteria below, not assumed |
| `OpenAIStatusCard`, `TwilioStatusCard`, `DatabaseStatusCard`, `EmailStatusCard`, `EnvironmentStatusCard` | Status display only |

### API Endpoints
| Endpoint | Status | Notes |
|---|---|---|
| `GET /api/v1/settings/status` | ⬜ new | Returns masked-key + configured/not + last-verified per provider — must never return an unmasked secret value |
| `GET /api/v1/health/ready` | ✅ exists | Reused for `DatabaseStatusCard`'s connectivity check rather than duplicating the logic |

### Acceptance Criteria
- [ ] Every secret displayed anywhere on this page is masked (e.g. `sk-...a1b2`) — confirmed by inspecting the actual API response payload, not just the rendered UI, since a masked-in-UI-but-unmasked-in-payload value is still a live vulnerability.
- [ ] **Code review explicitly confirms no `PATCH`/`POST`/`PUT` request is wired to any control on this page**, and no component in `ReadOnlyConfigPanel`'s subtree accepts user input intended for persistence.
- [ ] All five provider cards (OpenAI, Twilio, Database, Email, Environment) render correctly for both a fully-configured and a not-configured provider state.
- [ ] `DatabaseStatusCard` reflects the real result of `GET /api/v1/health/ready`, not a separate, potentially-drifting check.
- [ ] Page renders a single page-level error state if `GET /api/v1/settings/status` fails — not five independently-broken cards.
- [ ] This phase's completion does **not** trigger any follow-on work to add editing — that is explicitly out of scope until Phase 3 auth ships, and is tracked separately, not as a "Phase 8b."

---

## Cross-Phase Notes

- **Sequencing rationale**, briefly: Phases 1–2 are foundation nothing else can build on top of skipped; Phase 3 (Tickets) ships first among content pages because it's closest to a working backend today; Phase 4 (Dashboard) intentionally ships with partial data rather than waiting for Phases 5–7; Phases 5–6 pair with real, scoped new backend work; Phase 7 is last because it needs product decisions, not just engineering time; Phase 8 is low-urgency and has no dependencies on anything else, so its position in the sequence is flexible — it could move earlier without disrupting other phases if capacity allows.
- **No phase is blocked from shipping by another team's undone backend work.** Every phase's acceptance criteria include an explicit "if the endpoint isn't ready, render this honest state instead" clause — the frontend should never be the reason a phase slips, and the backend should never be pressured into shipping an underspecified endpoint (e.g. AI Insights, AI Resolution Rate) just to unblock a frontend deadline.
- **A motion/accessibility/responsive audit is not a separate ninth phase in this plan** — each phase's acceptance criteria already includes its own responsive and accessibility checks, so quality gates travel with the phase that introduces the surface, rather than being deferred to the end.

## Changelog

- **1.0** (2026-09-20) — Initial version, sequenced against `WIREFRAMES.md`, `DESIGN_SYSTEM.md`, and `COMPONENTS.md` v1.0.

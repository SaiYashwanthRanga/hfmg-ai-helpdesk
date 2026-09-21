# HFMG AI Help Desk — Product Design Specification

**Version:** 1.0
**Status:** Design Phase
**Owner:** HFMG
**Last updated:** 2026-09-20

---

## How to Use This Document

This is the **single source of truth for product and UI design** — navigation, page composition, visual language, and component choices. When frontend work disagrees with this document, one of them is wrong; fix the mismatch rather than letting them drift.

It is *not* the source of truth for backend architecture, data model, or API contracts — those remain `ARCHITECTURE.md`, `DATABASE_DESIGN.md`, and `API_SPEC.md`. Where a page here needs data, §3 states plainly whether that data exists today or is aspirational, so this document can be trusted without also re-deriving backend status from the code.

**Every section in this document is one of three things, and says which:**
- ✅ **Built** — matches what's running today
- 🔶 **Partial** — some backend support exists, UI doesn't (or vice versa)
- ⬜ **Vision** — described here so implementation has a target, not yet started

A vision document that doesn't admit what's missing stops being a source of truth the first time someone checks it against reality. §3 is the section that keeps this one honest.

---

## 1. Vision

HFMG AI Help Desk is not a traditional ticketing system. It is an **AI Operations Center** that combines:

- AI Voice Agent
- Ticket Management
- Call Monitoring
- Analytics
- AI Insights

The experience should feel closer to **Linear, the OpenAI Platform, Vercel, and Datadog** than to **ServiceNow or a generic admin dashboard.** Concretely, that means: information-dense but never cluttered, fast (no full-page reloads, optimistic UI where safe), typographically confident rather than boxed-in-cards-everywhere, and comfortable in dark mode as its native state rather than dark mode as an afterthought toggle.

## 2. Design Principles

### 2.1 AI First
The AI agent is the primary feature, not a bolted-on assistant. The UI should communicate, within the first screen, three things at a glance: **AI status** (is it working right now), **AI activity** (what has it done recently), and **AI insight** (what has it learned). A dashboard that leads with ticket counts and buries the AI in a corner widget has the emphasis backwards for this product.

### 2.2 Executive Friendly
A CEO should understand system health and value within 10 seconds of the Dashboard loading — no legend to read, no jargon to decode. This constrains the Dashboard specifically (§6): it earns the right to be dense elsewhere (Tickets, Analytics) only because the Dashboard stays legible at a glance.

### 2.3 Enterprise Ready
Must look professional enough for **healthcare, IT operations, and executive demos.** Concretely: no placeholder-looking empty states, no unstyled browser defaults leaking through, consistent spacing and type scale everywhere, and error states that read as "the system handled this gracefully" rather than "something broke."

### 2.4 Dark Mode First
Primary theme is **dark**. Light mode is optional and secondary — designed as a deliberate second pass on the dark palette (§14), not the default from which dark mode is derived. This is the opposite of how most admin tools are built, and it's a real engineering constraint worth stating plainly so nobody designs a component in light mode first and "adds dark mode later."

## 3. Current Implementation Status

**Updated during the final pre-production review pass** (see `FINAL_PROJECT_STATUS.md`, `docs/archive/DOCUMENTATION_AUDIT.md`). This table previously described a pre-implementation state — Frontend Phases 1–8 and Backend Tiers 0–5 have since shipped and are verified against running code, not just re-asserted from the original plan. Historical framing (the original "vision vs. reality" table as of the design phase) is preserved in git history if needed; this version describes the system as it actually runs today.

| Area | Status | Notes |
|---|---|---|
| **Navigation shell / sidebar** | ✅ Built | Six-route sidebar (Frontend Phase 2), responsive: expanded on desktop, icon-rail on tablet, full-screen overlay on mobile |
| **Dashboard** | ✅ Built | Real data throughout (Frontend Phase 4): System Health, 5 KPIs, Recent Tickets, Recent Calls, Recent Activity, AI Insights preview |
| **Tickets — list/search/filter** | ✅ Built | Status, Category, Priority, Source, and free-text search all filter `GET /api/v1/tickets` for real (Backend Tier 0; Priority/Source/Search enabled on the frontend during the final review pass — see `docs/archive/FRONTEND_GAP_REPORT.md`) |
| **Ticket Drawer** | 🔶 Partial | Slide-over, not a page (Frontend Phase 3). Transcript is still embedded in `description` for phone tickets rather than a separate field — see §8, unchanged and still an open decision (`REMAINING_PRODUCT_DECISIONS.md` doesn't cover it; it's a schema decision, not a business one) |
| **Calls page** | ✅ Built | Full Voice Operations Center (Frontend Phase 5) against real `GET /voice-calls`/`{id}`/`summary` endpoints (Backend Tier 2). `LiveCallMonitor` (`/calls/live/:id`) remains an unbuilt stretch item, by design |
| **Analytics page** | ✅ Built | Six real aggregation endpoints (Backend Tier 3) rendered as Recharts visualizations (Frontend Phase 6). "AI Resolution Rate" is correctly absent — no chart was built for a metric with no approved definition |
| **AI Insights page** | 🔶 Partial, by design | `GET /ai-insights` (Backend Tier 4) returns real Category Breakdown data; Trending Issues, Repeated Problems, High Risk Alerts, and Recommendations are explicit blocked fields, not fabricated content (Frontend Phase 7). See `REMAINING_PRODUCT_DECISIONS.md` |
| **Settings page** | 🔶 Built, read-only (correctly blocked from writing) | `GET /settings/status` (Backend Tier 1) + read-only UI (Frontend Phase 8), masked secrets, zero write-capable controls anywhere — verified by direct code grep in the final security review (`docs/reviews/SECURITY_REVIEW.md`). The write path remains correctly blocked on Phase 3 auth, exactly as this document originally specified |
| **System Status widget** | ✅ Built | `GET /health/dependencies` (Backend Tier 1) reports OpenAI/Twilio/Database/Email, each cached 30s server-side; rendered in both the Header's `StatusBar` and Dashboard's `SystemHealthPanel` |
| **Design system (colors, type, shadcn-style tokens, motion)** | ✅ Built | Tailwind v4 + CSS-variable tokens matching `docs/archive/DESIGN_SYSTEM.md` exactly, Lucide icons, Framer Motion for drawers/modals/toasts, Recharts for all charts |
| **Dark mode** | ✅ Built | Dark is the default theme (`ThemeProvider`), light mode implemented as the documented secondary pass |
| **Auth (referenced throughout Settings/RBAC)** | ⬜ Vision | Still correctly deferred to Phase 3 (`docs/archive/IMPLEMENTATION_PLAN.md`) — nothing in this engagement built auth, and Settings' write-path block depends on that remaining true |

**What changed since this table was last accurate:** every row above that now reads ✅/🔶 was previously ⬜. Nothing in §4 onward describes anything left to build from scratch — remaining work is either a product decision (`REMAINING_PRODUCT_DECISIONS.md`) or Phase 3 backend auth, not frontend or analytics engineering.

## 4. Navigation Structure

```
Dashboard
Tickets
Calls
Analytics
AI Insights
Settings
```

Six top-level items, flat — no nested menus. If a seventh item becomes necessary later, that's the signal to introduce grouping rather than growing the flat list; don't pre-build grouping for a list this short.

## 5. Layout

```
┌──────────────────────────────────────────────────────────┐
│  Header                                                    │
├───────────────┬────────────────────────────────────────────┤
│  Sidebar       │  Main Content                              │
│                │                                            │
│  ● Dashboard   │                                            │
│    Tickets     │                                            │
│    Calls       │                                            │
│    Analytics   │                                            │
│    AI Insights │                                            │
│    Settings    │                                            │
│                │                                            │
└───────────────┴────────────────────────────────────────────┘
```

- **Header:** product name/mark, environment indicator (dev/staging/prod — cheap to build, prevents the single most common "wait, which environment is this" mistake in an internal tool), and the System Status summary (§6.1) as a compact always-visible strip, not something buried a click away.
- **Sidebar:** fixed width, the six nav items from §4, active item indicated by both color and a leading marker (never color alone — see §14 accessibility).
- **Main content:** the only region that scrolls independently; header and sidebar stay fixed. Standard content max-width with generous side padding on large displays — resist the admin-dashboard reflex to stretch every table to full viewport width just because the space exists.

## 6. Dashboard

**Purpose:** executive overview. This is the page principle §2.2 is written for — everything on it must be legible without hovering, clicking, or reading a legend.

### 6.1 System Status
Four indicators: **OpenAI, Twilio, Database, Email Service.** Each shows a status (operational / degraded / down) and, on hover, when it was last checked.

**Data source, honestly:** no endpoint currently reports this. `GET /api/v1/health` is liveness-only and says nothing about the three external dependencies. Building this requires a small new endpoint that does what `OPERATIONS_RUNBOOK.md` §3.2's synthetic checks describe manually today — a lightweight reachability check per dependency, cached for a short interval so the dashboard doesn't trigger a live OpenAI/Twilio call on every page load.

### 6.2 KPI Cards
- Open Tickets
- Tickets Today
- Active Calls
- Escalations
- AI Resolution Rate

**Data source:** Open Tickets and Tickets Today are one query away from `tickets` today. Active Calls and Escalations are one query away from `voice_call_sessions` (state and `escalated`, respectively — `OPERATIONS_RUNBOOK.md` §3.3 already has the SQL). **AI Resolution Rate needs a definition before it needs an endpoint** — "tickets closed without human escalation," "tickets where the AI summary was used," and "voice calls that completed without escalation" are three different numbers this phrase could mean, and picking wrong makes the number meaningless to the exact executive audience §2.2 is written for. Resolve the definition in `ANALYTICS_SPEC` (or an addendum here) before building the query.

### 6.3 Recent Tickets / Recent Calls
Latest 10 of each, minimal columns (ticket number, caller, category/priority, time), linking into the Tickets and Calls pages respectively. Both are straightforward — the existing `GET /api/v1/tickets?page=1&page_size=10` covers the first; the second needs the new Calls endpoint from §9.

### 6.4 AI Insights (dashboard widget)
"Most common issues today" — a small preview of the full AI Insights page (§11). Not a separate feature; the dashboard widget and the full page should share one aggregation endpoint, with the widget requesting a smaller page size.

## 7. Tickets Page

**Purpose:** ticket management. This is the most-built page today — extending it is additive, not a rebuild.

**Search:** free-text across caller name, ticket number, and description. **Not built** — the current `GET /api/v1/tickets` has no `q` param (though `DATABASE_DESIGN.md` §3.1's full-text index already exists in the schema, unused).

**Filters:** Status (✅ built), Priority (⬜ needs a query param, trivial), Category (⬜ needs a query param, trivial), Source (⬜ needs a query param — `tickets.source` already exists on the model, added in Phase 2 for voice tickets).

**Columns:** Ticket Number, Caller, Category, Priority, Status, AI Summary, Source, Created Date. All are present on the existing `TicketRead`/`TicketListItem` schemas except a summary excerpt in the list view — today's `TicketListItem` deliberately omits `ai_summary` to keep list payloads light (`API_SPEC.md` §3); showing it in this table means either widening that schema or truncating server-side. Decide before building (§20 lists this as an open decision).

**Interaction:** clicking a row opens the **Ticket Drawer** (§8) as a slide-over, not a navigation to a new page. This is the single biggest interaction change from what exists today (`TicketDetailPage.tsx` is currently a full route) and the one most likely to actually be felt by users — get it right before polishing anything else on this page.

## 8. Ticket Drawer

Replaces `TicketDetailPage.tsx`'s full-page layout with a slide-over panel, so an agent triaging a list never loses their place in it.

- **Caller Information:** Name, Phone, Email — direct from `Ticket`.
- **Ticket Details:** Category, Priority, Status (with the status-change control that exists today).
- **Description:** the original complaint, verbatim.
- **AI Summary:** the generated summary, or the pending/disabled/failed state already designed in Phase 1 (`TicketDetailPage.tsx`'s existing `AI_SUMMARY_COPY` handling carries over unchanged).
- **Transcript** (voice tickets only): **this needs a decision, not just a UI change.** Today, the call transcript is *appended directly into `tickets.description`* by the voice orchestrator (`app/voice/orchestrator.py`, `_create_ticket`) — there is no separate `transcript` field. Rendering it as its own drawer section either means parsing it back out of `description` (fragile — it's plain text with a `--- Call transcript ---` marker) or adding a real column. The clean fix is a migration adding `tickets.transcript` (nullable, populated only for phone-sourced tickets) and having the orchestrator write to it directly instead of concatenating. Flagged as an open decision in §20 rather than decided here, since it's a schema change outside this document's authority.

## 9. Calls Page

**Purpose:** monitor AI voice calls. This is where §3's "data exists, page doesn't" gap is most concrete — `voice_call_sessions` already has everything this page needs.

**Cards:** Active Calls, Completed Calls, Escalated Calls — counts by `state`/`escalated`, same query family as the Dashboard KPIs (§6.2).

**Table:** Caller, Call Duration, Issue, Priority, Outcome.

| Column | Source |
|---|---|
| Caller | `voice_call_sessions.from_number`, or `collected.caller_name` once captured |
| Call Duration | **Not currently stored.** `ended_at` exists; call start time doesn't (only `created_at` on the row, which is close but not identical to when Twilio's call actually started). Cheap to add if precision matters; `created_at`→`ended_at` is good enough for a first version |
| Issue | `collected.short_issue` / `collected.description` |
| Priority | `collected.priority` |
| Outcome | Derive from `state` + `escalated` + `escalation_reason`: completed-with-ticket, escalated-caller-requested, escalated-misunderstood, abandoned |

**New backend work required:** a `GET /api/v1/voice-calls` (or similar) list endpoint with the same pagination/filter shape as `GET /api/v1/tickets`, plus a detail endpoint if this page needs a call-detail drawer showing the full transcript (likely, given the "Call Monitoring" framing in §1). Neither exists today. This is real, scoped backend work, not a frontend-only page.

**Explicitly excluded from this page, and why:** live audio monitoring and call playback appear in §19 as future enhancements, not here. `TWILIO_ARCHITECTURE.md` §9 made a deliberate decision to keep call recording **off** pending compliance sign-off; a "Call Playback" feature is impossible without reopening that decision (and adding consent language to the greeting). Don't let this page's design imply audio exists when it explicitly doesn't.


## 10. Analytics Page

**Purpose:** operational metrics, trended over time — the difference between this and the Dashboard is time-series depth versus at-a-glance status.

**Charts:** Tickets by Category, Tickets by Priority, Calls Per Day, AI Resolution Rate, Escalation Rate.

All five are aggregation queries against existing tables (`tickets`, `voice_call_sessions`) — no new tables needed. What's needed is: (1) one or more `/api/v1/analytics/*` endpoints returning pre-aggregated series rather than raw rows (never ship a chart that fetches all tickets and aggregates client-side — it will not survive real data volume), and (2) the same "AI Resolution Rate" definition question from §6.2, resolved once and reused by both pages.

**Chart library:** Recharts (§13). Pull the categorical and sequential color choices for these charts from the existing brand-neutral palette guidance already established in this project's dataviz conventions — don't invent a second, uncoordinated chart palette separate from §14's product colors.

## 11. AI Insights

**Purpose:** the AI intelligence layer — surfacing patterns a human triaging one ticket at a time wouldn't notice.

**Features:** Trending Issues, Most Common Problems, Repeated Callers, Category Breakdown, AI Recommendations.

This is the least-specified page in the original brief, deliberately left that way here too rather than inventing detail that hasn't been thought through: "Trending Issues" and "Most Common Problems" sound identical without a stated distinction (time-windowed spike vs. all-time frequency, presumably — say so explicitly before building). "Repeated Callers" needs a caller-identity concept the schema doesn't currently have (tickets are matched by phone number today, informally, per `VOICE_AGENT_DESIGN.md` §8's noted limitation that duplicate-caller detection isn't built). "AI Recommendations" needs a decision as basic as "recommendations to whom, about what" before it's a spec rather than a name.

**Future:** AI Copilot (see §19) — a conversational interface over this same data, once the underlying aggregations exist to converse about.

**Recommendation:** treat this page as the last one built, not the first, precisely because Dashboard/Tickets/Calls/Analytics all have a concrete data source today and this one doesn't yet.

## 12. Settings

**OpenAI Configuration, Twilio Configuration, Email Configuration, System Configuration.**

**This page cannot ship a write path yet, and that's not a design opinion — it's a direct consequence of a decision already made and documented.** `docs/archive/IMPLEMENTATION_PLAN.md`'s MVP Scope Decision and `DEPLOYMENT_GUIDE.md` §1 both state plainly: there is no authentication anywhere in this system today. A Settings page that can view or edit `OPENAI_API_KEY`, `TWILIO_AUTH_TOKEN`, or `SENDGRID_API_KEY` — the three secrets that authenticate this system to the outside world — with no login in front of it is not a rough edge to smooth over later; it is a live vulnerability the moment it exists, on a system explicitly documented as network-ACL-protected only (`DEPLOYMENT_GUIDE.md` §6).

**What can ship now:** a **read-only** version showing configuration *status* — which provider is configured, masked key (`sk-...a1b2`), model name, last-verified timestamp — with zero write capability. This satisfies the "System Configuration" visibility goal without the vulnerability.

**What must wait for Phase 3 auth:** any edit/save action on this page. Track this explicitly as a Phase 3 dependency, not a frontend TODO — it's a security gate, not a scheduling convenience.

## 13. Design System

### 13.1 Typography
**Inter**, throughout. One typeface, weight and size doing the differentiation work — resist introducing a second display face for headings; Inter's weight range (especially at 600–700 for headings against 400 for body) is sufficient for the hierarchy this product needs.

### 13.2 Border Radius
**16px** as the standard radius for cards, panels, and the ticket drawer. Smaller controls (buttons, inputs, badges) can use a smaller radius (e.g., 8px) for visual proportion — 16px on a small button reads as a pill, not a rounded rectangle; reserve the full 16px for surfaces large enough to carry it.

### 13.3 Shadows
Soft, modern — low-opacity, large-blur shadows rather than sharp drop shadows. On a dark background (§14), shadows do less visual work than on light; lean more on a subtle border or background-color step between elevation levels (card vs. page background) than on shadow alone, since shadows are far less visible on `#1F2937` over `#0B1120` than they'd be on a white background.

### 13.4 Animations
**Framer Motion**, subtle and professional — used for state transitions (drawer open/close, tab switches, KPI card value changes) and never for decoration. Rule of thumb consistent with §2.3's "enterprise ready" principle: if removing an animation wouldn't make the product feel broken, it's probably an animation worth cutting. Respect `prefers-reduced-motion`.

## 14. Color Palette

| Token | Value | Usage |
|---|---|---|
| Background | `#0B1120` | Page background, dark mode base |
| Sidebar | `#111827` | Sidebar surface — one step lighter than page background |
| Card | `#1F2937` | Cards, panels, the ticket drawer surface |
| Primary | `#3B82F6` | Primary actions, active nav indicator, links, focus rings |
| Success | `#22C55E` | Resolved/operational status, positive KPI deltas |
| Warning | `#F59E0B` | Medium/High priority, degraded status |
| Danger | `#EF4444` | Urgent/Critical priority, down status, destructive actions |
| Text | `#F8FAFC` | Primary text on dark surfaces |
| Muted | `#94A3B8` | Secondary text, timestamps, placeholder text |

**Accessibility note:** `Text` (#F8FAFC) on `Background` (#0B1120) and `Card` (#1F2937) both comfortably clear WCAG AA (>12:1). `Muted` (#94A3B8) on `Background` clears AA for normal text (~7:1) but tightens on `Card` — verify at implementation time rather than assuming, and never drop to `Muted`-on-`Muted` combinations. **Status color must never be the only signal** (§5's nav-active-marker rule applies system-wide): pair Success/Warning/Danger with a label or icon, not color alone — this matters doubly for a healthcare-adjacent tool where color-blind accessibility isn't optional polish.

**Light mode** (§2.4, secondary): derive from this palette's *relationships*, not by inverting values — a naive invert of `#0B1120`→`#F4EFDF`-ish produces a muddy, low-contrast light theme. Treat light mode as its own short design pass against these same semantic tokens (background/card/primary/status/text/muted) once dark mode is built and approved, not as a CSS filter.

## 15. Component Library

| Library | Role |
|---|---|
| **shadcn/ui** | Base component primitives (button, input, dialog, drawer, table, tabs) |
| **Lucide Icons** | Iconography, throughout |
| **Recharts** | All charts (Analytics page, §10) |
| **Tailwind CSS** | Styling layer underneath shadcn/ui |
| **Framer Motion** | Animation (§13.4) |

**None of these are installed today** (§3) — current `frontend/package.json` has React, React Router, and nothing else. Adopting this stack is a real dependency-and-setup task (Tailwind config, shadcn init, a theme provider wiring the tokens in §14 into CSS variables) that should happen once, up front, rather than piecemeal per page — building the Dashboard with inline styles and the Tickets page with shadcn would leave the product visually incoherent mid-migration.

## 16. Responsive Behavior

Not specified in the original brief; stated here because "single source of truth" means gaps get named, not silently left for whoever builds it to guess. Minimum target: the sidebar collapses to icon-only (or a hamburger-triggered overlay) below a standard tablet breakpoint; the Tickets/Calls tables become horizontally scrollable rather than attempting to reflow every column; the Ticket Drawer becomes full-screen rather than a partial slide-over on narrow viewports. This is an internal operations tool primarily used at a desk, not a mobile-first product — optimize for desktop/laptop first, degrade gracefully on tablet, and treat phone-width support as out of scope until requested (consistent with §19's "Mobile Application" being a distinct future item, not an assumption baked into this pass).

## 17. Inspiration

Linear · OpenAI Platform · Vercel · Datadog · GitHub · Stripe Dashboard

Used as reference points for density, type confidence, and restraint — not as a license to copy any one product's specific components wholesale. Where this spec is silent on a detail, default to "what would Linear do" over "what would a generic admin template do," but resolve genuine ambiguity by asking rather than guessing from a screenshot.

## 18. Rollout Sequencing (Non-Binding)

This is a design document, not `docs/archive/IMPLEMENTATION_PLAN.md` — but a vision this large is unhelpful without at least a proposed order, so implementation isn't left to guess where to start. Proposed, not decided:

1. **Design system foundation**: Tailwind + shadcn/ui + theme tokens (§14) + Inter + the shell layout (§5), applied to the *existing* three pages first — proves the system works before any new page is built on top of it.
2. **Tickets page + Ticket Drawer** (§7–8): highest-value, closest to what exists, and resolves the transcript-field decision (§8) that other work depends on.
3. **Calls page** (§9): needs one new list endpoint; `voice_call_sessions` data is otherwise ready.
4. **Dashboard** (§6): once Tickets and Calls endpoints exist, the KPI cards and recent-activity widgets mostly compose from them.
5. **Settings (read-only)** (§12): status display only, no write path, no auth dependency.
6. **Analytics** (§10): needs the AI Resolution Rate definition resolved first (§6.2, §20).
7. **AI Insights** (§11): last, per §11's own recommendation.
8. **Settings (write path)**: gated on Phase 3 authentication landing. Not sequenced relative to the above — sequenced relative to a different plan entirely.

## 19. Future Enhancements

Live Call Monitoring · Call Playback · AI Copilot · Knowledge Base · Real-Time Notifications · Role-Based Access Control · Multi-Tenant Support · Mobile Application

Two of these have documented prerequisites elsewhere in this repo, noted here so nobody scopes them as smaller than they are: **Call Playback** requires reopening the call-recording-off decision in `TWILIO_ARCHITECTURE.md` §9 (consent, compliance sign-off, greeting changes) — it is a compliance decision wearing a feature-request costume. **Role-Based Access Control** is not a future enhancement adjacent to this product; it is `docs/archive/IMPLEMENTATION_PLAN.md`'s Phase 3, and several sections of *this* document (§12 Settings, arguably §9/§10's data sensitivity) are blocked on it rather than merely complemented by it.

## 20. Open Decisions

Carried over from inline flags above, collected here for visibility:

1. **"AI Resolution Rate" definition** (§6.2, §10) — needs one authoritative definition before any query is written against it.
2. **Transcript storage** (§8) — add `tickets.transcript` as a real column (recommended) vs. parsing it out of `description` in the frontend (not recommended — fragile, and couples the UI to an internal string format).
3. **`ai_summary` in the ticket list response** (§7) — widen `TicketListItem` or truncate server-side; either is fine, but `API_SPEC.md` needs to change to match whichever is chosen.
4. **AI Insights' actual definitions** (§11) — "trending" vs. "most common," what a "recommendation" recommends, and to whom — needs a short answer before this page is anything but a name.
5. **Settings page write-path timing** (§12) — confirm the read-only-until-Phase-3-auth constraint is acceptable, since it means this page ships visibly incomplete relative to the original brief until auth lands.

## Changelog

- **1.0** (2026-09-20) — Initial version, adapted from the product brief and reconciled against current implementation status.

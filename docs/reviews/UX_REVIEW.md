# HFMG AI Help Desk — Frontend UX Review

**Scope:** All six screens (Dashboard, Tickets, Calls, Analytics, AI Insights, Settings), their supporting components, shared UI primitives, and the app shell/navigation.
**Method:** Full static read of every file in scope, cross-referenced against `DESIGN.md` (source of truth for intended behavior) and `KNOWN_LIMITATIONS.md` (what's already disclosed to stakeholders as unbuilt/blocked). Findings that duplicate an already-documented, deliberate gap are cited as such rather than re-reported as new problems.
**Note on Twilio/Calls:** Per instructions, the Calls page and Live Call Monitor are reviewed only for how well they communicate their blocked/unbuilt state — not for missing live-call functionality, which is out of scope and not recommended here.

Every finding below cites `file:line`. Every screen's write-up also states plainly what's already working well — this is not a complaints-only pass.

---

## 1. Dashboard

**Files:** `pages/DashboardPage.tsx`, `components/dashboard/{KPICard,KPIGrid,ActivityFeed,RecentCallsPanel,RecentTicketsPanel,SystemHealthPanel,AIInsightsPreview}.tsx`

### What's already good
Every panel (`KPIGrid`, `ActivityFeed`, `RecentCallsPanel`, `RecentTicketsPanel`, `AIInsightsPreview`) independently handles `isLoading` → `LoadingState`, `isError` → `ErrorState` with a working `retry`, and empty → `EmptyState` with a helpful description (`components/dashboard/RecentCallsPanel.tsx:24-29`, `components/dashboard/ActivityFeed.tsx:22-27`). This is the correct pattern and it's applied consistently. Cross-page navigation from the Dashboard's Recent Tickets/Calls rows (`RecentTicketsPanel.tsx:40`, `RecentCallsPanel.tsx:35`) correctly round-trips into `TicketsPage`'s and `CallsPage`'s own `?ticket=`/`?call=` URL-param drawer state (`pages/TicketsPage.tsx:39,78-84`, `pages/CallsPage.tsx:23,36-42`) — clicking a dashboard row opens the right drawer on the target page rather than dead-ending. KPI cards correctly render `—` with a `title` tooltip explaining *why* a metric (AI Resolution Rate) is blocked, rather than a fabricated number (`components/dashboard/KPICard.tsx:28-34`) — this matches `DESIGN.md` §6.2 and `KNOWN_LIMITATIONS.md`'s disclosed "AI Resolution Rate isn't shown" gap exactly.

### Confusing Workflows
None found. The dashboard is read-only/navigational; there's no multi-step flow to be ambiguous about.

### Missing Loading States
- `components/layout/StatusBar.tsx:11-15` and `components/dashboard/SystemHealthPanel.tsx:12-16` both destructure only `{ data }` from their `useQuery`, never `isLoading`. For roughly the first request round-trip on page load, all four dependency dots (OpenAI/Twilio/Database/Email) render as "Unknown" (`?? "unknown"` fallback) rather than a skeleton, before flipping to their real status. This is low severity — "Unknown" is itself a real, meaningful state per `api/health.ts:38-43` — but it's visually indistinguishable from a genuinely-unreachable health endpoint, so a user can't tell "still loading" from "backend can't tell us." Adding an `isLoading` branch (even just dimming the dots) would remove that ambiguity.

### Missing Empty States
None — all list-rendering panels have one (see above).

### Error Handling
Good throughout. Every panel's `ErrorState` uses `severity="degraded"` with a plain-language description and a working retry callback, consistent with `DESIGN.md` §2.3's "reads as the system handled this gracefully" principle.

### Accessibility
- `StatusIndicator` (`components/ui/StatusIndicator.tsx:36-46`) pairs every status dot with a text label ("Operational"/"Down"/etc.), satisfying `DESIGN.md` §14's "never color alone" rule.
- No labeling issues found on this page — it's non-interactive besides links, which use visible link text.

### Mobile
`KPIGrid` uses `grid-cols-2 sm:grid-cols-3 xl:grid-cols-5` (`components/dashboard/KPIGrid.tsx:21`) and the two-panel rows use `grid-cols-1 lg:grid-cols-2` (`pages/DashboardPage.tsx:21,26`) — both reflow cleanly to a single column on phone width rather than forcing horizontal scroll. No fixed-width elements found on this page.

---

## 2. Tickets

**Files:** `pages/TicketsPage.tsx`, `pages/NewTicketPage.tsx`, `pages/TicketDetailRedirect.tsx`, `components/tickets/*.tsx`

### What's already good
`TicketsPage` is the strongest-engineered screen in the app. Filters, search, page, and the open-drawer ticket are all URL state (`pages/TicketsPage.tsx:33-39`), so the view is shareable/refresh-safe and the drawer never causes a route change — exactly matching `DESIGN.md` §8's stated goal. Search is properly debounced (300ms, `pages/TicketsPage.tsx:44-51`) so typing doesn't fire a request per keystroke. The empty state is context-aware: "No tickets match these filters" with a **Clear filters** action when filters are active, vs. "No tickets yet" with a **+ New Ticket** action when the table is genuinely empty (`pages/TicketsPage.tsx:119-135`) — this is exactly the kind of state-aware empty-state design the brief asks reviewers to look for, done right. `TicketDrawer` handles loading/error/success independently of the underlying list (`components/tickets/TicketDrawer.tsx:31-43`), and `TicketStatusControl` only ever offers valid transitions by construction (`components/tickets/TicketStatusControl.tsx:32-33`, sourced from `VALID_STATUS_TRANSITIONS`), so a user can never pick a status change the backend would reject. `TicketTimeline` (`components/tickets/TicketTimeline.tsx:1-23`) correctly distinguishes "blocked forever" (no audit log exists) from "empty," per `NotYetAvailable`'s own doc comment — this matches `KNOWN_LIMITATIONS.md`'s disclosed lack of ticket history and should **not** be re-reported as a missing feature.

### Confusing Workflows — **the single biggest finding in this review**
`pages/NewTicketPage.tsx` is a completely different, unstyled application bolted onto the rest of the product:
- It uses raw `<input>`, `<select>`, `<textarea>`, `<button>` elements (`pages/NewTicketPage.tsx:72-135`) instead of the design-system `Input`/`Select`/`Textarea`/`Button` primitives every other screen uses.
- It's styled with inline `style={{...}}` objects (`pages/NewTicketPage.tsx:68,151,152,154`) instead of Tailwind, so it doesn't pick up dark mode, spacing scale, border radius (`DESIGN.md` §13.2), or type scale — it falls back to the raw HTML-element styling defined in `index.css:100-148`, whose own comment names it explicitly: *"Legacy shim — TicketListPage/TicketDetailPage/NewTicketPage predate the design system... Removed once Phase 3 (Tickets) rebuilds those pages."* Of those three, `TicketListPage`/`TicketDetailPage` have since been replaced by `TicketsPage`/`TicketDrawer` — but `NewTicketPage` is still live and routed at `/tickets/new` (`App.tsx:25`), reachable from the primary **+ New Ticket** button on the Tickets page (`pages/TicketsPage.tsx:90-92`) and from the empty-state action (`pages/TicketsPage.tsx:132`). A user going from a polished dark-mode ticket list into this page will see a jarring, unstyled, light-background form mid-task — this reads as "broken," directly contradicting `DESIGN.md` §2.3's enterprise-ready principle.
- The category dropdown fetch (`pages/NewTicketPage.tsx:26-35`) has no loading indicator at all — the `<select>` simply renders with zero options until the fetch resolves, and if it fails, the only feedback is a plain string ("Failed to load categories") with no retry.
- No Cancel/Back affordance exists on this page — a user who opens it accidentally must use the browser back button.

### Missing Loading States
- `pages/NewTicketPage.tsx:25-35` — categories load with no skeleton/spinner (see above).

### Missing Empty States
None beyond what's covered above — `TicketsPage`'s table/empty-state handling is comprehensive (see "What's already good").

### Error Handling
- `TicketsPage`, `TicketDrawer` both surface `ApiError.message` from the backend through `ErrorState` (`pages/TicketsPage.tsx:108-109`, `components/tickets/TicketDrawer.tsx:40`) — clear, non-technical, with retry.
- `NewTicketPage.tsx:131` renders `submitError` as a bare inline red `<p>` with no icon, no `ErrorState`/toast pattern, and no association via `aria-describedby` to the form — inconsistent with the `ErrorState` pattern used everywhere else, and with `TicketStatusControl.tsx:38-40`'s toast-based error handling for the same kind of failed-write scenario.

### Accessibility
- **Concrete bug:** `NewTicketPage.tsx`'s `Field` helper (`pages/NewTicketPage.tsx:141-157`) renders a `<label>` as a sibling of its `<input>`/`<select>`/`<textarea>`, not wrapping it, and never sets `htmlFor`/`id`. There is **no programmatic association** between any label and its form control on this page — a screen reader focusing the "Caller Name" input announces nothing about what it is. This is a real WCAG failure (1.3.1/4.1.2), and it's avoidable for free: `components/ui/Input.tsx:11-24`, `Select.tsx:22-35`, and `Textarea.tsx:11-24` already generate an `id` via `useId()` and wire `htmlFor` correctly — `NewTicketPage` simply doesn't use them.
- Everywhere else in Tickets, labeling is solid: `TicketFilters.tsx` uses `aria-label` on every `Select` (lines 30,38,46,54), `TicketSearch`/`SearchBar.tsx:31` labels the search input, and `Modal`/`Drawer`-pattern focus handling (via `TicketDrawer` → `Drawer`) is correct (see §7, Cross-Cutting).
- `TicketCard.tsx:12-23` (mobile row fallback) correctly implements `role="button"`, `tabIndex={0}`, and Enter/Space activation — good keyboard support for a `<div>`-based clickable card.

### Mobile
`TicketTable` swaps to `TicketCard` stack below `md` (`components/tickets/TicketTable.tsx:51,61-71`) rather than shrinking the 8-column table, avoiding the horizontal-scroll-table failure mode entirely. `NewTicketPage`, however, has a hardcoded `style={{ maxWidth: 560 }}` (`pages/NewTicketPage.tsx:68`) with no responsive adjustment — on a narrow viewport this is harmless (560px is just an upper bound, not a fixed width), but combined with the unstyled inputs above, the whole page is simply outside the responsive system the rest of the app is built on.

---

## 3. Calls (including Live Call Monitor)

**Files:** `pages/CallsPage.tsx`, `pages/LiveCallMonitor.tsx`, `components/calls/*.tsx`

### What's already good
`CallsPage` mirrors `TicketsPage`'s URL-state pattern exactly (`pages/CallsPage.tsx:21-23,36-42`), and its comment even says so — consistent interaction model across the two most-used list screens. `CallTable`/`CallCard` correctly show "—" instead of guessing when `priority`/`escalation_reason` are absent (`components/calls/CallTable.tsx:35,40`). `TranscriptViewer` renders an explicit "No transcript recorded for this call yet" empty state (`components/calls/TranscriptViewer.tsx:14-16`) rather than a blank panel. `CallTimeline` (`components/calls/CallTimeline.tsx`) correctly explains that state *history* isn't stored at all (permanently blocked, not "empty") — consistent with `KNOWN_LIMITATIONS.md`'s "no live view of an in-progress call" disclosure, and should not be re-reported.

**Live Call Monitor specifically communicates its blocked state well.** `pages/LiveCallMonitor.tsx:10-15` uses the dedicated `NotYetAvailable` component (distinct from `EmptyState` by design — `components/ui/NotYetAvailable.tsx:8-13` documents the distinction: "empty" implies the feature works and there's nothing to show, which would be false here), with a title ("Live Call Monitoring is not yet built") and a description naming what it's waiting on. It is deliberately **not** linked from primary navigation or from anywhere in `CallDrawer`/`CallTable` (confirmed via repo-wide search — `/calls/live` appears only in the route definition and the page's own comment) — so a user can only land on it by typing the URL directly, and when they do, the message is honest and non-alarming rather than a broken blank page or console error. This is the right way to handle a Twilio-blocked screen and needs no further UI work.

### Confusing Workflows
None found beyond what's inherent to the (out-of-scope) Twilio gap.

### Missing Loading States
`CallStatsPanel.tsx:9-17` handles loading correctly with three `LoadingState` cards. No gaps found.

### Missing Empty States
None — `CallsPage`'s table has a real `EmptyState` (`pages/CallsPage.tsx:64-69`), and `TranscriptViewer` has its own (see above).

### Error Handling
Consistent with Tickets: `CallsPage.tsx:50-56` and `CallDrawer.tsx:35-43` both surface `ApiError.message` via `ErrorState` with retry.

### Accessibility
No issues found. `CallCard.tsx:12-21` reuses the same keyboard-accessible card pattern as `TicketCard`. `EscalationReasonBadge`/`CallStateBadge` pair color with text labels throughout.

### Mobile
`CallTable` swaps to `CallCard` below `md` (`components/calls/CallTable.tsx:64,68-77`), same pattern as Tickets — no horizontal-scroll risk.

---

## 4. Analytics

**Files:** `pages/AnalyticsPage.tsx`, `components/analytics/*.tsx`

### What's already good
This is the most consistent page in the app. Every chart goes through the shared `ChartCard` shell (`components/analytics/ChartCard.tsx:22-37`), which handles loading (`LoadingState variant="chart"`), error (`ErrorState` with retry), and empty ("No data in this range") uniformly — so all six charts behave identically, and a failed endpoint never blanks the rest of the page (`pages/AnalyticsPage.tsx` comment, lines 12-14, and verified true in every chart component). `EscalationChart.tsx:24-29` is honest about its metric's definition being "disclosed, not yet product-confirmed" via a `title` tooltip, matching `KNOWN_LIMITATIONS.md`'s Analytics disclosure — correctly not re-reported here as a new gap.

### Confusing Workflows
None — `DateRangeSelect` is a single, clearly-labeled control (`components/analytics/DateRangeSelect.tsx:16-24`) that drives all charts uniformly.

### Missing Loading States
None found — every chart and `MetricsGrid` (`components/analytics/MetricsGrid.tsx:17-24`) handles `isLoading`.

### Missing Empty States
None — every chart passes `isEmpty` to `ChartCard` based on a real zero-count check (e.g., `components/analytics/PriorityChart.tsx:10,13`).

### Error Handling
Fully consistent — every chart uses `ChartCard`'s shared `ErrorState`/retry path.

### Accessibility
Charts rely on Recharts' own rendering; no `aria-label`/`role="img"` wrapper is applied to any `ResponsiveContainer`/chart root (e.g. `components/analytics/CategoryChart.tsx:13-25`), so a screen reader gets no summary of what a given chart shows beyond whatever Recharts emits by default (typically none for SVG bar/line/pie charts). This is a real gap for a healthcare-adjacent, "enterprise ready" tool, though it's consistent across all six charts rather than isolated to one.

### Mobile
Charts use `ResponsiveContainer width="100%"` throughout, so they resize rather than causing page-level horizontal scroll. One nitpick: `CategoryChart.tsx:17` fixes the category-name `YAxis` at `width={140}`, which on a ~320-375px phone viewport (minus `PageContainer`'s `px-4` and `Card`'s `p-6` padding, leaving roughly 220-260px of card width) leaves very little room for the bars themselves — not broken, but cramped compared to the other charts, which don't reserve a fixed label column.

---

## 5. AI Insights

**Files:** `pages/AIInsightsPage.tsx`, `components/aiInsights/*.tsx`

### What's already good
The blocked-vs-real distinction is handled honestly and well. `CategoryBreakdownCard` (real data) and the four `BlockedInsightCard`-based sections (Trending Issues, Repeated Problems, High Risk Alerts, Recommendations) are visually distinct: the blocked cards render a `Lock` icon plus the server's own `blocked_reason` text verbatim (`components/aiInsights/BlockedInsightCard.tsx:17-30`), never a fabricated metric or sample data. This matches `KNOWN_LIMITATIONS.md`'s explicit disclosure ("The AI Insights page mostly shows 'not yet available'... each needs a business decision before it can be built honestly") almost exactly, and is the correct implementation of that disclosure — not a new finding.

### Confusing Workflows
One real issue: `pages/AIInsightsPage.tsx:26-32`, the page's top-level `isError` branch (a genuine fetch/network failure) reuses the title **"AI Insights is not yet available"** — the same framing used for the intentionally-blocked sections below it. A real backend outage and "this feature is deliberately unbuilt" now read identically to the user. If `GET /ai-insights` starts failing for an unrelated reason (server error, timeout), the page tells the user it's "not yet available" rather than "something went wrong," which could mask an actual incident behind what looks like expected, by-design behavior. Recommend a distinct error title (e.g. "Couldn't load AI Insights") to keep the two states visually and textually separate — this is a copy/wiring fix, not a Twilio-scale build.

### Missing Loading States
None — `pages/AIInsightsPage.tsx:24-25` handles `isLoading` with `LoadingState variant="page"` before any section renders.

### Missing Empty States
`CategoryBreakdownCard.tsx:15-16` handles the zero-tickets case with a plain sentence rather than the shared `EmptyState` component (no icon, no consistent visual treatment) — minor inconsistency, not a broken/blank render.

### Error Handling
See "Confusing Workflows" above — the mechanism (retry button, `ApiError.message`) is present and correct (`pages/AIInsightsPage.tsx:29-32`); it's the *labeling* of the error that's misleading, not the handling itself.

### Accessibility
No issues found — this page is static content with no interactive controls beyond the retry button.

### Mobile
`grid-cols-1 lg:grid-cols-2` (`pages/AIInsightsPage.tsx:36`) reflows the four blocked cards to a single column on phone width. No issues.

---

## 6. Settings

**Files:** `pages/SettingsPage.tsx`, `components/settings/*.tsx`

### What's already good
This page correctly implements the read-only constraint `DESIGN.md` §12 and `KNOWN_LIMITATIONS.md` mandate: `ReadOnlyConfigPanel.tsx:1-16`'s own doc comment states plainly that no child may contain an `<input>`, `<form>`, or save action, and a read of every child component (`ProviderStatusCard.tsx`, `EnvironmentStatusCard.tsx`) confirms none do. Masked secrets render exactly as the server sends them (`ProviderStatusCard.tsx:32`), never unmasked client-side. The page-level copy at `pages/SettingsPage.tsx:21-24` proactively tells the user *why* editing isn't available ("won't be until authentication exists") rather than silently omitting the controls — this is good, honest UX for a feature gap, not a bug to fix.

### Confusing Workflows
`ProviderStatusCard.tsx:28` passes `label={data.configured ? "Configured" : "Not configured"}` into `StatusIndicator`, whose own render (`components/ui/StatusIndicator.tsx:42-43`) appends a *second*, separate status word (`STATUS_TEXT[status]`, e.g. "Operational"/"Down") right after it. The result reads as a doubled, slightly redundant/confusing string — e.g. "● Configured Operational" or "● Not configured Down" — where `StatusIndicator` was designed for a `label` that names the *entity* (as `SystemHealthPanel.tsx:22` correctly does with `label="OpenAI"`), not a second status phrase. Low severity, but worth a copy fix since it's the kind of thing a CEO glancing at this page (per `DESIGN.md` §2.2) would have to pause and parse.

### Missing Loading States
None — `pages/SettingsPage.tsx:26-27` handles `isLoading` with `LoadingState variant="page"`.

### Missing Empty States
Not applicable — this page always has content to show (provider cards + environment card) once loaded.

### Error Handling
Good — `pages/SettingsPage.tsx:28-34` surfaces `ApiError.message` through `ErrorState` with retry, consistent with every other screen.

### Accessibility
No issues found — this page is entirely read-only `<dl>`/badge content, no interactive form elements to mislabel.

### Mobile
`ReadOnlyConfigPanel.tsx:15` uses `grid-cols-1 md:grid-cols-2`, reflowing cleanly to one column on phone width.

---

## 7. Cross-Cutting: Shared Components & Navigation

### What's already good
- **`components/ui/*` primitives are genuinely well-built** and, with the single exception of `NewTicketPage`, consistently *used*: `LoadingState`, `ErrorState`, `EmptyState`, `NotYetAvailable`, `DataTable`, `Pagination`, `Modal`, `Drawer` all appear in real usage across Dashboard/Tickets/Calls/Analytics/AI Insights/Settings, not just defined and ignored.
- **Focus management is correct.** `lib/useFocusTrap.ts:15-56` traps Tab/Shift+Tab inside the open `Modal`/`Drawer`, restores focus to the triggering element on close (line 52), and locks body scroll while open (line 23) — this is exactly what `DESIGN.md` §20.2 calls for, and it's shared by both `Modal.tsx:33` and `Drawer.tsx:22` and `Sidebar.tsx:41` (mobile nav overlay), so there's one correct implementation rather than three divergent ones.
- **Escape/backdrop-dismiss is correctly restricted for destructive actions.** `Modal.tsx:32-33` passes a no-op in place of `onClose` for `variant="delete"`, so a destructive confirmation can't be dismissed by Escape or backdrop click — matches the component's own doc comment. (Note: `Modal` itself isn't currently used by any production page — only `routes/DevComponentsPage.tsx` — so there's no live destructive-action flow in the app today to verify this against end-to-end, but the primitive itself is correctly built for when one exists.)
- **Nav active-state never relies on color alone.** `components/layout/NavItem.tsx:42-48` pairs the active route's primary-color text with a persistent leading bar marker, satisfying `DESIGN.md` §5/§14's explicit rule.
- **Responsive sidebar behavior matches spec.** `Sidebar.tsx:25-37` implements the documented three states — hidden below `md`, icon-only rail from `md`-`xl` (label is `sr-only` until `xl`, `NavItem.tsx:50`), full label at `xl`+ — and the mobile overlay (`Sidebar.tsx:39-82`) is keyboard-trapped and closes via the header's hamburger (`Header.tsx:33-35`) or an explicit close button.
- **Skip-to-content link is present and correctly ordered** before the sidebar in tab order (`app/AppShell.tsx:20-25`), a real, easy-to-miss accessibility feature that's actually implemented here.
- **Toasts are accessible:** `NotificationCenter.tsx:41` uses `role="alert"` for error toasts and `role="status"` for others, with a `aria-live="polite"` container (`NotificationCenter.tsx:86`), and every toast has an explicit dismiss button with `aria-label="Dismiss notification"` (line 62).
- **Icon-only buttons are guarded:** `Button.tsx:39-42` logs a console warning if a `variant="icon"` button is rendered without an `aria-label` — a lightweight but real guardrail against the most common icon-button accessibility miss. A spot-check of actual usage (`Modal.tsx:67`, `Drawer.tsx:54`, `Sidebar.tsx:69`, `Header.tsx:33`) shows every icon button in the app does supply one.

### Confusing Workflows / Gaps
- **Light mode is unreachable from the UI.** `app/ThemeProvider.tsx:44-48` exports a working `useTheme()`/`setTheme()`, and `styles/tokens.css:64-86` defines a full light-mode token override exactly as `DESIGN.md` §14 specifies — but a repo-wide search shows `setTheme`/`useTheme` are never called anywhere outside `ThemeProvider.tsx` itself. There is no toggle in `Header.tsx`, `SettingsPage.tsx`, or anywhere else. A user can only reach light mode by manually editing `localStorage["hfmg-theme"]` in devtools. Since `DESIGN.md` §2.4 explicitly designed light mode as "a deliberate second pass," this looks like an intentionally-deferred toggle rather than an oversight, but as it stands the feature is fully built and completely inaccessible — worth either adding a one-click toggle (cheap) or documenting the gap in `KNOWN_LIMITATIONS.md` the way other deferred items are.

### Missing Loading States
Covered per-page above (Dashboard's `StatusBar`/`SystemHealthPanel`, Tickets' `NewTicketPage`).

### Accessibility (cross-cutting findings not already covered per-page)
- **Light-mode badge contrast is very likely below WCAG AA and should be verified before light mode is exposed to users.** `Badge.tsx:22-31`'s `COLOR_CLASSES` render each semantic color as `text-<color>` directly (e.g. `text-success`, `text-danger`, `text-warning`, `text-info`) over a ~16%-opacity tint of that same color (`bg-<color>/16`), which is visually close to the raw color-on-background-white contrast in light mode since `--card`/`--background` become `#ffffff`/`#f8fafc` (`styles/tokens.css:65,68`) while `--success`/`--warning`/`--danger`/`--info` keep their **dark-mode hex values unchanged** in the light-mode override block (`styles/tokens.css:64-86` — notably absent from the list of redefined tokens). Computing WCAG relative luminance for the two most-used status colors against white: `--success #22c55e` ≈ **2.3:1**, `--danger #ef4444` ≈ **3.8:1** — both fail the 4.5:1 AA threshold for normal-size text (badges render at `text-xs`, 12px, not large text), despite `tokens.css:6-7`'s own comment asserting these "were chosen to already clear AA contrast on both a very light and very dark surface." `DESIGN.md` §14 itself flags this as something to "verify at implementation time rather than assuming" — this review's math suggests that verification hasn't happened yet for the status-color badges specifically. This affects `PriorityBadge`, `StatusBadge` (partially — some statuses use `muted`/`primary` which likely fare better), `CallStateBadge`, and `EscalationReasonBadge` wherever they render in light mode. Recommend running an actual contrast checker against the rendered light-mode DOM before treating light mode as ship-ready, and if confirmed, darkening the status hex values specifically for `[data-theme="light"]` rather than reusing the dark-mode values.
- Charts have no accessible text alternative (see Analytics section) — a cross-cutting gap since it's the same `ChartCard`/Recharts pattern in all six chart components.
- No `<img>`/`alt` usage anywhere in the app (confirmed via repo-wide search) — not a gap, just noting there was nothing to check here; all iconography is Lucide SVG with `aria-hidden="true"` applied consistently.

### Mobile
- Confirmed no horizontal-scroll risk on any table (`DataTable` wraps in `overflow-x-auto` as a deliberate fallback — `components/ui/DataTable.tsx:55` — but both `TicketTable` and `CallTable` avoid needing it in practice by swapping to a card stack below `md`).
- Tap targets: `Button.tsx:52` sizes icon buttons at `size-10` (40px), meeting the general 44px-adjacent mobile tap-target guidance closely enough to not flag as broken; non-icon buttons are `h-10` (40px tall) with horizontal padding, similarly reasonable.
- `Drawer.tsx:29,48` degrades to full-width with no backdrop blur below `md` (`md:backdrop-blur-sm`) — functionally full-screen as `DESIGN.md` §16 specifies, though the backdrop itself is still semi-transparent-only (not blurred) on mobile; purely cosmetic, not a usability issue.

---

## Top Fixes (Prioritized)

| # | Finding | Severity | Effort | Where |
|---|---|---|---|---|
| 1 | `NewTicketPage` is entirely unstyled/off-design-system, with no label↔input association at all (real accessibility bug) | **High** | Medium | `pages/NewTicketPage.tsx` (rebuild using `Input`/`Select`/`Textarea`/`Button` from `components/ui`) |
| 2 | Light-mode status-color badge contrast likely fails WCAG AA (`success` ≈2.3:1, `danger` ≈3.8:1 against white) | **High** (if/when light mode ships to users) | Low–Medium | `styles/tokens.css:64-86`, `components/ui/Badge.tsx:22-31` |
| 3 | AI Insights' real-error state reuses "not yet available" copy, masking genuine backend failures as by-design gaps | Medium | Low (copy/prop change) | `pages/AIInsightsPage.tsx:26-32` |
| 4 | Light mode is fully built but has no UI entry point anywhere (`setTheme` never called outside `ThemeProvider`) | Medium | Low (add a header/settings toggle) | `app/ThemeProvider.tsx`, `components/layout/Header.tsx` |
| 5 | `NewTicketPage` category dropdown has no loading indicator and a bare-string fetch-failure message with no retry | Medium | Low | `pages/NewTicketPage.tsx:25-35` |
| 6 | `SystemHealthPanel`/`StatusBar` show "Unknown" instead of a skeleton during initial load, indistinguishable from a real unreachable-health-endpoint state | Low | Low | `components/layout/StatusBar.tsx:11-15`, `components/dashboard/SystemHealthPanel.tsx:12-16` |
| 7 | `ProviderStatusCard` produces a doubled/confusing status phrase ("Configured Operational") | Low | Low | `components/settings/ProviderStatusCard.tsx:28` |
| 8 | Charts have no accessible text alternative for screen-reader users | Low–Medium | Medium (needs a per-chart summary strategy) | `components/analytics/*.tsx` (via `ChartCard`) |
| 9 | `CategoryChart`'s fixed 140px Y-axis label column crowds the bars on narrow phone widths | Low | Low | `components/analytics/CategoryChart.tsx:17` |
| 10 | `CategoryBreakdownCard`'s zero-data state uses a bare sentence instead of the shared `EmptyState` component | Low | Low | `components/aiInsights/CategoryBreakdownCard.tsx:15-16` |

---

*Reviewed by static code read of every file in scope plus targeted cross-referencing against `DESIGN.md` and `KNOWN_LIMITATIONS.md`. No code was modified as part of this review.*

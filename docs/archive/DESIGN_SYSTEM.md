# HFMG AI Help Desk — Design System

**Version:** 1.0
**Status:** Design Phase (companion to `DESIGN.md`, `WIREFRAMES.md`)
**Audience:** Frontend engineers, UI reviewers

This document is the token- and component-standard layer beneath `WIREFRAMES.md`. `DESIGN.md` remains the single source of truth for product/page decisions; where this document defines a value `DESIGN.md` already stated (color hexes, radius, typeface), it repeats that value verbatim rather than re-deriving it, so the two never drift. Where this document goes further than `DESIGN.md` (secondary/info/AI colors, spacing scale, component-level specs), it extends the same system rather than introducing a second one.

No code in this document. Values here are contracts for whoever wires up Tailwind config, shadcn theme tokens, and Recharts color scales — not a preview of that code.

---

## 1. Design Philosophy

Five principles, inherited from `DESIGN.md` §2, restated as the lens every design decision below is filtered through:

| Principle | What it rules out |
|---|---|
| **AI First** | Any dashboard that leads with raw counts and buries AI status in a corner. AI status/activity/insight must be visible in the first screen, not a settings toggle. |
| **Enterprise Grade** | Placeholder-looking empty states, unstyled browser defaults, inconsistent spacing, "lorem ipsum" energy anywhere. |
| **Executive Friendly** | Legends, jargon, or a need to hover/click to understand system health. The Dashboard specifically must read in under 10 seconds. |
| **Healthcare Appropriate** | Color-as-only-signal (color-blind accessibility is not optional here — `DESIGN.md` §14), casual/playful copy, any UI that implies call recording or audio playback exists when it does not. |
| **Dark Mode First** | Designing a component in light mode and "adding dark mode later." Dark is the native theme; light is a deliberate second pass against the same semantic tokens. |

---

## 2. Color System

All dark-mode values are copied verbatim from `DESIGN.md` §14 where that section already defines them; everything else (Secondary, Info, AI, table, status) is a compatible extension using the same palette family (Tailwind slate/blue/violet families) so nothing clashes when placed side by side.

### 2.1 Core Palette — Dark (default theme)

| Token | Hex | Usage |
|---|---|---|
| `color.background` | `#0B1120` | Page background |
| `color.surface.sidebar` | `#111827` | Sidebar, one step lighter than background |
| `color.surface.card` | `#1F2937` | Cards, panels, drawer surface |
| `color.surface.card-hover` | `#263145` | Hoverable card/row state |
| `color.border` | `#27324A` | Hairline borders between elevation levels — does the elevation work shadows can't on dark backgrounds (`DESIGN.md` §13.3) |
| `color.primary` | `#3B82F6` | Primary actions, active nav indicator, links, focus rings |
| `color.primary.hover` | `#2563EB` | Primary hover |
| `color.primary.active` | `#1D4ED8` | Primary pressed |
| `color.secondary` | `#64748B` | Secondary buttons, less-emphasized controls |
| `color.secondary.hover` | `#475569` | Secondary hover |
| `color.success` | `#22C55E` | Resolved/operational status, positive deltas |
| `color.success.hover` | `#16A34A` | — |
| `color.warning` | `#F59E0B` | Medium/High priority, degraded status, in-progress work |
| `color.warning.hover` | `#D97706` | — |
| `color.danger` | `#EF4444` | Urgent/Critical priority, down status, destructive actions |
| `color.danger.hover` | `#DC2626` | — |
| `color.info` | `#38BDF8` | Informational badges (e.g. `NEW` ticket status) — distinct from Primary so "new/informational" never visually competes with "primary action" |
| `color.text.primary` | `#F8FAFC` | Primary text on dark surfaces |
| `color.text.muted` | `#94A3B8` | Secondary text, timestamps, placeholders |
| `color.text.disabled` | `#475569` | Disabled control text |

### 2.2 AI Colors

A dedicated accent family so AI-generated or AI-in-progress content is visually distinct from human-authored content and from ordinary status colors — this is the "AI First" principle made literal in the palette.

| Token | Hex | Usage |
|---|---|---|
| `color.ai.accent` | `#8B5CF6` | AI Summary/Analysis/Insight card accents, left-border treatment, sparkle icon fill |
| `color.ai.accent-hover` | `#7C3AED` | Hover/active on AI-accented controls (e.g. "Regenerate summary") |
| `color.ai.subtle` | `rgba(139,92,246,0.12)` | AI card background wash, badge background |
| `color.ai.gradient` | `linear-gradient(135deg, #8B5CF6 0%, #3B82F6 100%)` | Reserved for a single hero moment per screen (e.g. the AI Insights page header rule) — never used on more than one element per view, or it stops reading as special |

**Usage rule:** violet means "the AI produced or is actively producing this," never "this is simply important." A `URGENT` ticket is Danger red, not violet, even though the AI assessed it — the color encodes *provenance*, not *severity*.

### 2.3 Background, Sidebar, Card Colors (elevation ladder)

Three-step elevation, darkest to lightest: `background` (`#0B1120`) → `sidebar` (`#111827`) → `card` (`#1F2937`) → `card-hover` (`#263145`). Never skip a step (e.g. a card directly on `background` with no intermediate is fine; a card-on-card needs the hover token, not a fourth invented shade).

### 2.4 Table Colors

| Token | Hex | Usage |
|---|---|---|
| `color.table.header-bg` | `#111827` | Table header row (same as sidebar — reads as a fixed structural element) |
| `color.table.row-bg` | `#1F2937` | Table body rows (= Card) |
| `color.table.row-hover` | `#263145` | Row hover |
| `color.table.row-border` | `#27324A` | Hairline row separators — no zebra striping; Linear-style tables separate rows with hairlines, not alternating fills |

### 2.5 Status Colors — Semantic Mapping

Ticket status (`ticket_status_enum`, `DATABASE_DESIGN.md` §3.1):

| Status | Color | Hex |
|---|---|---|
| `NEW` | Info | `#38BDF8` |
| `OPEN` | Primary | `#3B82F6` |
| `IN_PROGRESS` | Warning | `#F59E0B` |
| `ON_HOLD` | Muted | `#94A3B8` |
| `RESOLVED` | Success | `#22C55E` |
| `CLOSED` | Secondary (dim) | `#64748B` |
| `CANCELLED` | Danger (dim, strikethrough label) | `#EF4444` at 60% opacity |

Priority (`priority_enum`):

| Priority | Color | Hex |
|---|---|---|
| `LOW` | Muted | `#94A3B8` |
| `MEDIUM` | Info | `#38BDF8` |
| `HIGH` | Warning | `#F59E0B` |
| `URGENT` | Danger | `#EF4444` |

Source (`ticket_source_enum`) — deliberately ties `PHONE` to the AI accent, since every phone ticket passed through the voice agent:

| Source | Color | Hex |
|---|---|---|
| `WEB` | Primary | `#3B82F6` |
| `PHONE` | AI Accent | `#8B5CF6` |
| `EMAIL` | Info | `#38BDF8` |
| `WALK_IN` | Muted | `#94A3B8` |

Voice call state (`voice_call_state_enum`, `CALL_FLOW.md` §2), grouped by phase:

| Phase | States | Color |
|---|---|---|
| Collecting | `GREETING`, `COLLECT_DESCRIPTION`, `COLLECT_NAME`, `COLLECT_PHONE`, `COLLECT_EMAIL` | Info `#38BDF8` |
| Confirming | `CONFIRM_EMAIL`, `CONFIRM_CATEGORY`, `ANYTHING_ELSE` | Primary `#3B82F6` |
| AI Working | `CREATING_TICKET`, `READ_BACK` | AI Accent `#8B5CF6` |
| Terminal — good | `COMPLETED` | Success `#22C55E` |
| Terminal — escalated | `ESCALATED` | Danger `#EF4444` |
| Terminal — lost | `ABANDONED` | Muted `#94A3B8` |

Dependency/system status (OpenAI, Twilio, Database, Email — `DESIGN.md` §6.1):

| State | Color | Hex |
|---|---|---|
| Operational | Success | `#22C55E` |
| Degraded | Warning | `#F59E0B` |
| Down | Danger | `#EF4444` |
| Unknown / not yet checked | Muted | `#94A3B8` |

**Non-negotiable rule (carried from `DESIGN.md` §14):** status color is never the only signal. Every status dot/badge ships with a text label or icon alongside the color, system-wide, no exceptions — doubly true for a healthcare-adjacent product.

### 2.6 Light Mode (secondary theme)

Per `DESIGN.md` §14, derived from the same semantic relationships, not inverted:

| Token | Hex |
|---|---|
| `color.background` | `#F8FAFC` |
| `color.surface.sidebar` | `#F1F5F9` |
| `color.surface.card` | `#FFFFFF` |
| `color.border` | `#E2E8F0` |
| `color.primary` | `#2563EB` (darkened one step from dark-mode Primary for AA contrast on white) |
| `color.text.primary` | `#0F172A` |
| `color.text.muted` | `#64748B` |

Success/Warning/Danger/Info/AI Accent keep their dark-mode hex values in light mode — they were chosen to already clear AA on both a very light and very dark surface; only the neutrals (background/surface/border/text) get their own light-mode values.

---

## 3. Typography System

### 3.1 Font Family
**Inter**, throughout — no second display face (`DESIGN.md` §13.1). System font stack as fallback only for pre-load flash: `Inter, -apple-system, "Segoe UI", sans-serif`.

### 3.2 Heading Scale

| Token | Size / Line-height | Weight | Usage |
|---|---|---|---|
| `text.display` | 36px / 44px | 700 | Reserved — not used in MVP scope; future marketing/empty-state hero only |
| `text.h1` | 28px / 36px | 700 | Page title (e.g. "Tickets", "Analytics") |
| `text.h2` | 22px / 30px | 600 | Section heading within a page (e.g. drawer section titles) |
| `text.h3` | 18px / 26px | 600 | Card titles, table section headers |
| `text.h4` | 15px / 22px | 600 | Sub-labels, KPI card titles |

### 3.3 Body Scale

| Token | Size / Line-height | Weight | Usage |
|---|---|---|---|
| `text.body-lg` | 16px / 24px | 400 | Primary reading content (ticket description, transcript) |
| `text.body` | 14px / 20px | 400 | Default UI text — table cells, form labels, most copy |
| `text.body-sm` | 13px / 18px | 400 | Dense table rows, secondary metadata |

### 3.4 Caption Scale

| Token | Size / Line-height | Weight | Usage |
|---|---|---|---|
| `text.caption` | 12px / 16px | 500 | Timestamps, badge labels, helper text |
| `text.overline` | 11px / 14px | 600, uppercase, +0.04em tracking | Section eyebrows (e.g. "SYSTEM HEALTH") |

### 3.5 Font Weights
400 (regular, body), 500 (medium, captions/badges), 600 (semibold, headings/emphasis), 700 (bold, page titles only). No 300 or 800 — Inter's 600/700 pairing against 400 is sufficient hierarchy (`DESIGN.md` §13.1); introducing more weights dilutes it.

### 3.6 Usage Rules
- Never use a heading token below `text.h4` for anything but a heading role, even if the size happens to match — screen readers and visual scanning both depend on heading tokens staying reserved for actual hierarchy.
- Numeric values in KPI cards use tabular figures (`font-variant-numeric: tabular-nums`) so digits don't shift width as they update.
- Monospace (`ui-monospace` stack) is reserved for ticket numbers (`HFMG-2026-000482`) and call SIDs only — it signals "this is an identifier," not general UI text.

---

## 4. Spacing System

**4px base grid.** Every margin, padding, and gap is a multiple of 4px — no arbitrary values (e.g. 7px, 13px).

| Token | Value | Typical use |
|---|---|---|
| `space.0` | 0px | — |
| `space.1` | 4px | Icon-to-text gap (tight), badge internal padding |
| `space.2` | 8px | Icon-to-text gap (default), inline element gaps |
| `space.3` | 12px | Compact form field gap, table cell vertical padding |
| `space.4` | 16px | Standard form field gap, card internal padding (small cards) |
| `space.5` | 20px | — |
| `space.6` | 24px | Card internal padding (default), gap between related cards in a grid |
| `space.8` | 32px | Section gap within a page, page horizontal padding (tablet) |
| `space.10` | 40px | — |
| `space.12` | 48px | Gap between major page sections |
| `space.16` | 64px | Page horizontal padding (desktop, large viewport) |

**Margins:** sections stack with `space.8` (32px) between them; never rely on a component's own external margin for page rhythm — the page container owns section spacing via `gap`, components own only their internal padding.

**Padding:** cards default to `space.6` (24px) all sides; compact cards (KPI cards in a 5-up grid) use `space.4` (16px) to fit density without crowding.

**Gaps:** grid/flex gaps between sibling cards default to `space.6` (24px) desktop, `space.4` (16px) tablet/mobile.

---

## 5. Layout System

### 5.1 Desktop Layout (≥1280px — primary target)
```
┌──────────────────────────────────────────────────────────┐
│ Header — 64px height, full width                          │
├───────────────┬────────────────────────────────────────────┤
│ Sidebar        │ Content — max-width 1440px, centered,       │
│ 240px fixed    │ 32px side padding within that max-width     │
│                │ (scrolls independently of header/sidebar)   │
└───────────────┴────────────────────────────────────────────┘
```
- **Header height:** 64px, fixed, never scrolls.
- **Sidebar width:** 240px fixed, expanded state. Never resizable by the user (resizable sidebars are a complexity this product doesn't need).
- **Content max-width:** 1440px — resist stretching tables to full viewport width on ultra-wide monitors (`DESIGN.md` §5).

### 5.2 Tablet Layout (768–1279px)
Sidebar collapses to a 64px icon-only rail (labels on hover/tooltip) or a hamburger-triggered full overlay — implementer's choice, but pick one and apply it consistently, not a hybrid. Content padding drops to `space.8` (32px → still 32px is fine at this width; drop to `space.6`/24px only below 900px). Tables become horizontally scrollable rather than reflowing columns (`DESIGN.md` §16).

### 5.3 Mobile Layout (<768px)
Out of scope for a dedicated layout pass (`DESIGN.md` §16, §19 — "Mobile Application" is a distinct future item). Minimum requirement only: no horizontal page-break, sidebar becomes a full-screen overlay triggered by a hamburger, and the Ticket Drawer becomes full-screen rather than a partial slide-over.

### 5.4 Content Width Rules
- Reading-width content (ticket description, transcript text) caps at 720px even inside a wider drawer/panel — unconstrained line length hurts readability regardless of available space.
- Tables and charts use the full available content width — the max-width rule is for the page shell, not every child.

### 5.5 Sidebar Width Rules
240px expanded / 64px collapsed (tablet). Icon size within the rail: 20px, centered, 44px touch target.

### 5.6 Header Height Rules
64px fixed at all breakpoints — never grows to accommodate wrapped content; the System Status strip inside it truncates/collapses to icon-only dots before the header would need to grow.

---

## 6. Border Radius

Per `DESIGN.md` §13.2: 16px is the ceiling, reserved for surfaces large enough to carry it; small controls use a proportionally smaller radius so they don't read as pills by accident.

| Token | Value | Applies to |
|---|---|---|
| `radius.sm` | 8px | Buttons, inputs, small filter chips |
| `radius.md` | 12px | Table container (outer wrapper only — cells are square), analytics chart containers |
| `radius.lg` | 16px | Cards, panels, drawers, modals |
| `radius.pill` | 9999px | Status badges, priority badges, source badges — a pill communicates "state," not "container" |

---

## 7. Shadow System

Dark backgrounds make shadows do far less visual work than on light surfaces (`DESIGN.md` §13.3) — every shadow token pairs with a 1px border rather than relying on shadow alone for elevation separation.

| Token | Value | Usage |
|---|---|---|
| `shadow.sm` | `0 1px 2px rgba(0,0,0,0.24)` + `1px solid #27324A` border | Default card resting state |
| `shadow.md` | `0 4px 12px rgba(0,0,0,0.32)` + border | Hover-elevated card, dropdown menu |
| `shadow.lg` | `0 12px 32px rgba(0,0,0,0.40)` + border | Drawer, popover |
| `shadow.overlay` | Backdrop: `rgba(2,6,23,0.72)` with light blur | Modal/drawer backdrop scrim |

Light mode: same shadow values work without adjustment (light-on-white shadows are naturally more visible; do not increase opacity to compensate).

---

## 8. Iconography

**Library:** Lucide Icons, exclusively — no mixing icon sets.

| Context | Size | Stroke width |
|---|---|---|
| Inline with body text / badges | 14px | 1.75 |
| Buttons, table row actions | 16px | 1.75 |
| Nav items, card headers | 20px | 1.5 |
| Empty-state hero icon | 40px | 1.25 |

**Usage guidelines:**
- Every icon-only control (icon buttons, collapsed-sidebar nav items) carries an `aria-label` and a tooltip on hover/focus — icons alone are never the only identifier of a control's function.
- Status icons always pair with a text label (§2.5 rule) — an icon substituting for a status word is not acceptable on this product.
- Nav icons are outline style at all times, including active state — the active state is communicated by color + the leading marker (§5 shell rule), never by switching to a filled icon variant, to keep the icon set visually consistent.

---

## 9. Status Indicators

Every status indicator in the product is one of two shapes: a **dot + label** (compact, used in tables and the system status strip) or a **badge** (pill-shaped, used standalone in cards/headers). Both always carry text; the dot/badge color is decoration, not the sole signal.

### 9.1 Dependency Status (OpenAI, Twilio, Database, Email)
```
🟢 Operational   🟡 Degraded   🔴 Down   ⚪ Unknown
```
Colors per §2.5. Hover reveals last-checked timestamp (`DESIGN.md` §6.1) — this is supplementary detail, not the only way to see the status, since the dot+label is already sufficient at a glance.

### 9.2 Ticket Status Badge
Pill, `radius.pill`, background = status color at 16% opacity, text/dot = full-opacity status color. Labels exactly match the enum values in Title Case: New, Open, In Progress, On Hold, Resolved, Closed, Cancelled.

### 9.3 Call State Badge
Same pill treatment, colors per §2.5's phase grouping. Label uses a human-readable form of the enum (`READ_BACK` → "Reading back ticket #"), not the raw enum string — raw enum strings are for logs, not the UI a human reads.

### 9.4 Priority Badge
Pill, always includes a leading icon reinforcing severity independent of color (Low: none/minus, Medium: circle, High: triangle, Urgent: double-triangle/flame) — this is the accessibility redundancy §2.5 requires, made concrete.

---

## 10. Buttons

| Variant | Fill | Text color | Border | Usage |
|---|---|---|---|---|
| **Primary** | `color.primary` | `#F8FAFC` | none | One per view/section — the single most important action (e.g. "New Ticket") |
| **Secondary** | transparent | `color.text.primary` | `1px solid color.border` | Supporting actions alongside a primary (e.g. "Cancel" next to "Save") |
| **Danger** | `color.danger` | `#F8FAFC` | none | Destructive/irreversible actions only (never for "close drawer" or ordinary negative-sounding actions) |
| **Ghost** | transparent | `color.text.muted` | none, `card-hover` bg on hover | Low-emphasis actions (table row actions, drawer close) |
| **Icon** | ghost by default | — | — | Square, 36px (compact) or 40px (default) hit area, icon centered at 16–20px |

**States, all variants:**
- **Hover:** background shifts to the variant's `.hover` token; 120ms ease transition, no scale/transform.
- **Loading:** label replaced by a centered spinner (same color as label), button width does not change (prevents layout shift), control is non-interactive.
- **Disabled:** 40% opacity, `cursor: not-allowed`, no hover state change — disabled must look inert, not just slightly dimmed.

---

## 11. Forms

| Element | Height | Border | Focus | Notes |
|---|---|---|---|---|
| **Input** | 40px | `1px solid color.border` | `2px solid color.primary` ring, offset 2px | Placeholder uses `color.text.muted` |
| **Textarea** | min-height 96px, auto-grow to a cap of 320px | same as Input | same as Input | Used for description fields, comment composition (Phase 3) |
| **Select** | 40px | same as Input | same as Input | Trailing chevron icon (Lucide `chevron-down`), 16px |
| **Search** | 40px | same as Input | same as Input | Leading `search` icon at 16px, clear (`x`) icon appears once text is entered |

**Validation:** invalid state = border color → `color.danger`, plus a caption-scale (`text.caption`) error message directly beneath the field in `color.danger` — never color-only (border color alone is not sufficient feedback).

**Error Display (form-level):** a banner above the form, `color.danger` at 12% opacity background, `1px solid color.danger` border, `radius.md`, listing every field error as a bullet — used when multiple fields fail validation at once (e.g. ticket creation form), so the user doesn't have to hunt field-by-field.

---

## 12. Cards

All cards: `radius.lg` (16px), `shadow.sm` resting / `shadow.md` on hover (only for clickable cards), `space.6` (24px) internal padding, `color.surface.card` background.

| Card type | Structure |
|---|---|
| **KPI Card** | Overline label (top) → large tabular-numeral value → optional delta chip (↑/↓ + %, colored Success/Danger) → optional trailing icon |
| **Status Card** | Icon + label (left) → status dot + text (right) → caption timestamp below |
| **Insight Card** | AI Accent left border (4px) → icon (violet) + title → body text → optional CTA link |
| **Analytics Card** | `text.h3` chart title (top-left) → optional date-range control (top-right) → chart body → legend below |
| **Call Card** | Caller (phone/name) + state badge (top row) → duration + category (second row) → click target for detail |
| **Ticket Card** | Ticket number (mono) + priority badge (top row) → caller + category (second row) → AI summary excerpt (truncated, 2 lines) — used as the Tickets table's row-as-card fallback below `md` breakpoint |

---

## 13. Tables

Base: `radius.md` container, `color.table.header-bg` header row (`text.overline`-style column labels, 44px height), body rows 56px height (40px in a "compact density" mode if one is ever added — not required for MVP), hairline `color.table.row-border` between rows, `color.table.row-hover` on hover.

- **Ticket Table:** columns per `WIREFRAMES.md` §3 — Ticket Number (mono), Caller, Category, AI Summary (truncated), Source Badge, Priority Badge, Status Badge, Created Date (relative, e.g. "2h ago," with absolute on hover).
- **Call Table:** Caller, Call State Badge, Duration, Issue (truncated), Priority Badge, Outcome.
- **Analytics Table:** used only where a chart alone is insufficient (e.g. a data-export view) — not part of MVP scope; defined here so it's consistent if/when built: same header/row treatment as Ticket Table, numeric columns right-aligned.
- **Sorting:** clickable column header, chevron indicator (up/down/none), one active sort column at a time — no multi-column sort in this product.
- **Filtering:** a filter bar row directly above the table, each filter a `Select`-style dropdown or chip group; active filters shown as removable chips beneath the bar when more than one is applied.
- **Pagination:** footer row — page size selector (left), page indicator + prev/next (center-right), total count (right). No infinite scroll on data tables in this product — pagination keeps position addressable (shareable, refresh-safe).
- **Empty States:** see `WIREFRAMES.md` §11 — icon + one sentence + primary action, centered within the table's normal height (not a collapsed single row).

---

## 14. Drawers

All drawers: slide in from the right, `radius.lg` on the leading edge only, `shadow.lg`, backdrop scrim (`shadow.overlay`) covering the rest of the viewport, closable via the `X` button, `Esc` key, or backdrop click.

| Drawer | Width (desktop) | Notes |
|---|---|---|
| **Ticket Drawer** | 480px | Sections per `WIREFRAMES.md` §4 (Caller Info, Issue Info, AI Summary, Transcript, Timeline, Status Controls, AI Analysis), each separated by a hairline border, sticky header (ticket number + close), no sticky footer — status change is inline in its section |
| **Call Drawer** | 480px | Caller info, state history, full transcript (scrollable `TranscriptViewer`), linked ticket link-out |
| **Details Drawer** (generic) | 480px | Reusable shell for any future single-record detail view (e.g. a user or category record in Phase 3) — same header/section pattern, no drawer-specific content baked into the shell component |

All drawers become full-screen (no backdrop, no radius) below the `md` breakpoint (`DESIGN.md` §16).

---

## 15. Modals

Centered, `radius.lg`, `shadow.overlay` backdrop with blur, max-width per type below. Modals close on `Esc` and backdrop click **except** destructive/irreversible ones, which require an explicit button press (never accidentally dismiss a delete confirmation).

| Modal | Max-width | Close behavior |
|---|---|---|
| **Confirmation** | 420px | Esc / backdrop / button all close |
| **Delete** | 420px | Explicit button only — no Esc/backdrop dismiss; primary action is Danger-variant and requires the exact record name typed back for anything with downstream effects (Phase 3, admin-only actions) |
| **Escalation** | 480px | Esc / backdrop / button — this is informational (viewing why a call escalated), not destructive |
| **Error** | 480px | Esc / backdrop / button — always offers a "Retry" or "Dismiss" action, never a dead end |

---

## 16. Notifications (Toast)

Stack from the top-right, `radius.md`, `shadow.md`, max 3 visible at once (older ones collapse into a "+N more" summary).

| Variant | Color | Auto-dismiss |
|---|---|---|
| **Success** | `color.success` left border + icon | 5s |
| **Warning** | `color.warning` left border + icon | 7s |
| **Error** | `color.danger` left border + icon | Manual dismiss only — errors should not vanish before being read |
| **Info** | `color.info` left border + icon | 5s |

Each toast: icon (16px, variant color) + message (`text.body-sm`) + optional inline action link + close (`x`, ghost icon button).

---

## 17. Charts

**Library:** Recharts, exclusively. Colors pulled from the categorical set below — never a separate, uncoordinated chart palette (`DESIGN.md` §10).

**Categorical palette (in order of assignment):** `#3B82F6` (Primary), `#38BDF8` (Info), `#8B5CF6` (AI Accent), `#22C55E` (Success), `#F59E0B` (Warning), `#94A3B8` (Muted) — six slots, sufficient for every categorical chart in this product (max 6 categories: 6 ticket categories, 4 priorities, 4 sources). If a dataset ever exceeds 6 categories, group the tail into an "Other" bucket rather than adding a 7th color.

| Chart type | Used for | Rule |
|---|---|---|
| **Bar** | Categorical comparison (Tickets by Category, Tickets by Priority, Tickets by Source) | Horizontal bars once a category label exceeds ~12 characters (avoids rotated axis labels); vertical otherwise |
| **Line** | Time-series trend (Calls per Day) | Single series only per chart in this product — no more than one line per chart, to keep the Dashboard/Analytics legible per the Executive Friendly principle |
| **Pie / Donut** | Source breakdown only, and only when ≤4 slices | Never used for anything with more than 4 categories — a 6-slice pie is harder to read at a glance than a bar; use bar instead |
| **Area** | Reserved for a cumulative-trend view (e.g. total tickets over time) if one is added post-MVP | Single series, filled at 20% opacity under a solid stroke line |

**Usage standards:**
- Every chart has a visible title (`text.h3`) and, where relevant, a legend — never color-coded data with no key.
- Tooltips on hover show exact values; axis labels alone are not sufficient precision for an operations tool.
- Charts never fetch raw rows and aggregate client-side (`DESIGN.md` §10) — this is a data-flow rule, not a visual one, but it belongs here because it's the thing that keeps charts fast enough to be usable at the density this product needs.
- Respect `prefers-reduced-motion`: chart entrance animations (Recharts' default draw-in) are disabled, not just shortened, when the user has that preference set.

---

## 18. AI Components

The visual signature of "AI First." Every AI component uses the AI Accent color (§2.2) and a consistent left-border or icon treatment so a user learns once, "violet + sparkle = the AI produced this," and it holds everywhere.

| Component | Structure | States |
|---|---|---|
| **AI Summary Card** | 4px violet left border, sparkle icon + "AI Summary" label (top), summary text body, "Regenerate" ghost button (bottom-right) | `PENDING` (skeleton shimmer + "Generating summary…"), `COMPLETED` (full text), `FAILED` (danger-toned inline message + retry button), `DISABLED` (muted "AI summaries are turned off" message, no retry) — states map 1:1 to `ai_summary_status` |
| **AI Recommendation Card** | Same violet left-border treatment, icon + title, recommendation text, no action button until AI Insights' output shape is defined (`WIREFRAMES.md` §8 open decision) | Populated / Blocked (renders an explicit "recommendation logic not yet defined" state — never fabricated placeholder text) |
| **AI Confidence Indicator** | Horizontal meter, 3 segments (Low/Medium/High), filled segment(s) colored, label always spelled out beside the meter | `high` (all 3 segments, Success-tinted), `medium` (2 segments, Warning-tinted), `low` (1 segment, Danger-tinted) — maps to the model's own `confidence` enum (`VOICE_AGENT_DESIGN.md` §3); **never renders a numeric percentage**, since the system stores a 3-level enum, not a raw probability — inventing decimal precision the model never reported would misrepresent the data |
| **AI Analysis Panel** | Model name (mono caption), generated-at timestamp, "Regenerate" action — sits below the AI Summary Card in the Ticket Drawer | Populated / `ai_model` unavailable (MVP schema doesn't persist it per `DATABASE_DESIGN.md` §3.1 — render "Model: not recorded" rather than guessing) |
| **AI Insight Widget** | Compact variant of the Insight Card — icon + one-line finding, no body paragraph, used only in the Dashboard's AI Insights preview (`WIREFRAMES.md` §2) | Populated / Empty ("No notable patterns today") |

**Cross-cutting AI component rule:** none of these components may render fabricated content to fill an empty or blocked state. Where the backend hasn't defined a metric yet (AI Resolution Rate, AI Recommendations' output shape), the component renders an explicit "not yet defined" or "pending" state — the same honesty discipline `WIREFRAMES.md` applies to whole pages applies at the component level too.

---

## 19. Motion System

**Library:** Framer Motion, used only for state transitions — never decoration (`DESIGN.md` §13.4). Rule of thumb before adding any animation: if removing it wouldn't make the product feel broken, cut it.

| Interaction | Animation | Duration | Easing |
|---|---|---|---|
| **Hover** (cards, rows) | Background color shift only — no scale/transform, no shadow pop | 120ms | ease-out |
| **Button hover/press** | Background color shift | 120ms | ease-out |
| **Drawer open/close** | Slide in/out from right + backdrop fade | 240ms | ease-out (open), ease-in (close) |
| **Modal open/close** | Fade + scale from 96%→100% | 180ms | ease-out |
| **Tab switch** | Cross-fade content, sliding underline indicator | 150ms | ease-in-out |
| **Toast enter/exit** | Slide in from right + fade | 200ms | ease-out |
| **Loading skeleton** | Shimmer sweep | 1.4s loop | linear |
| **Live Call state transition** (`CallStateProgress`) | Step indicator fills forward | 200ms | ease-out |
| **KPI value change** | Cut — DESIGN.md's own rule disqualifies this: a count-up animation on every poll tick is decoration, not a state transition a user needs signaled. Value updates in place, no animation. | — | — |

**Global rule:** every animation above respects `prefers-reduced-motion: reduce` — on that preference, drawers/modals still open but with a hard cut (no slide/fade), and the loading shimmer and chart draw-in animations are disabled outright rather than merely shortened.

---

## 20. Accessibility

Target: **WCAG 2.1 AA**, non-negotiable given the healthcare-adjacent context (`DESIGN.md` §2.3).

### 20.1 Contrast
- `color.text.primary` (`#F8FAFC`) on `color.background`/`color.surface.card` clears >12:1 (`DESIGN.md` §14) — comfortably exceeds AA's 4.5:1 for normal text.
- `color.text.muted` (`#94A3B8`) on `color.background` clears ~7:1; on `color.surface.card` it tightens — **verify at implementation time**, and never place muted text on a muted/AI-subtle background (e.g. don't put `color.text.muted` directly on `color.ai.subtle`).
- All status/priority/AI accent colors were chosen to clear AA against both `#0B1120` and `#1F2937` — if a future color is added to any palette, it must be checked against both surfaces before use, not just one.

### 20.2 Keyboard Navigation
- Full tab order follows visual reading order on every page — no positive `tabindex` values anywhere.
- Drawers and modals trap focus while open and return focus to the triggering element on close.
- `Esc` closes any open drawer, modal, or dropdown.
- Table rows that open a drawer (Ticket Table, Call Table) are reachable and activatable via keyboard (`Enter`/`Space` on a focused row), not mouse-click-only.
- Skip-to-content link precedes the sidebar in tab order for screen-reader and keyboard-only users.

### 20.3 Screen Readers
- Every icon-only button has an `aria-label` (§8).
- Status changes that happen without a page navigation (system status strip updates, live call state changes) are announced via an `aria-live="polite"` region — `aria-live="assertive"` reserved only for the Error notification variant.
- Tables use proper semantic markup (`<table>`, `<th scope="col">`) — not `<div>` grids styled to look like tables.
- The AI Confidence Indicator's meter includes a text equivalent (`aria-label="Confidence: high"`) so the segment-fill visual isn't the only way to perceive it.

### 20.4 Focus States
- Every interactive element gets a visible focus ring: `2px solid color.primary`, `2px` offset, on every surface (dark and light) — never suppressed with `outline: none` without a replacement.
- Focus ring color stays Primary blue even on Danger-variant buttons/modals — consistency of the focus indicator matters more than color-matching the control it's on.

### 20.5 WCAG Compliance Checklist (applies to every new component)
- [ ] Passes 4.5:1 contrast for text, 3:1 for UI component boundaries/icons.
- [ ] Fully operable by keyboard alone.
- [ ] No information conveyed by color alone.
- [ ] Respects `prefers-reduced-motion`.
- [ ] Has a text alternative for every icon-only control.
- [ ] Focus order matches visual order.

## Changelog

- **1.0** (2026-09-20) — Initial version, extending `DESIGN.md`'s color/typography/radius foundation into a full component-level design system.

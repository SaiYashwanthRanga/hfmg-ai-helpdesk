# HFMG AI Help Desk — Frontend Gap Report

**Purpose:** every place frontend work touched a business definition, a stale plan assumption, or a deliberately-left-disabled control, across Phases 4–8. Companion to the backend's `DOCS_GAP_REPORT.md` and `REMAINING_PRODUCT_DECISIONS.md` — this file tracks frontend-specific findings; genuine product decisions are cross-referenced there, not duplicated here.

---

## Phase 3 follow-up — ✅ RESOLVED in the final pre-production review pass

**Finding:** Phase 3's `TicketFilters` (Priority, Source selects) and `TicketSearch` shipped disabled with "coming soon" tooltips, because at the time they were built, the backend didn't accept those query params. That stopped being true once Backend Tier 0 added real `priority`/`source`/`q` support to `GET /tickets` — flagged here across every phase report from Phase 4 onward as a known, deliberately-deferred follow-up.

**Resolution:** the final review pass's explicit Phase 3 revalidation step re-verified the three params live (`curl` against the real backend) and then enabled all three controls — see `FRONTEND_WORK_LOG.md`'s "Final Pre-Production Review Pass — Phase 3 Revalidation" entry for the full change list and re-verified acceptance criteria. No control remains disabled where real backend support exists.

---

## Phase 4 findings

**`GET /analytics/recent-activity` is ticket-only.** `ActivityFeed` only shows `ticket_created` events — call events aren't merged into that endpoint (a real backend gap, documented in the backend's own `DOCS_GAP_REPORT.md` item, not fabricated). The component's docstring states this plainly rather than implying the feed is comprehensive.

**`calls_today` / `escalations` / `escalation-rate` use disclosed, not product-confirmed, window definitions.** These are real numbers, not blocked fields, but the backend's `REMAINING_PRODUCT_DECISIONS.md` flags their exact scope (day-window vs. all-time; terminal-only vs. all calls) as unconfirmed. The frontend renders them as ordinary `ready` KPIs since the backend itself doesn't mark them `blocked` — but a reviewer should know these numbers carry an asterisk. No frontend action needed unless the backend definition changes; noted here for traceability.

**`AIInsightsPreview` shows Category Breakdown only**, by design — it's the only section of `GET /ai-insights` with real data. The four blocked sections (Trending Issues, Repeated Problems, High Risk Alerts, Recommendations) are not referenced anywhere in the Dashboard preview, since a "preview of a blocked feature" would be more confusing than showing nothing for it.

---

---

## Phase 5 findings

**"Issue" column relabeled to "Category".** `WIREFRAMES.md` §5 specifies a call-table column called "Issue" sourced from `collected.short_issue`/`collected.description`. `GET /voice-calls`'s list response only denormalizes `caller_name`/`category`/`priority` out of `collected` — no free-text issue summary is returned at the list level (the full description only exists inside a specific call's `collected` dict via the detail endpoint, and even that isn't guaranteed to be a short label). Rather than fetch every row's detail just to populate one column (defeating the point of a lightweight list endpoint) or fabricate a summary, the column is honestly labeled "Category" and shows the real `category` field. Not a blocker — a disclosed adaptation.

**`LiveCallMonitor` remains unbuilt**, per the implementation plan's own framing of it as a stretch item independent of Calls' core ship date. No blocker — simply out of this phase's required scope.

---

---

## Phase 6 findings

None. All six analytics endpoints matched their documented shapes exactly; no business definition was invented or assumed beyond what the backend already disclosed (`calls_today`/`escalation-rate`'s window definitions, already flagged in the backend's own `REMAINING_PRODUCT_DECISIONS.md`, are surfaced to the user via a tooltip on `EscalationChart` rather than re-litigated here).

## Phase 7 findings

None new. This phase is a direct, disciplined consumption of the backend's own disclosed blocked-state design (Backend Tier 4) — every "missing definition" here was already identified and documented in `REMAINING_PRODUCT_DECISIONS.md` before this phase started; the frontend's job was only to render that honestly, which it does via `BlockedInsightCard`.

## Phase 8 findings

None. This phase's entire design goal was the *absence* of functionality (no write path) rather than new functionality — verified via direct code grep, not just visual review.

---

## Summary across all phases

No product decision, security approval, or missing backend contract stopped any phase. The one cross-phase gap this report tracked — Phase 3's filters being stricter than the backend they talk to — was resolved in the final pre-production review pass. As of that pass, there is no known instance of shipped frontend code being more restrictive than real, available backend support.

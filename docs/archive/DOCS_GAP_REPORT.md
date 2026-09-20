# HFMG AI Help Desk — Documentation Gap Report

**Purpose:** every place a planning document's claim about the backend was checked directly against the running code and found to disagree, across Tiers 0–5. "Fixed" means the document was corrected in Tier 5. "Open" means it's flagged here rather than fixed, with a reason.

Method note: every finding below was verified by reading the actual source (`backend/app/**`) or querying the actual database (`\d tickets`, `psql -l`) — never by trusting one document's description of another.

---

## Fixed in Tier 5

| # | Claim | Reality | Where fixed |
|---|---|---|---|
| 1 | `ix_tickets_fts` "already exists in the schema, unused" (`DATABASE_DESIGN.md`, restated in `WIREFRAMES.md` §7 and `BACKEND_GAP_ANALYSIS.md`) | Does not exist in either the dev or test database. Verified via `\d tickets` on both `hfmg_helpdesk` and `hfmg_helpdesk_test`. | `DATABASE_DESIGN.md` §3.1 indexes block; `API_SPEC.md` §0/§3 |
| 2 | `ix_tickets_status`, `ix_tickets_priority`, `ix_tickets_category_id`, `ix_tickets_assigned_agent_id`, `ix_tickets_email` all "exist" per `DATABASE_DESIGN.md`'s indexes block | None of them exist. Only `ix_tickets_ticket_number` and `ix_tickets_created_at` are real. This went unnoticed until Tier 5's direct DB inspection — every prior tier's analysis had only checked `ix_tickets_fts` specifically. | `DATABASE_DESIGN.md` §3.1 |
| 3 | `voice_call_state_enum` has 13 values including `CREATING_TICKET` and `READ_BACK` (`TWILIO_ARCHITECTURE.md` §6, `CALL_FLOW.md` §2) | The implemented enum (`backend/app/db/models.py`) has 11 values. Those two are narrative steps inline within another transition, never a distinct persisted state. | `TWILIO_ARCHITECTURE.md` §6, `CALL_FLOW.md` §2 |
| 4 | `POST /tickets/{id}/regenerate-summary` "documented" (implying it might already exist) | Did not exist anywhere in the backend — confirmed via `grep -rn regenerate app/` returning nothing, before Tier 0 built it. | Built in Tier 0; `API_SPEC.md` §3 now marks it ✅ with its real `409` behavior documented |
| 5 | `TicketListItem`/`TicketRead` responses — `source` field | Column existed and was correctly populated since Phase 2; the field was simply never added to either Pydantic schema. Not a missing feature, a one-line serialization bug. | Fixed in Tier 0; noted in `API_SPEC.md` §3 |
| 6 | `GET /health/ready` implied to already do a real check (`API_SPEC.md` §9's description) | Endpoint didn't exist at all before Tier 0; `/health` was (and still is, by design) a static stub. | Built in Tier 0; `API_SPEC.md` §9 |
| 7 | `GET /health/dependencies`, `/settings/status`, `/analytics/*`, `/voice-calls/*`, `/ai-insights` — described only in `WIREFRAMES.md`/`FRONTEND_IMPLEMENTATION_PLAN.md`, absent from `API_SPEC.md` entirely | All now built (Tiers 1–4). | Added to `API_SPEC.md` as new §11–§14 |

---

## Open — not fixed in this pass, and why

| # | Document | Stale claim | Why left open |
|---|---|---|---|
| 8 | `WIREFRAMES.md` | Status tags (✅/🔶/⬜) throughout §2 (Dashboard), §5 (Voice Ops), §7 (Analytics), §9 (Settings), §8 (AI Insights) reflect the pre-Tier-0 backend. Several ⬜ items are now ✅ (e.g., §5's "no endpoint exposes it" is no longer true). | This session's instruction was to focus exclusively on backend work. `WIREFRAMES.md` is a frontend planning document; re-tagging it accurately requires re-reading it end-to-end against the new endpoints, which is frontend-adjacent work best done when frontend development resumes (paused per this session). Retagging it now risked scope creep and a rushed, incomplete pass. |
| 9 | `FRONTEND_IMPLEMENTATION_PLAN.md` | Each phase's "API Endpoints" table (Phases 4–8) marks endpoints ⬜/🔶 that are now ✅. | Same reasoning as #8 — this is the frontend build plan, not backend documentation; updating it is naturally paired with resuming frontend Phase 4+, not with backend Tier 5. |
| 10 | `DESIGN.md` §3 | The "Current Implementation Status" table (System Status widget, Dashboard, Calls page, Analytics page, AI Insights page rows) still says "no endpoint reports this" / "no API exposes it" for things Tiers 1–4 built. | Same reasoning — `DESIGN.md` is the frontend/product design source of truth; its status table is meant to be updated alongside the frontend phase that consumes each endpoint, not preemptively from the backend side. |
| 11 | `API_SPEC.md` (pre-existing, unrelated to this engagement) | §5 Users, §7 Audit Log, and the Auth section (§2) describe a full target contract with no implementation status distinction beyond the new §0 table's one-line summary. | Out of scope — these are correctly deferred per `IMPLEMENTATION_PLAN.md`'s MVP Scope Decision, not a documentation error. §0's table already marks them ⬜, which is sufficient; a deeper per-field rewrite of already-correctly-deferred sections wasn't warranted. |

---

## Disclosed (not fabricated) business-definition assumptions made during implementation

These are not documentation *errors* — they're places where a document named a metric without fully specifying it, and a defensible, narrow, disclosed default was chosen so the endpoint could ship. Unlike the items in `REMAINING_PRODUCT_DECISIONS.md`, none of these have multiple genuinely incompatible candidate meanings — they're under-specified in scope (a window boundary), not in kind. Still, they should be confirmed, not assumed permanent.

| Metric | Assumption made | Source document's gap |
|---|---|---|
| `calls_today` / `escalations` (in `/analytics/kpis`) | Day-scoped (`created_at >= start of today UTC`), matching the "Today" framing of sibling KPI cards | `WIREFRAMES.md` §2 lists "Calls Today" and "Escalations" side by side without stating whether "Escalations" means today, all-time, or currently-active |
| `escalation-rate` | `escalated_calls / calls that reached a terminal state (COMPLETED, ESCALATED, ABANDONED)` in the window — in-progress calls excluded | Neither `DESIGN.md` nor `WIREFRAMES.md` states a denominator; `OPERATIONS_RUNBOOK.md` §3.3 was cited as already having "the SQL" for this but wasn't re-derived from verbatim, since that file wasn't in this session's read list |

Both are implemented, tested, and documented inline in `API_SPEC.md` §12 — they are not blocked, but they are marked here so a future product review can confirm or correct the window definition without archaeology.

---

## Addendum — Final Pre-Production Review Pass

Items #8, #9, #10 above (left open at the time, since backend-only sessions correctly deferred frontend-doc updates) were **resolved in this pass**, once the review scope explicitly included documentation modernization across both backend and frontend work:

| # | Document | Resolution |
|---|---|---|
| 8 | `WIREFRAMES.md` | Added a status note pointing to `DESIGN.md` §3 (now accurate) as the current source of truth; ~200 inline per-element tags left as a labeled historical record rather than hand-edited (see `DOCUMENTATION_AUDIT.md` for the reasoning) |
| 9 | `FRONTEND_IMPLEMENTATION_PLAN.md` | Header updated to reflect all 8 phases complete, pointing to `FRONTEND_WORK_LOG.md` |
| 10 | `DESIGN.md` §3 | Fully rewritten against verified current state — every row updated from ⬜ to ✅/🔶 as appropriate |

**Two new, more operationally significant findings** surfaced in this pass (not found in Tier 5, because Tier 5's read list didn't include these two ops-facing documents):

| # | Claim | Reality | Where fixed |
|---|---|---|---|
| 12 | `OPERATIONS_RUNBOOK.md` §3.2: "There is no readiness endpoint... it was never implemented" | `GET /health/ready` is real (Backend Tier 0) | `OPERATIONS_RUNBOOK.md` §3.2 |
| 13 | `DEPLOYMENT_GUIDE.md` §11's Known Gaps table: "No readiness endpoint" | Same — real since Tier 0 | `DEPLOYMENT_GUIDE.md` §11 |

These two are rated more severe than the frontend status-tag staleness (#8–10) because they're operator-facing instructions that would lead a real deployment to skip wiring up a readiness probe that exists — a documentation gap with a concrete operational consequence, not just a stale progress indicator. See `SECURITY_REVIEW.md` and `RELEASE_CHECKLIST.md` for how this is now reflected in deployment guidance.

Full audit trail, including files reviewed and found already accurate: `DOCUMENTATION_AUDIT.md`.

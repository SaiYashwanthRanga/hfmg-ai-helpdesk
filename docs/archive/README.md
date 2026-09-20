# Archive — Historical Documentation

These documents describe **how the HFMG AI Help Desk was planned and built**, not how to operate it today. They're preserved for context and traceability, not deleted, because they explain the reasoning behind decisions still visible in the current codebase — but nothing here should be treated as current instruction. For that, see the root [`README.md`](../../README.md) and the docs it points to.

See [`DOCUMENTATION_RESTRUCTURE_PLAN.md`](../../DOCUMENTATION_RESTRUCTURE_PLAN.md) at the repo root for the full reasoning behind what was kept, archived, or deleted.

## Implementation plans (superseded — the work described is done)
- `IMPLEMENTATION_PLAN.md` — the original backend Phase 1–3 sequencing plan
- `FRONTEND_IMPLEMENTATION_PLAN.md` — the original 8-phase frontend build plan

## Gap analyses and audit artifacts (point-in-time findings)
- `BACKEND_GAP_ANALYSIS.md` — the analysis that drove Backend Tiers 0–5
- `DOCS_GAP_REPORT.md` — documentation-vs-code discrepancies found during backend work
- `FRONTEND_GAP_REPORT.md` — same, frontend side
- `DOCUMENTATION_AUDIT.md` — the final full-repository documentation audit

## Work logs (chronological build history)
- `WORK_LOG.md` — backend, tier by tier
- `FRONTEND_WORK_LOG.md` — frontend, phase by phase

## Design/planning specifications (superseded by the running application)
- `DESIGN_SYSTEM.md` — design tokens and component patterns used to build the frontend design system; the actual Tailwind config and component code are now the source of truth
- `WIREFRAMES.md` — page-by-page wireframes with per-element build-status tags, most now stale (the pages are built — see the live application)
- `COMPONENTS.md` — component contracts written to drive implementation; the actual `frontend/src/components/` tree is now the source of truth

## Why these were archived, not deleted

Every file here has real historical value — understanding *why* a decision was made (e.g., why certain KPIs are computed the way they are, why Settings has no write path) sometimes requires the planning document that made the call, not just the resulting code. Archiving preserves that trail with full git history (`git log --follow <file>` still works) while keeping the active root documentation set small enough to actually read.

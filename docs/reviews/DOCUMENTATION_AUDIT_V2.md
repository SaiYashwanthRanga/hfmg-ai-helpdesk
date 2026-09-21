# Documentation Audit V2

Date: 2026-09-21. Scope: every tracked or new markdown file outside `node_modules` and `.venv` (49 files), audited, then reorganized. `backend/.pytest_cache/README.md` is a generated tool artifact, git-ignored, and excluded. Predecessor: `docs/archive/DOCUMENTATION_AUDIT.md` (first pass, content accuracy) and `docs/archive/DOCUMENTATION_RESTRUCTURE_PLAN.md` (first cleanup, 30 to 18 root files).

**Method.** Each file was opened or its header and scope read, then classified by one question: does a developer, operator, or AI agent need this to work on or run the system today, or is it a dated record? Application code, API contracts, and schemas were not touched. No document was edited beyond link and path fixes, plus the additions listed at the end. Nothing was deleted.

**Categories:** 1 Current Source of Truth, 2 Deployment & Operations, 3 Integration Documentation, 4 Architecture & Design, 5 Product Decisions, 6 Review / Audit Reports, 7 Historical Implementation Artifacts, 8 Obsolete / Redundant.

**Result:** root markdown files 35 to 17. No file classified 8 (nothing was completely redundant).

## Root (kept in place)

| File | Cat | Action | Reasoning |
|---|---|---|---|
| README.md | 1 | Keep, updated | Entry point; gained a Documentation Map |
| API_SPEC.md | 1 | Keep | API contract; section 0 is a verified status table |
| DATABASE_DESIGN.md | 1 | Keep | Schema reference needed for any migration |
| FINAL_PROJECT_STATUS.md | 1 | Keep | Single "state of the project" record |
| TECHNICAL_DEBT.md | 1 | Keep (see deviation) | Living list of accepted tradeoffs, each with a fix trigger |
| ARCHITECTURE.md | 4 | Keep | Current architecture and MVP scope decisions |
| DESIGN.md | 4 | Keep | Product/UI source of truth with per-page status |
| DEPLOYMENT_GUIDE.md | 2 | Keep | Required to deploy |
| OPERATIONS_RUNBOOK.md | 2 | Keep | Monitoring, backup, recovery, troubleshooting |
| RELEASE_CHECKLIST.md | 2 | Keep (see deviation) | Reused on every deploy; actionable, not historical |
| SENDGRID_SETUP.md | 3 | Keep | Configuration for a live integration |
| TWILIO_SETUP.md | 3 | Keep | Configuration for a live integration |
| TWILIO_ARCHITECTURE.md | 4 | Keep | Architecture of the implemented voice flow |
| CALL_FLOW.md | 4 | Keep | The implemented conversation state machine |
| VOICE_AGENT_DESIGN.md | 4 | Keep | Prompts, NLU, classification as implemented |
| KNOWN_LIMITATIONS.md | 5 | Keep | Stakeholder-facing limits, current |
| REMAINING_PRODUCT_DECISIONS.md | 5 | Keep | Open business decisions blocking features |
| backend/README.md | 1 | Keep | Backend setup |
| frontend/README.md | 1 | Keep | Frontend setup |

## Moved to docs/reviews/ (git mv; 16 files)

Dated audits and assessments. Each records a date and method, and some findings have since been fixed, so they inform but do not instruct.

| File | Cat | Action | Reasoning |
|---|---|---|---|
| EXECUTIVE_RECOMMENDATIONS.md | 6 | Moved | Latest top-5 issues, improvements, risks |
| EXECUTIVE_SUMMARY.md | 6 | Moved | Summary of the eight-part production readiness review |
| AI_FAILURE_ANALYSIS.md | 6 | Moved | Root cause and fix of summary failures |
| AI_QUALITY_REVIEW.md | 6 | Moved | Earlier summary-pipeline quality review |
| OPENAI_PROMPT_REVIEW.md | 6 | Moved | Prompt findings and A/B evidence |
| SECURITY_REVIEW.md | 6 | Moved | First security pass. Was at root; a review, so moved. Still the reference for security posture, linked from README |
| SECURITY_REVALIDATION.md | 6 | Moved | Second security pass |
| PRODUCTION_HARDENING_REPORT.md | 6 | Moved | Hardening review |
| OBSERVABILITY_REVIEW.md | 6 | Moved | Logging and monitoring review |
| PERFORMANCE_REVIEW.md | 6 | Moved | Performance review |
| TEST_COVERAGE_REVIEW.md | 6 | Moved | Test coverage review |
| SYSTEM_VALIDATION_REPORT.md | 6 | Moved | End-to-end validation |
| UX_REVIEW.md | 6 | Moved | Frontend UX review |
| UI_POLISH_REPORT.md | 6 | Moved | UI polish findings and fixes |
| TWILIO_READINESS_CHECK.md | 6 | Moved | Latest voice readiness classification |
| TWILIO_READINESS_REPORT.md | 6 | Moved | Earlier readiness report with the go-live checklist; overlaps the CHECK but has unique checklist content, so kept |
| DOCUMENTATION_AUDIT_V2.md | 6 | New | This file |

## Moved to docs/integrations/ (1 file)

| File | Cat | Action | Reasoning |
|---|---|---|---|
| OPENAI_INTEGRATION_REPORT.md | 3 | Moved | The only OpenAI integration document. Point-in-time verification, so a note was added at its top pointing to the later failure analysis |

## docs/archive/ (already archived; verified)

| File | Cat | Action | Reasoning |
|---|---|---|---|
| IMPLEMENTATION_PLAN.md | 7 | Verified archived | Original backend sequencing plan; work done |
| FRONTEND_IMPLEMENTATION_PLAN.md | 7 | Verified archived | Original frontend build plan; work done |
| BACKEND_GAP_ANALYSIS.md | 7 | Verified archived | Drove backend tiers 0 to 5 |
| DOCS_GAP_REPORT.md | 7 | Verified archived | Doc-vs-code discrepancy log |
| FRONTEND_GAP_REPORT.md | 7 | Verified archived | Same, frontend |
| DOCUMENTATION_AUDIT.md | 7 | Verified archived | First audit; superseded by V2 |
| WORK_LOG.md | 7 | Verified archived | Backend build log |
| FRONTEND_WORK_LOG.md | 7 | Verified archived | Frontend build log |
| WIREFRAMES.md | 7 | Verified archived | Superseded by the running app |
| COMPONENTS.md | 7 | Verified archived | Superseded by `frontend/src/components/` |
| DESIGN_SYSTEM.md | 7 | Verified archived | Superseded by Tailwind config and components |
| README.md (archive) | 7 | Verified, updated | Archive index; link fixed |
| DOCUMENTATION_RESTRUCTURE_PLAN.md | 7 | **Archived now** | Planning document for the first cleanup; was at root |

## New

| File | Cat | Purpose |
|---|---|---|
| docs/DOCUMENTATION_INDEX.md | 1 | Nine-section map with purpose, audience, and when to read |

## Deviations from the proposed target structure

1. **RELEASE_CHECKLIST.md and TECHNICAL_DEBT.md remain at root.** The target list omits them, but both are living, actively maintained documents referenced from README and the deployment docs. Archiving would hide live guidance; moving them under `docs/reviews/` would mislabel them as dated reports. They can be moved if you prefer a strict 15-file root.
2. **SECURITY_REVIEW.md moved to docs/reviews/** although the first cleanup kept it at root. It is a dated audit; README links to it directly, so it stays discoverable.
3. **docs/integrations/ holds one file.** TWILIO_SETUP.md and SENDGRID_SETUP.md stay at root as the target specifies.

## Link and reference fixes

- Every markdown hyperlink to a moved file was rewritten (README to SECURITY_REVIEW; archive README to the restructure plan). Links between files that moved together (for example EXECUTIVE_SUMMARY to the other reviews) stayed valid because they share a folder.
- 60 prose citations of moved or archived files in 13 root and sub-project docs were rewritten to full paths (for example `WORK_LOG.md` to `docs/archive/WORK_LOG.md`). Pattern-matched, verified by dry run, line endings preserved.
- A relative-link check over all 51 markdown files (124 links) found 3 broken links, all pre-existing in `docs/archive/IMPLEMENTATION_PLAN.md` (left over from the first archive move); they were fixed. The re-run reports 0 broken links.

## Remaining concerns

1. **Stale content, not fixed (outside scope).** `TECHNICAL_DEBT.md` and `DATABASE_DESIGN.md` still list `ix_tickets_category_id` as unbuilt, but migration `dd3a82a4a05a` adds it. `docs/reviews/PRODUCTION_HARDENING_REPORT.md` finding 3 (summarizer has no try/except) and parts of `AI_QUALITY_REVIEW.md` and `EXECUTIVE_SUMMARY.md` describe behavior that has since been fixed; they are dated reports and say so.
2. **Code comments cite archived docs.** About 115 references in `backend/` and `frontend/src/` reference `WIREFRAMES.md`, `DESIGN_SYSTEM.md`, `*_GAP_*`, `IMPLEMENTATION_PLAN.md`, and `WORK_LOG.md` by bare name. They were not edited (application code was out of scope); the files are in `docs/archive/`.
3. **Bare filenames inside archived and review documents** mean root files. Explained in the index and README rather than by editing historical text.
4. **Two overlapping Twilio readiness reports** are both kept; the CHECK is newer, the REPORT holds the go-live checklist. Consider merging the checklist into `TWILIO_SETUP.md` later.

# HFMG AI Help Desk — Documentation Restructure Plan

**Goal:** reduce documentation noise (30 root-level markdown files) down to the set actually required for long-term maintenance, deployment, and onboarding, while preserving every historical artifact that has genuine reference value.

**Method:** every file was read (or re-confirmed from this session's own prior work) and classified by asking one question — *does a new engineer, an operator, or a future maintainer need this to do their job today, or is it a record of how we got here?* The former is kept in the active root; the latter moves to `docs/archive/`, not deleted, per the instruction to archive rather than delete under uncertainty.

**Archive mechanism:** `git mv` into a new `docs/archive/` directory, preserving full git history on every file (`git log --follow` still works). Cross-references to archived files elsewhere in kept docs are almost all plain prose citations (e.g. `` `WORK_LOG.md` ``), not clickable relative links — grep confirmed exactly **one** real markdown hyperlink to an archive candidate in the entire repo (README.md's own doc index, which is being rewritten anyway). Rather than hand-editing dozens of prose citations across a dozen files to prepend `docs/archive/`, a single pointer note in the new README and a `docs/archive/README.md` index handles discoverability — lower risk than a wide, mechanical find-replace across files whose prose might not tolerate it cleanly.

---

## Files to Keep (18 root + 2 sub-project READMEs)

| File | Reasoning |
|---|---|
| `README.md` | Entry point — rewritten as part of this task |
| `ARCHITECTURE.md` | Current system architecture; still the accurate target/actual split after Tier 5's correction |
| `DESIGN.md` | Product/UI design source of truth; its §3 status table was brought current in the last review pass |
| `API_SPEC.md` | The API contract — actively required to integrate with or extend the backend |
| `DATABASE_DESIGN.md` | Schema documentation — actively required for any migration or query work |
| `DEPLOYMENT_GUIDE.md` | How to actually deploy this system — required, not historical |
| `OPERATIONS_RUNBOOK.md` | Monitoring, alerting, backup/DR, troubleshooting — required for anyone operating this in production |
| `SECURITY_REVIEW.md` | Current security posture and verification method — required reading before any production change touching auth, secrets, or Settings |
| `TECHNICAL_DEBT.md` | Live-tracked engineering tradeoffs with stated trigger conditions — exactly the kind of document long-term maintenance needs, not a one-time artifact |
| `RELEASE_CHECKLIST.md` | Actionable pre-deploy checklist, reused on every future deployment — operational, not historical |
| `KNOWN_LIMITATIONS.md` | What the product doesn't do yet, for stakeholders — current and load-bearing |
| `REMAINING_PRODUCT_DECISIONS.md` | Open business decisions still blocking specific features — current and load-bearing until each is resolved |
| `FINAL_PROJECT_STATUS.md` | The consolidated go/no-go and feature-complete record — the single "state of the project" document |
| `TWILIO_SETUP.md` | Operational configuration guide for a live, running integration — needed whenever the Twilio number/webhooks are reconfigured |
| `SENDGRID_SETUP.md` | Operational configuration guide for a live, running integration — same reasoning |
| `TWILIO_ARCHITECTURE.md` | Describes the *currently running* voice integration's architecture (not a proposal) — needed to safely modify the voice webhook flow |
| `CALL_FLOW.md` | The *currently running* conversation state machine, corrected against the real 11-state enum in the last review pass — needed to debug or extend call behavior |
| `VOICE_AGENT_DESIGN.md` | The *currently running* prompts/NLU/classification design — needed to safely change what the voice agent says or how it classifies |
| `backend/README.md`, `frontend/README.md` | Standard per-project setup instructions, unaffected by this restructure |

**Why the voice-specific documents (`TWILIO_SETUP`, `SENDGRID_SETUP`, `TWILIO_ARCHITECTURE`, `CALL_FLOW`, `VOICE_AGENT_DESIGN`) are kept despite not being named in the task's explicit Keep category list:** the task's categories ("current architecture," "deployment documentation," etc.) are categories, not a literal enumeration — these five documents describe currently-running, load-bearing subsystems, not historical planning. Archiving them would remove documentation for a live feature, which is the opposite of this task's goal.

---

## Files to Archive (11 files → `docs/archive/`)

| File | Reasoning |
|---|---|
| `IMPLEMENTATION_PLAN.md` | The original backend Phase 1–3 sequencing plan — historical; Phases 1–2 are done, Phase 3 (auth/queue/Docker) is tracked as a forward-looking item in `REMAINING_PRODUCT_DECISIONS.md`/`TECHNICAL_DEBT.md` instead |
| `FRONTEND_IMPLEMENTATION_PLAN.md` | The original 8-phase frontend build plan — historical; all 8 phases are complete, and `FINAL_PROJECT_STATUS.md` is the current summary |
| `BACKEND_GAP_ANALYSIS.md` | A point-in-time gap analysis that drove Backend Tiers 0–5 — historical planning artifact, its findings are either fixed (reflected in `API_SPEC.md`) or carried into `REMAINING_PRODUCT_DECISIONS.md` |
| `DOCS_GAP_REPORT.md` | A running log of documentation-vs-code discrepancies found during backend work — historical audit artifact, superseded as a reference by `DOCUMENTATION_AUDIT.md`'s more complete pass |
| `FRONTEND_GAP_REPORT.md` | Same category, frontend side — historical audit artifact |
| `DOCUMENTATION_AUDIT.md` | The final documentation audit's findings — valuable record of what was found and fixed, but a one-time review artifact, not something a maintainer consults routinely |
| `WORK_LOG.md` | Backend chronological build log — valuable history, zero day-to-day utility once the work it describes is done |
| `FRONTEND_WORK_LOG.md` | Same, frontend side |
| `DESIGN_SYSTEM.md` | Design-token/component-pattern specification written to drive frontend implementation — now superseded as ground truth by the actual Tailwind config and component code; still a good historical reference for *why* a token has a given value |
| `WIREFRAMES.md` | Page-by-page wireframe planning document with extensive historical per-element status tags (already flagged as stale in the last review pass) — superseded by the running application itself |
| `COMPONENTS.md` | Component contract specification (1,124 lines, the largest doc in the repo) written to drive implementation — superseded by the actual `frontend/src/components/` tree, which is now the source of truth for real props/structure |

---

## Files to Delete (1 file)

| File | Reasoning |
|---|---|
| `IMPLEMENTATION_SUMMARY.md` | Written as "one read for a new engineer or stakeholder" — but that is now exactly README.md's job (this task explicitly requires README to serve that role). Its two genuinely useful, non-duplicated pieces (the architecture diagram and the "how to verify any claim" method note) were folded into the rewritten `README.md` before deletion, so no unique content is lost. Confirmed via `grep` that no other file links to it (zero incoming references) before deleting. This is the one file in the repository whose entire purpose is now fully absorbed elsewhere — the bar this task sets for Delete rather than Archive. |

---

## Net Effect

- **Root markdown files: 30 → 18** (a 40% reduction), plus a `docs/archive/README.md` index for the 11 archived files.
- **Nothing is lost** — every archived file keeps its full git history via `git mv`, and `IMPLEMENTATION_SUMMARY.md`'s unique content is preserved in `README.md` before its deletion.
- **The 5-file onboarding path this task requires** (`README.md` → `ARCHITECTURE.md` → `API_SPEC.md` → `DATABASE_DESIGN.md` → `DEPLOYMENT_GUIDE.md`) is now genuinely sufficient on its own — none of those five documents depend on an archived file to make sense, since each was written (or is being rewritten, for README) to stand alone with pointers to *further* reading, not *required* reading.

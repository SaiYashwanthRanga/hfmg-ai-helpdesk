# System Validation Report

Date: 2026-09-21. Method: end-to-end code review of each flow (backend and frontend contract), plus live runs against the real database and OpenAI for the AI path. **I did not drive the UI in a browser and did not exercise the deployed environment**, so "OK" below means code and contract consistency plus the tests noted, not click-through verification.

## Results

| Flow | Result | Basis |
|---|---|---|
| Ticket creation | OK | `TicketCreate` (`schemas/ticket.py`) and `NewTicketPage.validate()` check the same required fields; backend also rejects whitespace-only text and unknown/inactive categories. AI summary runs as a background task; frontend polls every 3s while `PENDING` |
| Ticket status updates | OK | Backend enforces `VALID_STATUS_TRANSITIONS` (422 otherwise); frontend `types/ticket.ts` mirrors it exactly and only offers valid next states |
| AI summary generation | **BUG found and fixed** | See AI_FAILURE_ANALYSIS.md. Contract fields match (`ai_summary`, `ai_summary_status`, `ai_summary_generated_at`, enum values) |
| Dashboard KPIs | OK | Counts are UTC-based; divide-by-zero guarded in escalation rate and AI usage; "AI Resolution Rate" is deliberately reported as `blocked` with a reason rather than invented |
| Analytics | OK | All charts share one `days` param (1-365); frontend types match backend schemas; zero-count buckets filled |
| AI Insights | OK (mostly by design) | Only category breakdown has data; the other four sections return an explicit `blocked_reason`. No OpenAI dependency |
| Settings | OK | Single read-only `GET /settings/status`; keys pass through `mask_secret()`; no write endpoint |
| Voice/Twilio | See TWILIO_READINESS_CHECK.md | Code complete; NLU timeout bug fixed |

## Verified by execution

- Backend tests: **116 passed** after changes.
- Frontend: `tsc` clean, `vite build` succeeds; `oxlint` only pre-existing fast-refresh warnings.
- Live: summary status cycle `PENDING -> COMPLETED -> PENDING -> COMPLETED` with real OpenAI (2.4-3.3s per call).
- Live: voice NLU under 4s budget (1.8s and 2.7s).

## Stale documentation

- Reviewers found no doc claiming behavior the code lacks. `API_SPEC.md` discloses that search uses `ILIKE`, not full-text search; `README.md` states AI Resolution Rate and four AI Insights sections are not built.
- **Now stale because of my change**: any text saying an unconfigured provider leaves a ticket `PENDING`, and PRODUCTION_HARDENING_REPORT.md finding 3 ("no try/except in summarizer", recommended-not-fixed) which is now fixed. I did not edit those existing reports.
- AI_QUALITY_REVIEW.md describes a 27 ms failure that I could not reproduce (see AI_FAILURE_ANALYSIS.md).
- I did not read every root-level .md file end to end; other documents may still carry stale details.

## Remaining production risks

1. PII/PHI is sent to OpenAI unredacted (decision needed).
2. No auth on the API/settings (documented in the code as an existing constraint; I did not review this further).
3. Twilio health tile only checks token presence.
4. Regenerate race and no dedupe (low impact).
5. Deployed environment not inspected: confirm it runs the fixed code and does not override `OPENAI_REASONING_EFFORT` with a high value.

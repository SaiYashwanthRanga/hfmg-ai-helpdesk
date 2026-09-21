# HFMG AI Help Desk — Technical Debt

Every item here is a known, accepted tradeoff — not a bug. Each has an owner-facing "why this is acceptable today" and a trigger condition for when it stops being acceptable.

---

## Database

| Item | Impact | Trigger to fix |
|---|---|---|
| `tickets` table is missing 6 of 7 documented indexes (`ix_tickets_status`, `ix_tickets_priority`, `ix_tickets_category_id`, `ix_tickets_email`, plus the never-built `ix_tickets_fts`) — only `ix_tickets_ticket_number` and `ix_tickets_created_at` actually exist (verified via `\d tickets` on both dev and test databases) | Every ticket list/filter/search query is a full table scan | `DATABASE_DESIGN.md` §6 sizes this system for "low thousands of tickets/year" — fine until real volume approaches ~10k+ rows. Revisit if list/filter latency becomes noticeable |
| `q` search uses `ILIKE`, not the full-text (`to_tsvector`/GIN) index `API_SPEC.md` originally described | Slightly worse search relevance (no stemming/ranking); same table-scan cost as above | Same trigger — add the real migration once volume or search-quality complaints justify it |
| No `tickets.transcript` column — phone-ticket transcripts are concatenated into `description` | `TicketDrawer`'s transcript section can't cleanly separate transcript from complaint text; fragile to parse | This is flagged as an open decision, not pure debt — see `REMAINING_PRODUCT_DECISIONS.md`-adjacent note in `docs/archive/WIREFRAMES.md` §8. Needs a small schema decision, not urgent |
| No `tickets.ai_model` column | Can't audit which model produced a given summary | Low priority; add alongside any future prompt/model-versioning work |
| `voice_call_sessions` stores only the *current* state, no history | `CallTimeline` is permanently blocked (by design, not a bug) | Would need either a history table or an append-only JSONB column on every orchestrator transition — a real, scoped feature, not a quick fix |
| No `audit_log`, `ticket_comments`, `ticket_attachments`, `notification_log`, `users` tables | Correctly deferred to backend Phase 3 (auth) per `docs/archive/IMPLEMENTATION_PLAN.md`'s MVP Scope Decision | Phase 3 backend auth landing |

## Backend Services

| Item | Impact | Trigger to fix |
|---|---|---|
| Twilio dependency-health check is configuration-only (checks `TWILIO_AUTH_TOKEN` is set, doesn't call Twilio's API) | `/health/dependencies`'s Twilio status can read "operational" when the token is set but actually invalid/expired | Add `TWILIO_ACCOUNT_SID` to config and a real Basic-Auth call to Twilio's REST API when this distinction starts mattering operationally |
| `degraded` status value exists in the health-check schema but is never emitted — only `operational`/`down` | Slightly less granular status than the schema implies | Would need a real signal (e.g. elevated latency threshold) to justify emitting it rather than fabricating a threshold |
| `GET /analytics/recent-activity` only returns ticket-created events, not call events | Dashboard's Activity Feed is incomplete as a "everything that happened" view | A real, scoped feature: merge `voice_call_sessions` into the union query. Not started because it wasn't in Tier 3's six named aggregation endpoints |
| No retry/backoff for failed email sends (`BackgroundTasks`, fire-and-forget) | A transient SendGrid failure silently drops a notification | Accepted per `docs/archive/IMPLEMENTATION_PLAN.md`'s risk register at MVP volume; Phase 3's queue (Redis/Celery) adds durability when it lands |
| Logs are plain text, not structured JSON | Harder to query in a log aggregator at scale | `DEPLOYMENT_GUIDE.md` §11 already tracks this as a Phase 3 item |
| No rate limiting implemented anywhere (documented as target-only in `API_SPEC.md`) | Public-facing endpoints (`POST /tickets`, the Twilio webhooks) have no throughput cap beyond Twilio's own signature-gate | Needed before any real internet-facing traffic, not just MVP internal-network use — see `RELEASE_CHECKLIST.md` |

## Frontend

| Item | Impact | Trigger to fix |
|---|---|---|
| Production JS bundle is ~950KB minified (Recharts + Framer Motion + the full app in one chunk), above Vite's 500KB warning threshold | Slightly slower initial load on a cold cache | Not a correctness issue. Fix via route-based code splitting (`React.lazy` per page) whenever load-time becomes a real user complaint — internal ops tool, not a public marketing site, so this is genuinely low priority |
| `LiveCallMonitor` (`/calls/live/:callId`) remains the Phase 2 placeholder | No live in-progress-call view | Explicitly scoped as an optional stretch item in `docs/archive/FRONTEND_IMPLEMENTATION_PLAN.md` Phase 5 — build when there's a concrete need for real-time call monitoring, and decide the polling-vs-push architecture question first |
| No client-side caching invalidation strategy documented beyond TanStack Query defaults | Minor risk of briefly stale data after a mutation in an edge case not covered by existing `invalidateQueries` calls | Low risk; revisit if a specific stale-data bug is ever reported |

## Cross-Cutting

| Item | Impact | Trigger to fix |
|---|---|---|
| Two "disclosed assumption" metrics (`calls_today`/`escalations`' day-scoping, `escalation-rate`'s terminal-only denominator) ship as real numbers without product sign-off on their exact definition | Low — the numbers are reasonable and clearly documented, but could need adjustment if product disagrees with the window chosen | See `REMAINING_PRODUCT_DECISIONS.md`'s "Disclosed assumptions" section — resolve whenever product reviews the Analytics page |

None of the above is rated Critical or High in `RELEASE_CHECKLIST.md` — every item here has a working, honest fallback today (a slow query still returns correct data; a blocked feature renders its blocked state; a placeholder page says so plainly).

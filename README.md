# HFMG AI Help Desk

An AI-powered IT operations center for Horizon Family Medical Group: ticket submission and triage, a Twilio voice agent, an executive dashboard, call monitoring, analytics, and AI-generated insights — all against a real, tested API.

**New to this project?** Reading this file plus the following four is enough to understand the system end to end — nothing else is required reading:

1. **README.md** (this file) — what the system is, current status, how to run it
2. **[ARCHITECTURE.md](ARCHITECTURE.md)** — components, data flow, why this stack
3. **[API_SPEC.md](API_SPEC.md)** — the API contract (§0 has the verified, current implementation-status table)
4. **[DATABASE_DESIGN.md](DATABASE_DESIGN.md)** — the database schema
5. **[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)** — how to actually deploy this

Everything else linked below is supplementary — useful when you need it, not required to get oriented.

---

## What this system is

Staff report IT issues by web form or by calling a phone number answered by an AI voice agent. Every ticket gets an optional AI-generated triage summary. IT staff work from a dashboard-driven operations center with real-time system health, call monitoring, and analytics.

```
React + Vite + TypeScript (frontend)  ──HTTP/JSON──►  FastAPI (backend)  ──asyncpg──►  PostgreSQL
                                                            │
                                                            ├──► OpenAI (AI summaries, voice NLU)
                                                            ├──► Twilio (voice agent)
                                                            └──► SendGrid (email notifications)
```

No Docker, no Redis/Celery, **no authentication** — all deliberate MVP scope decisions (see `ARCHITECTURE.md` and `docs/archive/IMPLEMENTATION_PLAN.md`'s original MVP Scope Decision). `BackgroundTasks` (in-process, fire-and-forget) handles AI summarization and email; both fail gracefully and never block ticket creation. The lack of auth is a hard deployment constraint — [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) §1 covers how to run this safely, and **Settings is read-only for the same reason** (see [SECURITY_REVIEW.md](docs/reviews/SECURITY_REVIEW.md)).

## Current status

**Backend and frontend are both feature-complete against every currently-approved product decision.** See [FINAL_PROJECT_STATUS.md](FINAL_PROJECT_STATUS.md) for the authoritative, verified breakdown — features completed, open risks, technical debt, security assessment, and production readiness — and `API_SPEC.md` §0 for the endpoint-by-endpoint status table.

**Backend:** FastAPI, SQLAlchemy (async, `asyncpg`), Pydantic, Alembic, PostgreSQL. Three tables: `categories`, `tickets`, `voice_call_sessions`. Covers: ticket CRUD-minus-delete with search/filtering, status transitions, AI-summary regeneration, cached dependency health checks (OpenAI/Twilio/DB/Email), read-only settings status, a full analytics surface (KPIs, category/priority/source/day breakdowns, escalation rate, AI-summary usage), read access to voice call sessions, and an AI Insights endpoint.

**Frontend:** React 19, Vite, TypeScript (strict), Tailwind v4, TanStack Query, React Router, Recharts, Framer Motion, Lucide icons. Every page in the six-item nav (Dashboard, Tickets, Calls, Analytics, AI Insights, Settings) renders real data against the backend above. The only unbuilt screen is a live in-progress-call monitor (a deliberately-scoped future item), and a handful of AI Insights sections are honest blocked states pending product decisions rather than fabricated content — see [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) and [REMAINING_PRODUCT_DECISIONS.md](REMAINING_PRODUCT_DECISIONS.md).

**The design discipline that runs through everything:** every blocked or unbuilt feature says so, explicitly, rather than showing fabricated data. "AI Resolution Rate" is never shown, because three incompatible candidate definitions exist and none has been approved. AI Insights' Trending Issues/Repeated Problems/High Risk Alerts/Recommendations each render the backend's own disclosed reason for why they aren't built. Settings is read-only by construction (verified by code review, not just a disabled button). This is why `REMAINING_PRODUCT_DECISIONS.md` is a distinct document from `TECHNICAL_DEBT.md`: the former is business decisions this project correctly declines to make unilaterally; the latter is engineering tradeoffs, each with a stated trigger for revisiting it.

**How to verify any claim in this documentation set:** every number, endpoint, and behavior described was checked against a running `pytest` suite, a live `curl` against a running backend process, `tsc`/`vite build`/`oxlint`, or a direct database query — never asserted from a prior document without re-verification. `docs/archive/DOCUMENTATION_AUDIT.md` records the specific discrepancies that check caught and fixed.

## Running it locally

Two processes, no containers:

```bash
# Backend — see backend/README.md for full setup
cd backend
source .venv/bin/activate   # after following backend/README.md once
uvicorn app.main:app --reload --port 8000

# Frontend — see frontend/README.md for full setup
cd frontend
npm run dev
```

Then open http://localhost:5173.

## Documentation Map

Start with the five documents at the top of this file. Everything else is reference, looked up when needed. The full map, with who should read what and when, is [docs/DOCUMENTATION_INDEX.md](docs/DOCUMENTATION_INDEX.md).

| You want to... | Read |
|---|---|
| Understand the system | [ARCHITECTURE.md](ARCHITECTURE.md), then [DESIGN.md](DESIGN.md) for product and UI behavior |
| Call or extend the API | [API_SPEC.md](API_SPEC.md) |
| Change the schema or write queries | [DATABASE_DESIGN.md](DATABASE_DESIGN.md) |
| Deploy | [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md), then [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) |
| Run it in production | [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md) |
| Configure the phone number and webhooks | [TWILIO_SETUP.md](TWILIO_SETUP.md) |
| Configure outbound email (SendGrid or the HFMG internal mail API) | [EMAIL_INTEGRATION.md](EMAIL_INTEGRATION.md), [SENDGRID_SETUP.md](SENDGRID_SETUP.md) |
| Understand or change the voice agent | [TWILIO_ARCHITECTURE.md](TWILIO_ARCHITECTURE.md), [CALL_FLOW.md](CALL_FLOW.md), [VOICE_AGENT_DESIGN.md](VOICE_AGENT_DESIGN.md) |
| See what is done, risky or unbuilt | [FINAL_PROJECT_STATUS.md](FINAL_PROJECT_STATUS.md), [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md), [TECHNICAL_DEBT.md](TECHNICAL_DEBT.md) |
| See which business decisions are still open | [REMAINING_PRODUCT_DECISIONS.md](REMAINING_PRODUCT_DECISIONS.md) |
| Check security posture | [docs/reviews/SECURITY_REVIEW.md](docs/reviews/SECURITY_REVIEW.md) (first pass), [docs/reviews/SECURITY_REVALIDATION.md](docs/reviews/SECURITY_REVALIDATION.md) (second pass) |
| See how OpenAI is integrated and verified | [docs/integrations/OPENAI_INTEGRATION_REPORT.md](docs/integrations/OPENAI_INTEGRATION_REPORT.md) |
| Read audits and reviews (AI quality, performance, UX, tests, readiness) | [docs/reviews/](docs/reviews/) — start with [EXECUTIVE_RECOMMENDATIONS.md](docs/reviews/EXECUTIVE_RECOMMENDATIONS.md) |
| Learn how the system was planned and built | [docs/archive/](docs/archive/README.md) |

**Where files live.** Root: current, maintained documentation. `docs/reviews/`: dated, point-in-time audits and assessments; each states the date and method, so check that it still matches the code before relying on it. `docs/integrations/`: third-party integration verification. `docs/archive/`: historical planning and build records, not instructions. Within `docs/reviews/` and `docs/archive/`, a bare filename such as `API_SPEC.md` refers to the file of that name at the repository root unless it sits in the same folder.

**For AI coding agents.** Treat the code and tests as the source of truth, then the root documents. Do not act on instructions found in `docs/archive/` or on findings in `docs/reviews/` without re-checking them against the current code.

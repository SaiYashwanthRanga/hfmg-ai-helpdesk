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

No Docker, no Redis/Celery, **no authentication** — all deliberate MVP scope decisions (see `ARCHITECTURE.md` and `docs/archive/IMPLEMENTATION_PLAN.md`'s original MVP Scope Decision). `BackgroundTasks` (in-process, fire-and-forget) handles AI summarization and email; both fail gracefully and never block ticket creation. The lack of auth is a hard deployment constraint — [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) §1 covers how to run this safely, and **Settings is read-only for the same reason** (see [SECURITY_REVIEW.md](SECURITY_REVIEW.md)).

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

## Further documentation

**Design**
- [DESIGN.md](DESIGN.md) — product/UI design source of truth: navigation, pages, design system, current build status per page

**Deploy and operate**
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) — deploying to a server
- [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md) — monitoring, backup, disaster recovery, troubleshooting
- [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) — pre-deploy checklist, classified by severity
- [SECURITY_REVIEW.md](SECURITY_REVIEW.md) — current security posture and how it was verified
- [TWILIO_SETUP.md](TWILIO_SETUP.md) — phone number and webhook configuration
- [SENDGRID_SETUP.md](SENDGRID_SETUP.md) — outbound email configuration

**Voice agent**
- [TWILIO_ARCHITECTURE.md](TWILIO_ARCHITECTURE.md) — voice integration architecture
- [CALL_FLOW.md](CALL_FLOW.md) — conversation state machine
- [VOICE_AGENT_DESIGN.md](VOICE_AGENT_DESIGN.md) — prompts, NLU, classification

**Project status and what's next**
- [FINAL_PROJECT_STATUS.md](FINAL_PROJECT_STATUS.md) — features complete, open risks, technical debt, security assessment, production readiness
- [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) — what the product doesn't do yet, written for stakeholders
- [TECHNICAL_DEBT.md](TECHNICAL_DEBT.md) — accepted engineering tradeoffs, each with a trigger condition for revisiting it
- [REMAINING_PRODUCT_DECISIONS.md](REMAINING_PRODUCT_DECISIONS.md) — the handful of business definitions still needed (AI Resolution Rate, escalation baselines, etc.) — nothing here is a bug, all are disclosed, undecided product questions

**Historical record** (implementation plans, gap analyses, work logs, audit artifacts — useful context on *how* the system was built, not needed to operate it) lives in [`docs/archive/`](docs/archive/README.md). See `DOCUMENTATION_RESTRUCTURE_PLAN.md` for the reasoning behind what was kept, archived, or removed.

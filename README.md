# HFMG AI Help Desk

An IT help desk system for Horizon Family Medical Group: ticket submission, triage, AI-generated summaries, and email notification to `helpdesk@hfmg.net`.

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — target production system design
- [DATABASE_DESIGN.md](DATABASE_DESIGN.md) — target production schema
- [API_SPEC.md](API_SPEC.md) — target production API contract
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — phased plan, **including the current MVP scope** (what's built now vs. deferred to later phases)

## Current status: Phase 1 MVP

The code in `backend/` and `frontend/` implements the MVP described in `IMPLEMENTATION_PLAN.md`: ticket creation, listing, status updates, email notification (log-only without SMTP configured), an optional AI summary, and a React dashboard. No Docker, no Redis, no auth — see that plan's "MVP Scope Decision" section for what's intentionally deferred and why.

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

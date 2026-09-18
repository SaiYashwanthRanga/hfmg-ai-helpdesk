# HFMG AI Help Desk — Backend (MVP)

FastAPI backend. No Docker, no Redis, no auth — see `IMPLEMENTATION_PLAN.md` in the repo root for what's deferred and why.

## Prerequisites

- Python 3.11–3.12 (3.14 currently fails to build `pydantic-core`/`asyncpg` from source via PyO3 — use 3.12: `brew install python@3.12`)
- A local PostgreSQL server (`brew install postgresql@16 && brew services start postgresql@16`)

## Setup

```bash
# 1. Create the dev database (once)
createdb -O "$(whoami)" hfmg_helpdesk   # or see below for a dedicated role

# Or, to match .env.example exactly, create a dedicated role + database:
psql -d postgres -c "CREATE ROLE hfmg WITH LOGIN PASSWORD 'hfmg_dev_local';"
createdb -O hfmg hfmg_helpdesk

# 2. Python environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# edit .env if your DB credentials differ, or to enable SMTP / AI summaries

# 4. Migrate + seed
alembic upgrade head
python seed.py

# 5. Run
uvicorn app.main:app --reload --port 8000
```

API docs (interactive): http://localhost:8000/docs

## Running tests

Tests run against a **separate** database — never point them at your dev database, since the test suite truncates tables between tests.

```bash
createdb -O hfmg hfmg_helpdesk_test
DATABASE_URL="postgresql+asyncpg://hfmg:hfmg_dev_local@localhost:5432/hfmg_helpdesk_test" pytest
```

## Notes

- **Email**: if `SMTP_HOST` is left empty in `.env`, outbound notifications are logged instead of sent, so the app runs end-to-end without a mail server. Point `SMTP_HOST`/`SMTP_USERNAME`/`SMTP_PASSWORD` at a real relay (or a local one like Mailhog) to see real delivery.
- **AI summaries**: off by default (`ENABLE_AI_SUMMARY=false`). Set it to `true` and provide `ANTHROPIC_API_KEY` to turn them on; tickets work identically either way — `ai_summary_status` is `DISABLED` when the feature is off.
- **No auth**: every endpoint is open. Do not expose this beyond a trusted internal network before Phase 3 (see `IMPLEMENTATION_PLAN.md`).

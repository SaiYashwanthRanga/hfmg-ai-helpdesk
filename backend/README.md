# HFMG AI Help Desk — Backend (MVP)

FastAPI backend. No Docker, no Redis, no auth — see `docs/archive/IMPLEMENTATION_PLAN.md` in the repo root for what's deferred and why.

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
# edit .env if your DB credentials differ, or to enable SendGrid / AI summaries

# 4. Migrate + seed
alembic upgrade head
python seed.py

# 5. Run
uvicorn app.main:app --reload --port 8000
```

API docs (interactive): http://localhost:8000/docs

## Running tests

Tests run against a **separate** database, because the suite drops, recreates and truncates every table. This is enforced in `tests/conftest.py`: a plain `pytest` uses your `.env` database's name plus `_test` (e.g. `hfmg_helpdesk_test`) and creates it on first run. It refuses outright to run against any database whose name doesn't end in `_test`.

```bash
pytest                                              # uses <your dev db>_test automatically
TEST_DATABASE_URL="postgresql+asyncpg://hfmg:hfmg_dev_local@localhost:5432/other_test" pytest   # explicit
```

Before this guard existed, a plain `pytest` ran against `DATABASE_URL` from `.env`, i.e. the dev database, and wiped it. If your dev database suddenly has no categories or tickets, that is the likely cause: run `python seed.py` to restore the categories.

## Notes

- **Email**: sent via Twilio SendGrid's API (not SMTP) — see `SENDGRID_SETUP.md`. If `SENDGRID_API_KEY` is left empty in `.env`, sends are skipped (logged, without content) rather than failing, so the app runs end-to-end without a SendGrid account. Set `SENDGRID_API_KEY`, `EMAIL_FROM`, and `HELPDESK_EMAIL` to see real delivery.
- **AI summaries**: off by default (`ENABLE_AI_SUMMARY=false`). Set it to `true` and provide `OPENAI_API_KEY` to turn them on; tickets work identically either way — `ai_summary_status` is `DISABLED` when the feature is off.
- **No auth**: every endpoint is open. Do not expose this beyond a trusted internal network before Phase 3 (see `docs/archive/IMPLEMENTATION_PLAN.md`). The SIP voice endpoints are the exception — they authenticate via `VOICE_SIP_GATEWAY_TOKEN`.

## SIP voice agent (Phase 2)

The voice agent answers calls, collects ticket details conversationally, and creates tickets through the same pipeline as the web form. Call path: Nextiva → SIPSorcery gateway → this backend → ticket pipeline. Full setup and the gateway contract: `SIP_SETUP.md` in the repo root. Conversation design: `CALL_FLOW.md`, `VOICE_AGENT_DESIGN.md`.

**Endpoints** (called by the gateway, authenticated by `Authorization: Bearer $VOICE_SIP_GATEWAY_TOKEN`, not JWT):

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/voice/sip/start` | Call answered (turn 0) |
| `POST /api/v1/voice/sip/turn` | One caller utterance |
| `POST /api/v1/voice/sip/status` | Call ended |

**Testing without a phone** — set `VOICE_SIP_GATEWAY_TOKEN=devtoken` and POST JSON directly:

```bash
curl -X POST http://localhost:8000/api/v1/voice/sip/start   -H "Authorization: Bearer devtoken" -H "Content-Type: application/json"   -d '{"call_id":"test-1","from_number":"+18455550142"}'

curl -X POST http://localhost:8000/api/v1/voice/sip/turn   -H "Authorization: Bearer devtoken" -H "Content-Type: application/json"   -d '{"call_id":"test-1","utterance":"my eClinicalWorks is not opening","confidence":0.92}'
```

An empty `VOICE_SIP_GATEWAY_TOKEN` disables the endpoints (`403`). Keep them off the public internet.

**The agent needs `OPENAI_API_KEY`** to understand callers (it reuses the same provider and model as the AI summary feature). Without it, callers are escalated to a human callback rather than being misunderstood silently — degraded, but safe.

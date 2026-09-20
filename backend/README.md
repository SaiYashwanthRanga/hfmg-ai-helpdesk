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
# edit .env if your DB credentials differ, or to enable SendGrid / AI summaries

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

- **Email**: sent via Twilio SendGrid's API (not SMTP) — see `SENDGRID_SETUP.md`. If `SENDGRID_API_KEY` is left empty in `.env`, sends are skipped (logged, without content) rather than failing, so the app runs end-to-end without a SendGrid account. Set `SENDGRID_API_KEY`, `EMAIL_FROM`, and `HELPDESK_EMAIL` to see real delivery.
- **AI summaries**: off by default (`ENABLE_AI_SUMMARY=false`). Set it to `true` and provide `OPENAI_API_KEY` to turn them on; tickets work identically either way — `ai_summary_status` is `DISABLED` when the feature is off.
- **No auth**: every endpoint is open. Do not expose this beyond a trusted internal network before Phase 3 (see `IMPLEMENTATION_PLAN.md`). The Twilio webhooks are the exception — they authenticate via request signature.

## Twilio voice agent (Phase 2)

The voice agent answers calls, collects ticket details conversationally, and creates tickets through the same pipeline as the web form. Design: `TWILIO_ARCHITECTURE.md`, `CALL_FLOW.md`, `VOICE_AGENT_DESIGN.md` in the repo root.

**Endpoints** (all authenticated by `X-Twilio-Signature`, not JWT):

| Endpoint | Twilio console setting |
|---|---|
| `POST /api/v1/webhooks/twilio/voice` | "A call comes in" |
| `POST /api/v1/webhooks/twilio/voice/gather` | (used internally by `<Gather action>`) |
| `POST /api/v1/webhooks/twilio/voice/status` | Status callback |
| `POST /api/v1/webhooks/twilio/voice/fallback` | Primary handler fails |

**Local testing with a real Twilio number:**

```bash
ngrok http 8000                       # expose the local server
# then in .env:
#   TWILIO_AUTH_TOKEN=<from Twilio console>
#   TWILIO_VALIDATE_SIGNATURE=true
#   TWILIO_PUBLIC_BASE_URL=https://<your-subdomain>.ngrok-free.app
# and point the Twilio number's voice webhook at
#   https://<your-subdomain>.ngrok-free.app/api/v1/webhooks/twilio/voice
```

`TWILIO_PUBLIC_BASE_URL` matters: Twilio signs the public URL, so validation fails behind a tunnel unless the app knows what that URL was.

**Testing without Twilio** — set `TWILIO_VALIDATE_SIGNATURE=false` and POST form data directly:

```bash
curl -X POST http://localhost:8000/api/v1/webhooks/twilio/voice \
  -d "CallSid=CA-test-1" -d "From=%2B18455550142" -d "To=%2B18455559999"

curl -X POST http://localhost:8000/api/v1/webhooks/twilio/voice/gather \
  -d "CallSid=CA-test-1" -d "SpeechResult=my eClinicalWorks is not opening" -d "Confidence=0.92"
```

Never set `TWILIO_VALIDATE_SIGNATURE=false` anywhere reachable from the internet — it disables the only authentication these endpoints have.

**The agent needs `OPENAI_API_KEY`** to understand callers (it reuses the same provider and model as the AI summary feature). Without it, callers are escalated to a human callback rather than being misunderstood silently — degraded, but safe.

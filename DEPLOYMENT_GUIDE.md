# HFMG AI Help Desk — Deployment Guide

**Audience:** the engineer deploying this system to a server for the first time
**Covers:** Phases 1–2 as built (web ticketing + SIP voice agent)
**Companion docs:** [SIP_SETUP.md](SIP_SETUP.md), [SENDGRID_SETUP.md](SENDGRID_SETUP.md), [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md)

---

## 1. Read This First — The System Has No Authentication

Phase 1 deliberately deferred auth (`docs/archive/IMPLEMENTATION_PLAN.md`, MVP Scope Decision). **Every ticket endpoint is open to anyone who can reach it.** There is no login, no API key, no role check.

Voice calls arrive over SIP (Nextiva → SIPSorcery gateway), and the gateway calls the backend over the internal network, so **nothing needs to be reachable from the public internet**:

| Path | Must be reachable from | Authentication |
|---|---|---|
| `/api/v1/voice/sip/*` | The SIP gateway host (internal network) | Bearer token (`VOICE_SIP_GATEWAY_TOKEN`) |
| Everything else (`/api/v1/tickets`, `/api/v1/categories`, frontend) | HFMG internal network only | **None** |

**This guide's reverse-proxy configuration (§6) restricts everything to the internal network. Do not skip it.** Exposing `/api/v1/tickets` to the internet would let anyone read every ticket — including caller names, phone numbers, and free-text descriptions that may reference patients.

If your organization cannot accept an internally-unauthenticated app even on a trusted network, stop here and implement Phase 3 auth first.

## 2. What You're Deploying

Three things, no containers:

```
                     ┌──────────────────────────────────┐
  Internal network ──►│  Nginx :443  (TLS termination)   │
  (incl. SIP gateway) │  - all routes → internal ACL      │
                      │                                   │
                      └───────┬───────────────┬──────────┘
                              │               │
                    proxy_pass│               │ static files
                              ▼               ▼
                   ┌────────────────┐  ┌──────────────┐
                   │ uvicorn :8000   │  │ frontend     │
                   │ (FastAPI app,   │  │ dist/ build  │
                   │  systemd unit)  │  └──────────────┘
                   └───────┬────────┘
                           │
                           ▼
                  ┌──────────────────┐     outbound HTTPS to:
                  │   PostgreSQL 16   │     - api.openai.com
                  └──────────────────┘     - api.sendgrid.com
```

**No Redis, no worker process, no queue.** Background work (AI summaries, notification emails) runs in-process via FastAPI `BackgroundTasks`. This has a real operational consequence covered in §10: **in-flight background work is lost when the process restarts.**

## 3. Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Linux host | Ubuntu 22.04 LTS or similar | 2 vCPU / 4 GB RAM is ample for a mid-size medical group |
| Python | **3.11 or 3.12** | **Not 3.13+.** `pydantic-core` has no wheel and fails to build against newer PyO3 |
| PostgreSQL | 16 (15 minimum) | Managed (RDS/Cloud SQL) strongly preferred — see §4 |
| Node.js | 20+ | Build-time only; not needed at runtime |
| Nginx | any current | TLS termination + access control |
| DNS | Internal A record for the app hostname | Reachable from staff machines and the SIP gateway host |
| SIP gateway | .NET 8 + SIPSorcery, registered with Nextiva | Deployed separately; see [SIP_SETUP.md](SIP_SETUP.md) |

Accounts needed: Nextiva SIP credentials (configured in the gateway, see [SIP_SETUP.md](SIP_SETUP.md)), Twilio SendGrid ([SENDGRID_SETUP.md](SENDGRID_SETUP.md)), OpenAI API key.

**Sign BAAs with your SIP carrier (Nextiva), SendGrid, and OpenAI before real calls reach the system.** Caller descriptions are PHI-adjacent and flow through them.

## 4. PostgreSQL — Production Configuration

### 4.1 Create the database and role

```sql
CREATE ROLE hfmg_app WITH LOGIN PASSWORD '<strong-generated-password>';
CREATE DATABASE hfmg_helpdesk OWNER hfmg_app;
\c hfmg_helpdesk
CREATE EXTENSION IF NOT EXISTS citext;   -- required by tickets.email
```

The app role owns its schema and needs nothing more. Do not use `postgres`/superuser as the application role.

### 4.2 Settings that matter here

Baseline for a 4 GB host. Managed services set most of these sensibly; adjust rather than blindly paste.

| Setting | Value | Why |
|---|---|---|
| `max_connections` | `100` | See the pool math below |
| `shared_buffers` | `1GB` | ~25% of RAM |
| `effective_cache_size` | `3GB` | ~75% of RAM |
| `work_mem` | `16MB` | Small result sets here |
| `ssl` | `on` | Encrypt in transit — required for PHI-adjacent data |
| `log_min_duration_statement` | `1000` | Log slow queries, not every query |
| `wal_level` | `replica` | Required for PITR and replicas |
| `archive_mode` | `on` | Enables point-in-time recovery (§backup in runbook) |

**Connection pool math:** SQLAlchemy defaults to `pool_size=5, max_overflow=10` → up to **15 connections per uvicorn worker**. With 4 workers that's 60 connections. Keep `workers × 15 < max_connections`, leaving headroom for admin sessions and backups.

### 4.3 Encryption and network

- Storage encryption at rest: on (managed services: enable at creation — it usually cannot be added later without a rebuild).
- Never expose Postgres to the internet. Bind to the private network; restrict by security group / `pg_hba.conf` to the app host only.
- `DATABASE_URL` must use TLS in production: append `?ssl=require` for `asyncpg`.

## 5. Application Server

### 5.1 Install

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin hfmg
sudo -u hfmg -H bash
cd /home/hfmg
git clone <repo-url> app && cd app/backend
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 5.2 Environment file

Create `/home/hfmg/app/backend/.env`, owned by `hfmg`, mode `600` (it holds three secrets):

```bash
sudo chown hfmg:hfmg /home/hfmg/app/backend/.env
sudo chmod 600 /home/hfmg/app/backend/.env
```

Complete variable reference — every variable the app reads:

| Variable | Production value | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://hfmg_app:<pw>@<host>:5432/hfmg_helpdesk?ssl=require` | **Secret** |
| `CORS_ORIGINS` | `https://helpdesk.hfmg.net` | Exact frontend origin, comma-separated for several. A mismatch breaks the dashboard |
| `ENABLE_EMAIL_NOTIFICATIONS` | `true` | |
| `EMAIL_PROVIDER` | `sendgrid` | Selects the provider implementation |
| `SENDGRID_API_KEY` | `SG.xxxxx` | **Secret** — must be set in production, see §5.3 |
| `EMAIL_FROM` | `reminder@hfmg.net` | Must be on a domain you've authenticated in SendGrid |
| `HELPDESK_EMAIL` | `helpdesk@hfmg.net` | Fixed notification recipient, not per-ticket |
| `HELPDESK_NOTIFICATION_EMAIL` | `helpdesk@hfmg.net` | Where ticket notifications land |
| `ENABLE_AI_SUMMARY` | `true` | |
| `LLM_PROVIDER` | `openai` | Selects the provider implementation |
| `OPENAI_API_KEY` | `sk-…` | **Secret**. Also powers the voice agent |
| `OPENAI_MODEL` | `gpt-5-nano` | Swap models here; no code change needed |
| `OPENAI_TEMPERATURE` | *(leave blank)* | Reasoning models reject it. Only set for a model that supports it |
| `OPENAI_REASONING_EFFORT` | `minimal` (optional) | Lowest latency on reasoning models — matters on voice calls |
| `VOICE_SIP_GATEWAY_TOKEN` | long random string | **Secret**. Must equal `Backend.GatewayToken` in the gateway. Empty disables the SIP endpoints |
| `VOICE_NLU_TIMEOUT_SECONDS` | `4.0` | Keep short: the caller is waiting on the line for each reply |
| `VOICE_MAX_MISUNDERSTANDINGS` | `3` | Escalation threshold |
| `VOICE_MAX_EMAIL_ATTEMPTS` | `2` | |
| `VOICE_NLU_MODEL` | `gpt-4.1-mini` | Model for understanding callers, separate from `OPENAI_MODEL` (summaries). Chosen by measurement: see [the latency/accuracy report](docs/reviews/VOICE_LATENCY_ACCURACY_REPORT.md) |
| `VOICE_CONFIRM_NAME` | `true` | A name we're unsure of (unfamiliar, or low recognizer confidence) is read back spelled ("Y A S H W A N T H — is that right?"); on "no" they spell it and the corrected name is read back again. Common names cost no extra turn |
| `VOICE_CONFIRM_SUMMARY` | `true` | The whole ticket (who, what, since when, priority and why) is read back and can be corrected before it is created. One extra turn per call. `false` restores the old create-immediately flow |
| `VOICE_MAX_PHONE_ATTEMPTS` | `2` | After this many failed tries the agent carries on without a callback number instead of escalating |
| `VOICE_DEPARTMENTS` | HFMG's departments, comma-separated | **Set this.** Callers' departments are matched against it; anything else is read back and marked "unverified" on the ticket. If empty, a placeholder list written without HFMG's org chart is used |
| `VOICE_STRICT_EXTRACTION` | `false` | Record work-blocked / patient-care / who-is-affected **only when the caller said it**, and ask when unsure (see [VOICE_AGENT_DESIGN.md](VOICE_AGENT_DESIGN.md) §5.1). Off = the original, inferring behaviour. Turn on after trying it in the simulator; turn off again to roll back, no migration either way |
| `VOICE_MAX_CLARIFICATION_TURNS` | `2` | Extra impact questions per call with strict extraction (each fact at most twice) |
| `VOICE_VERIFY_SAFETY_FACTS` | `true` | With strict extraction, a second short model call confirms every accepted "cannot work" / "patient care blocked" |
| `VOICE_VERIFIER_TIMEOUT_SECONDS` | `3.0` | Timeout of that call; a failure leaves the fact unknown |
| `VOICE_NLU_HEDGE_AFTER_SECONDS` | `2.5` | A slow NLU call gets a duplicate request; first answer wins. Cuts tail latency at the cost of extra tokens on slow calls only. `0` disables |
| `ENVIRONMENT` | `production` | Must be exactly this in production: it hard-disables the AI Call Simulator and `LLM_PROVIDER=fake` |
| `ENABLE_VOICE_SIMULATOR` | *(unset / `false`)* | **Never `true` in production.** The API refuses to start if it is. Test environments only; see [VOICE_SIMULATOR.md](VOICE_SIMULATOR.md) §3 |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `10` | Per worker. Each voice turn holds a connection through its NLU call; see [the performance report](docs/reviews/VOICE_SIMULATOR_PERFORMANCE.md) §3 before raising, and keep workers × (pool + overflow) under Postgres `max_connections` |

Frontend build-time variable (`frontend/.env`): `VITE_API_BASE_URL=https://helpdesk.hfmg.net/api/v1`.

### 5.3 One production-specific warning about email

If `SENDGRID_API_KEY` is empty, the notifier skips the send — and logs only that it was skipped, never the recipient, subject, or body (the application layer never logs ticket content on any email path, success or failure). That means the app runs end-to-end without SendGrid configured, but **no notification reaches `helpdesk@hfmg.net`** until a real key is set.

Always set `SENDGRID_API_KEY` in production. The runbook's monitoring section includes an alert for a persistently unconfigured or failing email provider.

### 5.4 systemd unit

`/etc/systemd/system/hfmg-api.service`:

```ini
[Unit]
Description=HFMG AI Help Desk API
After=network.target

[Service]
Type=exec
User=hfmg
Group=hfmg
WorkingDirectory=/home/hfmg/app/backend
EnvironmentFile=/home/hfmg/app/backend/.env
ExecStart=/home/hfmg/app/backend/.venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 --workers 4 --proxy-headers \
    --forwarded-allow-ips 127.0.0.1
Restart=on-failure
RestartSec=5
# Give in-flight background work (emails, AI summaries) a chance to finish.
KillSignal=SIGTERM
TimeoutStopSec=30

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/hfmg/app

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hfmg-api
sudo systemctl status hfmg-api
```

Bind to `127.0.0.1` only — Nginx is the sole public entry point.

**On `--workers 4`:** conversation state lives in Postgres, so voice calls work correctly across workers (any worker can handle any turn). But `BackgroundTasks` are per-process, so a restart drops whatever that worker was doing (§10).

## 6. Nginx, TLS, and the Access Control Split

This is the security-critical part of the deployment.

### 6.1 Certificate

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d helpdesk.hfmg.net
```

Staff browsers need a trusted certificate for the dashboard (use your internal CA if the host is not public). The SIP gateway calls the backend directly over the internal network and does not go through this TLS layer unless you route it there. Certbot installs a renewal timer; verify with `systemctl list-timers | grep certbot`.

### 6.2 Configuration

`/etc/nginx/sites-available/hfmg-helpdesk`:

```nginx
server {
    listen 80;
    server_name helpdesk.hfmg.net;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name helpdesk.hfmg.net;

    ssl_certificate     /etc/letsencrypt/live/helpdesk.hfmg.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/helpdesk.hfmg.net/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;

    # Don't log caller data in URLs; keep access logs lean.
    access_log /var/log/nginx/hfmg-access.log;
    error_log  /var/log/nginx/hfmg-error.log warn;

    # --- INTERNAL ONLY: the whole API, including /api/v1/voice/sip/* ---
    # The SIP gateway's host must be inside one of these ranges. The SIP
    # routes are additionally protected by VOICE_SIP_GATEWAY_TOKEN.
    location /api/ {
        allow 10.0.0.0/8;        # replace with HFMG's actual internal ranges
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny all;

        proxy_pass http://127.0.0.1:8000;
        include /etc/nginx/proxy_params;
        proxy_set_header X-Forwarded-Proto https;
    }

    # --- INTERNAL ONLY: the React dashboard ---
    location / {
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny all;

        root /var/www/hfmg-helpdesk;
        try_files $uri $uri/ /index.html;   # SPA client-side routing
    }
}
```

**Verify the split actually works before going live** (§9). Getting this wrong is the single highest-consequence mistake available in this deployment.

## 7. Database Migrations and Seed Data

```bash
cd /home/hfmg/app/backend
sudo -u hfmg .venv/bin/alembic upgrade head
sudo -u hfmg .venv/bin/python seed.py     # categories; idempotent, safe to re-run
```

`seed.py` inserts the ten categories (including the six the voice agent classifies into) and renames `Printers` → `Printer`. It skips anything already present, so re-running it after an upgrade is safe.

**Take a backup before every migration** (runbook §5). Alembic has no automatic rollback for data loss.

## 8. Frontend Build

Built on a workstation or CI, then copied — Node isn't needed on the server.

```bash
cd frontend
echo 'VITE_API_BASE_URL=https://helpdesk.hfmg.net/api/v1' > .env
npm ci
npm run build              # type-checks, then emits dist/
sudo rsync -av --delete dist/ /var/www/hfmg-helpdesk/
sudo chown -R www-data:www-data /var/www/hfmg-helpdesk
```

`VITE_API_BASE_URL` is baked in at build time. Changing it requires a rebuild, not a restart.

## 9. Post-Deployment Verification

Run all of these before declaring the deployment done.

```bash
# 1. API is alive (from the app host)
curl -s http://127.0.0.1:8000/api/v1/health          # {"status":"ok"}

# 2. TLS is valid and trusted (from anywhere)
curl -sI https://helpdesk.hfmg.net/ | head -1

# 3. Internal access works (from inside the network)
curl -s https://helpdesk.hfmg.net/api/v1/categories | head -c 200

# 4. CRITICAL — ticket API is NOT public (from off-network, e.g. a phone on cellular)
curl -s -o /dev/null -w '%{http_code}\n' https://helpdesk.hfmg.net/api/v1/tickets
#    Expect 403. If you get 200, STOP and fix the Nginx ACL before continuing.

# 5. SIP endpoints reject a missing token (from the gateway host or internal network)
curl -s -o /dev/null -w '%{http_code}
' -X POST \
  https://helpdesk.hfmg.net/api/v1/voice/sip/start \
  -H "Content-Type: application/json" -d '{"call_id":"probe"}'
#    Expect 401 (403 if VOICE_SIP_GATEWAY_TOKEN is unset). 200 means auth is broken - fix it.

# 6. Database connectivity and schema
sudo -u hfmg /home/hfmg/app/backend/.venv/bin/alembic current   # shows head revision
```

Then the functional checks:

- [ ] Dashboard loads at `https://helpdesk.hfmg.net` from an internal machine
- [ ] Submitting a ticket through the form succeeds and it appears in the list
- [ ] A notification email arrives at `helpdesk@hfmg.net` within ~1 minute
- [ ] The ticket's AI summary populates within ~30 seconds (if `ENABLE_AI_SUMMARY=true`)
- [ ] A real test call produces a ticket — see [SIP_SETUP.md](SIP_SETUP.md) §5

## 10. Deploys and Restarts

**In-flight background work is lost on restart.** When uvicorn stops, any queued `BackgroundTask` — a notification email not yet sent, an AI summary not yet generated — dies with the process. Consequences and mitigations:

- A ticket created seconds before a restart may never send its notification email. The ticket itself is safe (it was committed before the task was queued).
- An AI summary interrupted this way stays `PENDING` forever. There's no retry; an agent can trigger regeneration, or you can leave it — the ticket is fully usable without it.
- **Deploy during low-call periods.** Restarting mid-call drops that caller's conversation; they'd have to call back.
- `TimeoutStopSec=30` in the unit file gives in-flight work a chance to finish before SIGKILL.

Phase 3's job queue removes this limitation entirely. Until then, treat restarts as mildly disruptive rather than free.

### Upgrade procedure

```bash
cd /home/hfmg/app
sudo -u hfmg git pull
cd backend
sudo -u hfmg .venv/bin/pip install -r requirements.txt
# Back up first (runbook section 5), then:
sudo -u hfmg .venv/bin/alembic upgrade head
sudo systemctl restart hfmg-api
curl -s http://127.0.0.1:8000/api/v1/health
# Frontend, if it changed: rebuild and rsync per section 8.
```

### Rollback

```bash
sudo -u hfmg git checkout <previous-tag>
sudo -u hfmg .venv/bin/pip install -r requirements.txt
sudo -u hfmg .venv/bin/alembic downgrade -1    # ONLY if the deploy applied a migration
sudo systemctl restart hfmg-api
```

Check what a downgrade actually drops before running it. The Phase 2 downgrade removes `voice_call_sessions` (losing all call transcripts) and `tickets.source`. If the new version is merely misbehaving rather than corrupting data, prefer rolling back code only and leaving the schema forward — the Phase 2 migration is additive and older code ignores the new column.

## 11. Known Gaps at This Phase

Honest list of things a production operator will notice are missing. All are Phase 3 items (`docs/archive/IMPLEMENTATION_PLAN.md`), not oversights:

| Gap | Impact | Workaround |
|---|---|---|
| No authentication | Network ACL is the only control | §6 reverse-proxy split; Phase 3 adds JWT/RBAC |
| ~~No readiness endpoint~~ **Fixed** | `GET /api/v1/health/ready` is now real (Backend Tier 0) — a genuine `SELECT 1` check, `503` on failure. `/api/v1/health` remains liveness-only, unchanged. Point your orchestrator's readiness probe at `/health/ready` | n/a — use the endpoint |
| Logs are plain text, not JSON | Harder to query in an aggregator | Parse by prefix (`hfmg.voice.*`); Phase 3 adds structured logging |
| No queue | Background work lost on restart (§10) | Deploy during quiet periods |
| No `notification_log` table | Email delivery isn't auditable in-app | Use SendGrid's Activity Feed |
| Application logs never contain ticket content | Can't debug a failed send's exact content from `journalctl` | By design — cross-reference the ticket number against the dashboard or SendGrid's Activity Feed instead |
| No audit log | Ticket edits aren't attributed | Postgres backups provide point-in-time history |

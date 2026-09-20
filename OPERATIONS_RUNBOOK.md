# HFMG AI Help Desk — Operations Runbook

**Audience:** whoever is on call for this system
**Scope:** day-to-day operation, monitoring, backup, disaster recovery, troubleshooting
**Setup docs:** [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md), [TWILIO_SETUP.md](TWILIO_SETUP.md), [SENDGRID_SETUP.md](SENDGRID_SETUP.md)

---

## 1. System at a Glance

| Component | What it is | Where |
|---|---|---|
| API | FastAPI under uvicorn, 4 workers | `systemd: hfmg-api`, `127.0.0.1:8000` |
| Dashboard | Static React build | `/var/www/hfmg-helpdesk`, served by Nginx |
| Database | PostgreSQL 16 | `hfmg_helpdesk` |
| Reverse proxy | Nginx, TLS termination + access control | `:443` |

**External dependencies** — an outage in any of these degrades the system in a specific way:

| Dependency | Used for | If it's down |
|---|---|---|
| OpenAI API | Voice agent understanding; AI summaries | **Every caller gets escalated to a human callback.** Web ticketing unaffected; summaries stay `PENDING` |
| Twilio | Inbound calls | Phone intake stops entirely. Web ticketing unaffected |
| SendGrid (API) | Notifications | Tickets still created; `helpdesk@hfmg.net` stops being notified |
| PostgreSQL | Everything | Full outage — both web and phone |

**Key architectural facts for on-call:**
- No authentication on ticket endpoints — the Nginx ACL is the only control (`DEPLOYMENT_GUIDE.md` §1).
- No job queue. Emails and AI summaries run in-process and **are lost if the service restarts mid-flight**.
- Voice conversation state lives in Postgres, so calls survive worker-to-worker routing — but not a full service restart.

## 2. Routine Checks

**Daily (2 minutes):**
```bash
systemctl status hfmg-api                                   # active (running)
curl -s http://127.0.0.1:8000/api/v1/health                 # {"status":"ok"}
journalctl -u hfmg-api --since "24 hours ago" -p err | tail # errors overnight
```

**Weekly:**
- Confirm last night's backup restored cleanly in staging (§5.4 — an unverified backup is not a backup).
- Review escalation rate and abandoned calls (§3.2).
- Check disk: `df -h` (WAL and logs are the usual growth).
- Check TLS expiry: `sudo certbot certificates`.

**Monthly:**
- Review the voice agent's category accuracy against real tickets (`VOICE_AGENT_DESIGN.md` §9).
- Apply OS and dependency security updates in staging, then production.
- Prune old `voice_call_sessions` per retention policy (§6.3).

## 3. Monitoring

### 3.1 What to alert on

| Alert | Condition | Why it matters |
|---|---|---|
| **API down** | `/api/v1/health` non-200 for 2 min | Total outage |
| **Database unreachable** | `pg_isready` fails | `/health` does **not** cover this — see below |
| **Disk >85%** | any volume | Postgres stops writing when full |
| **TLS expiring** | <14 days | Twilio refuses untrusted certs; phone intake dies |
| **5xx rate** | >1% of requests over 5 min | App-level breakage |
| **Twilio webhook failures** | any 11200/11205 in Twilio console | Callers hearing errors |
| **Email provider unconfigured** | log matches `Email provider not configured` | Notifications silently not sending — `SENDGRID_API_KEY` is unset in production |
| **Escalation rate** | >25% of calls over an hour | Agent failing callers; usually a missing/invalid OpenAI key |
| **LLM failures** | repeated `hfmg.voice.nlu` / `hfmg.llm.openai` errors | Voice agent degraded |

### 3.2 Health endpoints (updated — final review pass)

**`GET /api/v1/health` is liveness only**, unchanged: it returns `{"status":"ok"}` from the web process without touching the database. A healthy response does **not** mean the system is working.

**`GET /api/v1/health/ready` is now real** (Backend Tier 0 — this section previously said it "was never implemented," which was true at the time but is no longer accurate). It runs a real `SELECT 1` and returns `503` if the database is unreachable — point your load balancer / orchestrator readiness probe at this endpoint instead of `pg_isready` from an external host if that's simpler for your topology. `pg_isready`/`psql` directly against the database remain valid as an independent check, and the synthetic check below is still the most informative single signal:

```bash
curl -fsS https://helpdesk.hfmg.net/api/v1/health/ready       # now does what this used to say didn't exist
pg_isready -h <db-host> -U hfmg_app -d hfmg_helpdesk           # still valid as an independent check
psql -c "SELECT 1"        # from the app host, using the app credentials
```

A synthetic check that actually exercises the stack is more informative than either health endpoint alone:

```bash
curl -fsS https://helpdesk.hfmg.net/api/v1/categories > /dev/null   # hits the DB
```

**Also now real** (not covered by this runbook's original scope, added here for operators): `GET /api/v1/health/dependencies` reports OpenAI/Twilio/Database/Email reachability in one call, each cached server-side for 30 seconds — useful as a single dashboard check before diving into the alert table above.

### 3.3 Voice agent metrics worth watching

**Updated (final review pass):** the escalation-rate query below is now also available as a real endpoint — `GET /api/v1/analytics/escalation-rate?days=7` — cached-free, on-demand, and what the Analytics page's UI uses. The other three queries below (escalation-reason breakdown, average conversation length, abandoned-call drop-off point) still have no endpoint and remain ad hoc SQL. Run weekly at minimum, or hit the endpoint for the first one:

```sql
-- Containment: calls that produced a ticket without human escalation
-- (equivalent to GET /api/v1/analytics/escalation-rate?days=7, which is now real)
SELECT
  count(*)                                              AS calls,
  count(*) FILTER (WHERE ticket_id IS NOT NULL)         AS produced_ticket,
  count(*) FILTER (WHERE escalated)                     AS escalated,
  round(100.0 * count(*) FILTER (WHERE escalated) / NULLIF(count(*),0), 1) AS escalation_pct
FROM voice_call_sessions
WHERE created_at > now() - interval '7 days';

-- Why callers are being escalated
SELECT escalation_reason, count(*)
FROM voice_call_sessions
WHERE escalated AND created_at > now() - interval '7 days'
GROUP BY 1 ORDER BY 2 DESC;

-- Conversation length (rising = script or understanding degrading)
SELECT round(avg(jsonb_array_length(turns)), 1) AS avg_turns
FROM voice_call_sessions
WHERE created_at > now() - interval '7 days';

-- Where abandoned calls drop off
SELECT state, count(*)
FROM voice_call_sessions
WHERE state = 'ABANDONED' AND created_at > now() - interval '7 days'
GROUP BY 1 ORDER BY 2 DESC;
```

Interpretation: `CALLER_REQUESTED` escalations are usually fine — people who want a human. `REPEATED_MISUNDERSTANDING` escalations mean the agent is failing, and a spike almost always points at the OpenAI key or API.

## 4. Logging

### 4.1 Where

Everything goes to stdout/stderr, captured by systemd:

```bash
journalctl -u hfmg-api -f                      # live
journalctl -u hfmg-api --since "1 hour ago"
journalctl -u hfmg-api -p err                  # errors only
sudo tail -f /var/log/nginx/hfmg-error.log     # proxy/TLS issues
```

### 4.2 Logger names

Filter by prefix — logs are plain text, not JSON (Phase 3 adds structured logging):

| Logger | Covers |
|---|---|
| `hfmg.voice.routes` | Per-turn call progress: `call=<sid> state=… misunderstandings=… stt_confidence=…` |
| `hfmg.voice.orchestrator` | State machine errors |
| `hfmg.voice.nlu` | Caller-speech interpretation failures |
| `hfmg.llm.openai` | Provider calls: retries, timeouts, non-retryable errors |
| `hfmg.voice.security` | Rejected webhook signatures |
| `hfmg.notifications.email` | Send success/failure |
| `hfmg.ai.summarizer` | Summary generation failures |

Tracing one call end to end:
```bash
journalctl -u hfmg-api | grep "CAxxxxxxxxxxxxxxxx"
```

### 4.3 PHI in logs — two things to know

1. **Normal operation does not log caller speech.** Voice logs record the CallSid, state, counters, and confidence — not utterances.
2. **Email sending never logs ticket content, on any path.** With `SENDGRID_API_KEY` unset, or on any send failure, the notifier logs only that a send was skipped/failed — never the recipient, subject, or body (`SENDGRID_SETUP.md` §6). This is an application-layer guarantee, not just a production recommendation.

Before shipping logs to any third-party aggregator, confirm that vendor is covered by a BAA. (Email sending itself never logs ticket content on any path — success, skip, or failure — so this concern is specific to voice call handling, not email.)

Retention: keep journald bounded so logs can't fill the disk —
```bash
sudo journalctl --vacuum-time=90d
```
Align 90 days with HFMG's log retention policy.

## 5. Backup Strategy

The database is the only stateful component. Everything else — code, frontend build, config — is reproducible from the repo plus `.env`.

**What's irreplaceable:** tickets, categories, and `voice_call_sessions` (call transcripts).

### 5.1 If using managed Postgres (recommended)

Enable automated backups with **7–35 day retention** and **point-in-time recovery**. Take a manual snapshot before every migration. Verify the retention window actually matches what HFMG's compliance policy requires — healthcare record retention is typically far longer than a default backup window, which is why §5.5 exists.

### 5.2 If self-hosting: nightly dumps

`/usr/local/bin/hfmg-backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR=/var/backups/hfmg
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"

pg_dump -h localhost -U hfmg_app -d hfmg_helpdesk -F c \
  -f "$BACKUP_DIR/hfmg_helpdesk-$STAMP.dump"

gpg --encrypt --recipient backups@hfmg.net "$BACKUP_DIR/hfmg_helpdesk-$STAMP.dump"
rm "$BACKUP_DIR/hfmg_helpdesk-$STAMP.dump"

aws s3 cp "$BACKUP_DIR/hfmg_helpdesk-$STAMP.dump.gpg" \
  s3://hfmg-backups/helpdesk/ --sse aws:kms

find "$BACKUP_DIR" -name '*.dump.gpg' -mtime +14 -delete
```

Schedule at 02:00 daily. **Backups contain PHI-adjacent data**, so encrypt before they leave the host and store them somewhere with encryption at rest, access logging, and a BAA.

### 5.3 What else to back up

- `/home/hfmg/app/backend/.env` — **contains secrets**; store in a password manager or secrets vault, never with database backups.
- Nginx config and TLS certs (certs are re-issuable; config is quicker to restore).
- Twilio/SendGrid console settings — documented in their setup guides, so reproducible by hand.

### 5.4 Verify restores, monthly

An untested backup is a guess. Once a month, restore the latest dump into a scratch database and confirm it's intact:

```bash
createdb hfmg_restore_test
pg_restore -d hfmg_restore_test /var/backups/hfmg/<latest>.dump
psql -d hfmg_restore_test -c "SELECT count(*) FROM tickets;"
psql -d hfmg_restore_test -c "SELECT max(created_at) FROM tickets;"   # recent?
dropdb hfmg_restore_test
```

### 5.5 Retention vs. compliance

Backup retention (days/weeks) is **not** the same as record retention (often 6–7 years for healthcare-adjacent records). Rolling backups alone will not satisfy a records request for a two-year-old ticket. Confirm with HFMG compliance whether long-term archival is required; if so, add periodic archival dumps to cold storage on top of the rolling backups.

## 6. Disaster Recovery

### 6.1 Targets

| | Target | Determined by |
|---|---|---|
| **RPO** (data loss) | ≤24 h with nightly dumps; ≤5 min with PITR | Backup method |
| **RTO** (time to restore) | 2–4 h for full rebuild | Mostly provisioning time |

Agree these with HFMG IT leadership. If 24 hours of lost tickets is unacceptable, use managed Postgres with PITR — it's the single highest-value reliability upgrade available here.

### 6.2 Total loss — full rebuild

1. **Provision** a host per [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) §3.
2. **Restore the database first** — everything else depends on it:
   ```bash
   createdb -O hfmg_app hfmg_helpdesk
   psql -d hfmg_helpdesk -c "CREATE EXTENSION IF NOT EXISTS citext;"
   gpg --decrypt hfmg_helpdesk-<stamp>.dump.gpg > restore.dump
   pg_restore -d hfmg_helpdesk --no-owner --role=hfmg_app restore.dump
   ```
3. **Deploy the app**: guide §5, restoring `.env` from the secrets vault.
4. **Confirm schema currency**: `alembic current` should show the latest revision. If the dump predates a migration, run `alembic upgrade head`.
5. **Nginx + TLS**: guide §6. Re-issue certs with certbot if needed.
6. **Frontend**: rebuild and deploy (guide §8).
7. **Repoint DNS** to the new host.
8. **Update Twilio** if the hostname changed — webhook URLs *and* `TWILIO_PUBLIC_BASE_URL` (see [TWILIO_SETUP.md](TWILIO_SETUP.md) §5).
9. **Run the full verification checklist**, guide §9 — including the off-network test proving `/api/v1/tickets` is not public.

### 6.3 Partial scenarios

| Scenario | Action |
|---|---|
| **App server lost, DB intact** | Rebuild app only (steps 3–9). No data loss |
| **DB corrupted, host fine** | Restore from the most recent good backup into a *new* database name, verify contents, then repoint `DATABASE_URL` and restart. Don't overwrite the damaged database until the restore is confirmed good |
| **Bad migration** | Restore the pre-migration backup (you took one — guide §7). `alembic downgrade` is a fallback, but check what it drops: the Phase 2 downgrade deletes `voice_call_sessions` and all call transcripts |
| **Accidental ticket deletion** | Tickets are never hard-deleted by the app. If it happened via direct SQL, restore to a scratch DB and copy the rows back |
| **Certificate expired** | `sudo certbot renew --force-renewal && sudo systemctl reload nginx`. Phone intake is down until fixed — Twilio rejects untrusted certs |
| **Twilio account suspended** | Phone intake stops; web ticketing is unaffected. Post an internal notice telling staff to use the dashboard |

### 6.4 Communicating an outage

Staff need somewhere to report IT problems while the help desk itself is down. Keep a documented fallback — a monitored mailbox or a phone extension — and include it in the outage notice. Circular dependency ("report help desk outages in the help desk") is the failure to avoid.

## 7. Troubleshooting

### API won't start
```bash
systemctl status hfmg-api
journalctl -u hfmg-api -n 50
```
Common causes: bad `DATABASE_URL` (check credentials and `?ssl=require`), Postgres down, port 8000 already held by an old process (`lsof -ti:8000`), or a dependency missing after an upgrade (re-run `pip install -r requirements.txt`).

### Dashboard loads but shows no data / network errors
Almost always **CORS**. `CORS_ORIGINS` must exactly match the browser's origin (`https://helpdesk.hfmg.net`) — scheme included, no trailing slash. Confirm in the browser console; a CORS failure is explicit there. After changing it, restart the service.

If the dashboard is blank instead, `VITE_API_BASE_URL` was wrong at build time — it's baked into the bundle, so this needs a **rebuild**, not a restart.

### Tickets create but no emails
Work through [SENDGRID_SETUP.md](SENDGRID_SETUP.md) §9. Fastest check:
```bash
journalctl -u hfmg-api | grep hfmg.notifications.email | tail
```
`Email provider not configured; skipping` → `SENDGRID_API_KEY` unset. `SendGrid send failed with status …` → credentials, sender authentication, or network — see `SENDGRID_SETUP.md` §9. `disabled; skipping` → `ENABLE_EMAIL_NOTIFICATIONS=false`. Silence → no ticket was created, or the task died in a restart.

### AI summaries stuck on PENDING
```bash
journalctl -u hfmg-api | grep hfmg.ai.summarizer | tail
```
Causes: `OPENAI_API_KEY` missing/invalid/out of credit; no egress to `api.openai.com`; or the service restarted while the task was in flight (no retry — it stays `PENDING` forever).

Tickets are fully usable without summaries. An agent can trigger regeneration from the ticket. To find stragglers:
```sql
SELECT ticket_number, created_at FROM tickets
WHERE ai_summary_status = 'PENDING' AND created_at < now() - interval '1 hour';
```

### Every caller is escalated to a callback
The agent can't understand anyone. Overwhelmingly the OpenAI key:
```bash
journalctl -u hfmg-api | grep hfmg.voice.nlu | tail
```
This is a *degraded but safe* state — callers still get tickets and callbacks, so it's urgent but not an emergency. See [TWILIO_SETUP.md](TWILIO_SETUP.md) §10.

### Callers hear an error / all webhooks 403
[TWILIO_SETUP.md](TWILIO_SETUP.md) §10 covers both in detail. The 403 shortlist: `TWILIO_PUBLIC_BASE_URL` unset or mismatched, wrong auth token, or Nginx not forwarding `X-Forwarded-Proto`.

### 500 error: duplicate key on `ix_tickets_ticket_number`
Ticket numbers are generated by counting existing tickets for the year, so two simultaneous creations can race for the same number and one fails.

Rare at this volume, and the caller/user can simply retry. If it recurs, that's the signal to replace the counter with a real Postgres sequence (`DATABASE_DESIGN.md` §3.1.1) — a known, documented trade-off from the MVP.

### Database connections exhausted
`FATAL: sorry, too many clients already`. Each worker holds up to 15 connections (5 pool + 10 overflow), so 4 workers can reach 60.
```sql
SELECT count(*), state FROM pg_stat_activity
WHERE datname = 'hfmg_helpdesk' GROUP BY state;
```
Fix by reducing `--workers`, raising `max_connections`, or killing leaked idle sessions. Check for long-running queries holding connections open.

### Disk filling up
Usual culprits in order: journald logs, Postgres WAL (if `archive_mode=on` but archiving is failing, WAL accumulates indefinitely and **will** take the database down), and old backup files.
```bash
df -h
du -sh /var/log/journal /var/lib/postgresql /var/backups/hfmg
sudo journalctl --vacuum-time=30d
```
If WAL is growing, fix the archive command before deleting anything — removing un-archived WAL breaks point-in-time recovery.

### `voice_call_sessions` growing large
Every call keeps its full transcript. Low volume makes this a non-issue for a long time, but it's PHI-adjacent data under retention policy:
```sql
SELECT count(*), pg_size_pretty(pg_total_relation_size('voice_call_sessions'))
FROM voice_call_sessions;
```
Prune per the agreed retention policy, keeping rows still linked to open tickets:
```sql
DELETE FROM voice_call_sessions
WHERE created_at < now() - interval '<retention>' AND ticket_id IS NULL;
```
Confirm the retention interval with compliance before running this — and back up first.

## 8. Escalation Contacts

Fill in before go-live; an unfilled table is a gap, not a formality.

| Area | Owner | Contact |
|---|---|---|
| Application / deployment | | |
| Database | | |
| Network / DNS / TLS | | |
| Twilio account | | |
| Microsoft 365 / mail | | |
| Compliance / privacy (PHI incidents) | | |
| Fallback channel while the help desk is down (§6.4) | | |

**Suspected PHI exposure** — a log leak, a misdirected email, an unencrypted backup — is a compliance incident, not just a technical one. Notify the compliance contact the same day, and preserve evidence (don't delete the offending logs) until they advise.

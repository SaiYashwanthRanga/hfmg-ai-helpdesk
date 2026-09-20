# HFMG AI Help Desk — SendGrid Setup

**Audience:** the engineer configuring outbound ticket notifications
**Goal:** every ticket created (web or phone) emails `helpdesk@hfmg.net`
**Prerequisite:** the app is deployed ([DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md))

---

## 1. How This App Sends Mail

The notifier (`backend/app/notifications/`) calls **Twilio SendGrid's v3 Mail Send API directly over HTTPS** (`POST https://api.sendgrid.com/v3/mail/send`), via an async `httpx` client — not SMTP, and not the synchronous `sendgrid` Python SDK. A blocking SMTP or sync-SDK call inside a FastAPI `BackgroundTask` would stall the event loop for every concurrent request, including an in-progress Twilio voice webhook (which Twilio times out around 15s), so the implementation stays async end to end.

SendGrid sits behind a small provider abstraction (`app/notifications/base.py`, `sendgrid_provider.py`, `factory.py` — the same pattern used for the LLM provider in `app/llm/`), so a different email provider can be substituted later by adding one module and changing `EMAIL_PROVIDER`, with no changes to ticket or notification logic.

**This system does not use SMTP relays of any kind — not SendGrid SMTP, not Microsoft 365, not any other relay.** The API integration is the only supported path.

## 2. Sign a BAA First

Notification emails contain the caller's name, phone number, and full problem description — PHI-adjacent content. SendGrid (Twilio) offers HIPAA-eligible service under a BAA, but **not on a standard self-serve plan**; it requires an eligible plan and an executed agreement.

Do not route real ticket notifications through SendGrid until that's in place.

## 3. Account and Sender Authentication

1. Create the SendGrid account (or use HFMG's existing one).
2. **Settings → Sender Authentication → Authenticate Your Domain.**
3. Enter `hfmg.net` and follow the wizard.
4. SendGrid generates CNAME records; have whoever manages HFMG DNS add them.
5. Return to the console and click **Verify**. DNS propagation can take up to an hour.

Domain authentication sets up **SPF and DKIM**, without which mail from `reminder@hfmg.net` will be spam-filtered or rejected outright — particularly by HFMG's own mail server, which is exactly where these notifications are going. Single Sender Verification is quicker but gives worse deliverability; use full domain authentication for production.

## 4. Create a Restricted API Key

**Settings → API Keys → Create API Key.**

- Name: `hfmg-helpdesk-prod` (one key per environment)
- Permission: **Restricted Access**, with **Mail Send → Full Access** and nothing else

Do not use Full Access. This key only needs to send mail; if it leaks, a restricted key can't modify account settings, read suppression lists, or export contacts.

**The key is shown exactly once.** Copy it immediately into the app's `.env`.

## 5. Application Configuration

In `/home/hfmg/app/backend/.env`:

```bash
ENABLE_EMAIL_NOTIFICATIONS=true
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=SG.xxxxxxxxxxxxxxxxxxxxxx
EMAIL_FROM=reminder@hfmg.net
HELPDESK_EMAIL=helpdesk@hfmg.net
```

Then `sudo systemctl restart hfmg-api`.

Three things people get wrong:

- **`EMAIL_FROM` must be on the domain you authenticated** in §3, or SendGrid rejects the message.
- **`EMAIL_FROM` and `HELPDESK_EMAIL` are fixed, not per-ticket.** Every notification is sent from `reminder@hfmg.net` to `helpdesk@hfmg.net` regardless of the ticket's own `email` field (that field is the caller's contact address, never used as a send target). This is deliberate application policy, not a limitation — the notification target is a fixed organizational mailbox.
- **`SENDGRID_API_KEY` is a bearer token for the REST API**, not an SMTP password. It goes in the `Authorization: Bearer` header of every request; there is no username/password pair to configure.

## 6. Never Run Production With `SENDGRID_API_KEY` Unset

If `SENDGRID_API_KEY` is blank, the notifier **skips the send and logs only that it was skipped** — never the recipient, subject, or body. This is true both when the key is missing and when a send genuinely fails (a 4xx/5xx from SendGrid, a connection error, or a timeout): the application layer never logs ticket content on any failure path, by design.

That means the app runs end-to-end on a laptop with no SendGrid account configured — notifications are silently skipped rather than the app breaking — but it also means **no notification reaches `helpdesk@hfmg.net`** until a real key is set. Verify after deploying:

```bash
journalctl -u hfmg-api | grep -i "sendgrid\|notifications.email"
```

`SENDGRID_API_KEY is not set` or `Email provider not configured; skipping` means notifications are not being sent. Set the key and restart.

## 7. Verifying It Works

End to end, through the real code path:

1. Create a ticket in the dashboard (or place a test call).
2. Watch the log: `journalctl -u hfmg-api -f | grep -E "hfmg.notifications.email|hfmg.notifications.sendgrid"`
   - Success: `hfmg.notifications.sendgrid` logs `SendGrid accepted message <id>`, then `hfmg.notifications.email` logs `Sent created notification for HFMG-2026-000001`
   - Failure: `SendGrid send failed with status <code> after <n> attempt(s)` (no recipient, subject, or body — see §9)
3. Confirm the message arrived in the `helpdesk@hfmg.net` mailbox, sent from `reminder@hfmg.net`.
4. **Activity Feed** in the SendGrid console shows delivered/bounced/blocked per message — this is the tool for confirming *content* was correct, since the app's own logs deliberately don't show it.

Isolating the API call from the app, if step 2 fails:

```bash
curl -s -X POST https://api.sendgrid.com/v3/mail/send \
  -H "Authorization: Bearer SG.xxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "personalizations": [{"to": [{"email": "helpdesk@hfmg.net"}]}],
    "from": {"email": "reminder@hfmg.net"},
    "subject": "API test",
    "content": [{"type": "text/plain", "value": "Test from the HFMG help desk host."}]
  }' -w '\n%{http_code}\n'
```

A `202` with no body is success. If this succeeds but the app doesn't, the problem is configuration (`.env` not loaded, service not restarted). If it fails too, the problem is the key or network egress.

## 8. What Gets Sent

Plain-text email, triggered on **ticket creation** and **status change**, always **From: `reminder@hfmg.net` To: `helpdesk@hfmg.net`**:

```
Subject: New ticket HFMG-2026-000482 — eClinicalWorks

Ticket: HFMG-2026-000482
Status: NEW
Priority: HIGH
Category: eClinicalWorks
Caller: Maria Lopez
Phone: +18455550142
Email: mlopez@hfmg.net

Description:
EHR login fails with session expired on all exam room workstations since 8am.

AI Summary:
<included when the summary has finished generating>
```

For phone tickets the description also carries the call transcript. The AI summary is generated asynchronously, so a notification sent immediately after creation may not include it yet — expected, not a fault.

All wording lives in `backend/app/notifications/email.py`; the transport lives in `backend/app/notifications/sendgrid_provider.py`.

## 9. Troubleshooting

### `401` / send fails immediately, no retry
Wrong or revoked API key. Confirm `SENDGRID_API_KEY` is the full key including the `SG.` prefix with no whitespace or truncation. Keys are shown once — if unsure, generate a new one. 401s are **not retried** (a bad key fails identically every time), so one failed attempt in the log means the key is wrong, not a transient blip.

### `403` The from address does not match a verified Sender Identity
`EMAIL_FROM` is on a domain SendGrid hasn't authenticated. Complete §3, or temporarily use a Single Sender-verified address.

### Timeouts / connection refused
Outbound HTTPS (port 443) to `api.sendgrid.com` is blocked. Test with `curl -s -o /dev/null -w '%{http_code}\n' https://api.sendgrid.com` from the app host and open egress if it fails. The notifier retries connection errors and 429/5xx responses with backoff (`SENDGRID_MAX_RETRIES`, default 2) before giving up, so a fully blocked route delays the background task briefly but never blocks the API response or a caller on the phone.

### Nothing sends, no errors in the log
Check `ENABLE_EMAIL_NOTIFICATIONS`. When `false`, the notifier logs `Email notifications disabled; skipping…` and returns — quiet by design.

### Emails send but never arrive
1. Check the SendGrid Activity Feed for `dropped`, `bounced`, or `blocked`.
2. Check spam/junk in the destination mailbox — an unauthenticated domain (§3) is the usual cause.
3. Confirm `helpdesk@hfmg.net` exists and accepts external mail. If it's a distribution list, it may reject mail from outside the tenant — talk to whoever manages HFMG's mail system about an inbound allow rule for `reminder@hfmg.net`.
4. Check SendGrid's suppression lists (Bounces/Blocks); once an address lands there, later sends are silently dropped (SendGrid returns `202` anyway) until removed.

### Some tickets notify, others don't
Notifications run as in-process background tasks with no persistence. A restart drops whatever was in flight, and after `SENDGRID_MAX_RETRIES` is exhausted a failure is logged and not retried later — the ticket itself is unaffected. If this becomes common, that's the argument for Phase 3's durable job queue.

### I need to see what a failed notification actually said
You can't, from the application log — that's intentional (§6, §8). Cross-reference the ticket number and timestamp against the ticket itself in the dashboard, or against SendGrid's Activity Feed if the request reached SendGrid at all.

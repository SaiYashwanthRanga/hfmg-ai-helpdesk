# Email Integration Test Report

Date: 2026-09-21. Scope: one end-to-end send through `HfmgInternalMailProvider` to the HFMG internal mail API. Approved by the user immediately before sending. Related: [../../EMAIL_INTEGRATION.md](../../EMAIL_INTEGRATION.md).

## Result

**The API accepted the request: HTTP 200, body `"Mail sent"`, exactly one call made.**

Not verified by me: that the message reached the recipient's inbox, and which sender address it displays. Please check the mailbox of `Ranga.SaiYashwanth@hfmg.net` (see "Please confirm").

## Configuration used

| Item | Value | Note |
|---|---|---|
| `EMAIL_PROVIDER` | `hfmg_internal` | Set by environment variable for this run only. `backend/.env` still says `sendgrid` and was not changed |
| `ORG_BASE` | `http://172.22.6.188:177` | |
| `DEFAULT_FROM_EMAIL` | empty | Sender fell back to `EMAIL_FROM` |
| Provider selected | `HfmgInternalMailProvider`, `is_configured=true` | |
| Reachability before sending | `GET /` answered (below 500) | Nothing sent by the check |
| Retries | 0 for this run | Guarantees at most one request |

## Request

`POST http://172.22.6.188:177/api/Values/mail/send`, `multipart/form-data`

| Part | Value |
|---|---|
| `To` | `Ranga.SaiYashwanth@hfmg.net` (single part) |
| `Cc` | none (part omitted) |
| `Subject` | `AI Help Desk Email Integration Test` |
| `FromEmail` | `reminder@hfmg.net` |
| `Attachments` | one empty file part: `name="Attachments"; filename=""`, `Content-Type: application/octet-stream`, 0 bytes. No real attachments |
| `BodyHtml` | the approved text, HTML-escaped, each newline as `<br>`, wrapped in `<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">` |

Approved body text:

```
Hello,

This is a test email sent from the AI Help Desk system through the HFMG Internal Mail Provider.

If you received this message, the integration is working correctly.

Regards,
AI Help Desk
```

## Response

```
HTTP status : 200
Response body: "Mail sent"
```

## Logs

Application log lines emitted during the send (the only ones; no errors or warnings):

```
INFO httpx: HTTP Request: POST http://172.22.6.188:177/api/Values/mail/send "HTTP/1.1 200 OK"
INFO hfmg.mail: HFMG mail API: send_email succeeded (HTTP 200)
```

The send ran as a standalone script through the same provider code the backend uses, so these are the backend's own log lines. The running dev server was not involved.

## Issues found

1. **Defect, fixed before sending: the empty `Attachments` part was not a file part.** httpx drops an empty `filename=""`, so the part went out as a plain form field. `app/services/hfmg_mail_service.py` now builds the multipart body itself so the part carries `filename=""`, matching a browser's empty file input. My earlier unit test allowed a missing filename and hid this; it now asserts `name="Attachments"; filename=""` on the wire. All 153 backend tests pass.
2. **Not proven: that the empty-`Attachments` shape matters.** The API accepted the request as sent. I did not send a variant without the part, so this test cannot show whether the part is required.
3. **Sender not verified.** `DEFAULT_FROM_EMAIL` is unset, so `reminder@hfmg.net` was sent as `FromEmail`. The API sends through Microsoft Graph as a mailbox it is signed in as, so the message may display that mailbox rather than `reminder@hfmg.net`. Set `DEFAULT_FROM_EMAIL` to the mailbox the API is authorized for once known.
4. **Success is judged by HTTP 2xx only.** The API documents only `200 OK`; here the body was the plain string `Mail sent`. If it ever returns 200 with an error message, the app would still report success.
5. **`backend/.env` still uses SendGrid.** Ticket notifications continue through SendGrid (skipped while `SENDGRID_API_KEY` is blank) until `EMAIL_PROVIDER=hfmg_internal` and `ORG_BASE` are set there and the backend restarts.
6. **The dashboard Email tile only proves reachability**, not that the mailbox login is valid. This test is the stronger check; repeat it after any mail API restart or re-login.

## Please confirm

- The email arrived in `Ranga.SaiYashwanth@hfmg.net`, and in which folder (Inbox or Junk).
- The **From** address it shows. If it is not `reminder@hfmg.net`, set `DEFAULT_FROM_EMAIL` to what it shows.
- The body renders as five paragraphs with the greeting and sign-off on separate lines.

## Next steps

1. Once delivery is confirmed, set `EMAIL_PROVIDER=hfmg_internal`, `ORG_BASE=http://172.22.6.188:177` and `DEFAULT_FROM_EMAIL` in `backend/.env` and restart the backend.
2. Create a ticket and confirm the notification reaches `HELPDESK_EMAIL`.

---

# Round 2: extended tests (2026-09-21)

Authorized by the user: multiple test emails, to `Ranga.SaiYashwanth@hfmg.net` only. A guard in the test script inspected every outgoing request and would have aborted on any other `To` or `Cc` address. Mailbox contents were not read. Recipients seen by the guard: `Ranga.SaiYashwanth@hfmg.net` only.

## Results

| # | Test | HTTP | Response | Outcome |
|---|---|---|---|---|
| 1 | `send_email`: `To` + `Cc` (same address), a real attachment (`test_attachment.txt`), HTML with bold and a table, Unicode | 200 | `Mail sent` | Accepted |
| 2 | `send_reminder` (`/api/Values/mail/SentAsRemainder`) | 200 | `Mail sent` | Accepted |
| 3 | `send` with the `Attachments` part **omitted entirely** | 200 | `Mail sent` | Accepted, so the part is **not required** |
| 4 | `send` **without `FromEmail`** | 400 | `At least one recipient is required` | Rejected, nothing sent |
| 4b | Same as 4 with a real attachment, and with an empty `FromEmail` | 400 | `At least one recipient is required` | Rejected, nothing sent (confirms the cause) |
| 5 | Simulated new-ticket notification through the app's real provider path (`send_ticket_notification` with `HELPDESK_EMAIL` pointed at the test address), text containing `<script>`, `<b>`, `&`, quotes | 200 | `Mail sent` | Accepted |
| 6 | Mail API unreachable (dead port) | none | | Returned `False`, retried once, logged `ERROR ... unreachable ... ConnectError`, no exception, no email |

Emails actually sent this round: 4 (tests 1, 2, 3, 5). Together with the first test, 5 messages are in the mailbox. The emails carry subjects starting `[AI Help Desk test n/5]` (tests 1 to 3) or `New ticket HFMG-TEST-000001 - Network` (test 5). Test 4's subject never left.

## Findings

1. **Defect, fixed: `FromEmail` is required by the API.** Its 400 message ("At least one recipient is required") is misleading; the recipient was present. `send_email()` used to omit `FromEmail` when neither an argument nor `DEFAULT_FROM_EMAIL` was set. It now falls back to `EMAIL_FROM` (default `reminder@hfmg.net`), and makes no request if there is no sender at all. The provider path was never affected, because it always passes a sender. Two tests were added (155 pass in total).
2. **The `Attachments` part is not required by the API** (test 3). It is still sent by default, as specified, and it is harmless (round 1 and tests 1 and 5 succeeded with it).
3. **Real attachments, `Cc`, and HTML bodies are accepted** (test 1).
4. **The reminder endpoint works** (test 2). It takes no `FromEmail`.
5. **The app's real path works end to end** (test 5): the notification code, the provider, HTML escaping and the API.
6. **Failure handling behaves as designed** (test 6): no exception, a logged error, `False` returned.
7. **`reply_email()` and `forward_email()` were not exercised live.** They need the Graph `MessageItemId` of an existing message, which would mean reading a mailbox. They are covered by unit tests only.

## Please confirm in the mailbox

- Test 1: bold text, a table with two cells, `café ✓`, an attachment named `test_attachment.txt` containing one line, and your own address on both To and Cc.
- Test 3: same rendering, no attachment.
- Test 5: the body shows the literal text `<script>alert(1)</script>` and `<b>tags</b>` as visible characters, not as formatting, and no script runs. The caller line reads `Test <Caller> & Co`.
- The **From** line on all of them. Every request used `FromEmail=reminder@hfmg.net`; if the mailbox shows a different address, the API is overriding it, and `DEFAULT_FROM_EMAIL` should be set to that address.

---

# Round 3: Email tile showing "Down" after successful delivery (2026-09-21)

## Symptom

The Dashboard header strip and System Health panel showed **Email: Down**, although real emails had been delivered through the internal mail API.

## Root cause

The health check was never asked to use the internal provider. It was not a logic error in the internal check; there were two separate configuration/deployment causes, one per environment:

| Environment | What was found | Why the tile said Down |
|---|---|---|
| **Local dev** (`localhost:8000`) | `backend/.env` still has `EMAIL_PROVIDER=sendgrid`, `SENDGRID_API_KEY=` (blank), and no `ORG_BASE`. Verified by reading the settings the app loads with no overrides, and `GET /health/dependencies` returned `"email": {"status": "down"}` | The SendGrid check ran, and a blank key means down. All the successful test emails and round-1/2 checks selected the internal provider with **environment-variable overrides on a separate process**, so the running server and its `.env` never changed |
| **Deployed server** (`172.22.6.98:8001`) | Its `/health/dependencies` also returned `"email": {"status": "down"}`. It runs the branch pushed to GitHub (`0a442bb`), which contains **no** internal-mail code: `hfmg_internal` appears 0 times in `factory.py` and `dependency_health.py`, and `hfmg_mail_service.py` does not exist there. The old rule (`email_provider != "sendgrid"` means down) applies | Any provider value other than `sendgrid` is treated as down, and the `ORG_BASE` setting is ignored. The integration was never committed or pushed. I could not read that server's `.env` |

The rest of the path was verified correct:

| Stage | Result |
|---|---|
| `SystemHealthPanel` / `StatusBar` | Render `data.email.status` unchanged |
| `frontend/src/api/health.ts` | Maps `status` and `checked_at` 1:1 from the response; no email-specific logic |
| `StatusIndicator` | `operational` renders "Operational" (green), `down` renders "Down" (red) |
| `GET /health/dependencies` | Returns the dependency service's result unchanged |
| `dependency_health.check_email()` | Branches on `EMAIL_PROVIDER`; `hfmg_internal` calls `hfmg_mail_service.check_health()` |

## Health status rules for `hfmg_internal` (implemented)

| Status | Condition |
|---|---|
| **Operational** | `EMAIL_PROVIDER=hfmg_internal`, `ORG_BASE` set, and `GET {ORG_BASE}/` answers with any HTTP status below 500 |
| **Down** | `ORG_BASE` empty, connection refused or DNS failure, connect or read timeout, or an HTTP 5xx |

No email is sent by the check. Provider selection alone decides which check runs, so SendGrid settings do not affect an `hfmg_internal` deployment and vice versa.

## Fix

The check logic for `hfmg_internal` already existed and needed no change to its rules. What was added:

1. `check_health()` now **logs the reason** it reports down (`ORG_BASE is not set`, `health check failed: ConnectError`, or `server error HTTP 5xx`). Before, every failure was swallowed silently, which is why this was hard to diagnose. Results are cached for 30 seconds, so it logs about twice a minute at most.
2. Tests (below).
3. **Configuration is the fix for the local server:** set in `backend/.env`, then restart the backend:
   ```
   EMAIL_PROVIDER=hfmg_internal
   ORG_BASE=http://172.22.6.188:177
   DEFAULT_FROM_EMAIL=<mailbox the mail API sends as>
   ```
   Side effect to be aware of: this also switches ticket notification emails to the internal API, so every new ticket will email `HELPDESK_EMAIL` (`helpdesk@hfmg.net`). I did not change `backend/.env` for this reason.
4. **For the deployed server** the code must first be committed, pushed and pulled (not done: no commit was authorized), and then the same three `.env` lines set.

## Tests

- New: the exact response contract the dashboard reads (`email.status == "operational"`), that the check makes only a GET and never POSTs to a mail endpoint, `ORG_BASE` missing gives down with no request, connection refused, connect timeout and HTTP 503 each give down, the failure reason is logged, and a SendGrid deployment is not made "operational" by a reachable `ORG_BASE`.
- Existing tests for the check, endpoint and settings status still apply.
- Full backend suite: **162 passed**.

## Verification (live)

Run without touching `backend/.env`: a second backend instance on port 8002 with overrides `EMAIL_PROVIDER=hfmg_internal`, `ORG_BASE=http://172.22.6.188:177`, `ENABLE_EMAIL_NOTIFICATIONS=false` (so no ticket could send mail), against the real mail API.

`GET /api/v1/health/dependencies`:
```json
{
  "openai":   { "status": "operational" },
  "twilio":   { "status": "down" },
  "database": { "status": "operational" },
  "email":    { "status": "operational" }
}
```

`GET /api/v1/settings/status` email block: `configured: true`, `status: operational`, `masked_key: null`, detail `HFMG internal mail API, from reminder@hfmg.net to helpdesk@hfmg.net`. The internal URL is not exposed.

Dashboard: the production frontend build, pointed at that backend and rendered in headless Edge after its requests completed, showed **Email: Operational** (green) in both the header status strip and the System Health panel, alongside OpenAI Operational, Database Operational and Twilio Down (no credentials yet, expected). The test processes were stopped afterwards; the local dev server on port 8000 was not modified or restarted.

## Remaining items

- The local dev dashboard shows Email Down until `backend/.env` is changed and the backend restarted.
- The deployed dashboard shows Email Down until the code is pushed and pulled, and its `.env` is updated.
- A green tile still means only "the mail API answers". It does not prove the mailbox login is valid; see Round 1, issue 6.

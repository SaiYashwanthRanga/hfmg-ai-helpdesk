# HFMG AI Help Desk — Email Integration

How ticket notification emails are sent, and how to switch between SendGrid and the HFMG internal mail API. For SendGrid account setup see [SENDGRID_SETUP.md](SENDGRID_SETUP.md); for the API-level summary see [API_SPEC.md](API_SPEC.md) section 6.1.

## 1. Overview

Every new ticket and status change emails `HELPDESK_EMAIL`. The transport is chosen by one setting:

| `EMAIL_PROVIDER` | Provider class | Sends through |
|---|---|---|
| `sendgrid` (default) | `SendGridProvider` | SendGrid v3 API |
| `hfmg_internal` | `HfmgInternalMailProvider` | The HFMG internal mail API at `ORG_BASE` |

Both implement `IEmailProvider` (`app/notifications/base.py`). Nothing above that layer, including `app/notifications/email.py`, knows which one is active. Both providers stay in the codebase; switching is a config change plus a restart.

```
send_ticket_notification()  →  get_email_provider()  →  IEmailProvider.send()
                                     │
                     EMAIL_PROVIDER ─┼─ sendgrid       → SendGridProvider
                                     └─ hfmg_internal  → HfmgInternalMailProvider
                                                            → app/services/hfmg_mail_service.py
                                                            → POST {ORG_BASE}/api/Values/mail/send
```

Emails are sent from a FastAPI `BackgroundTask` after the ticket is committed. A failure here can never fail or slow ticket creation.

## 2. Configuration

Set in `backend/.env` (template: `backend/.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `EMAIL_PROVIDER` | `sendgrid` | `sendgrid` or `hfmg_internal`. An unknown value logs an error and falls back to `sendgrid` |
| `ORG_BASE` | empty | Base URL of the internal mail API, for example `http://172.22.6.188:177`. Empty means not configured |
| `DEFAULT_FROM_EMAIL` | empty | Mailbox the internal API sends as. When set it overrides `EMAIL_FROM` for this provider |
| `HFMG_MAIL_TIMEOUT_SECONDS` | `10` | Per-request timeout |
| `HFMG_MAIL_MAX_RETRIES` | `2` | Extra attempts after a connection-level failure |
| `HELPDESK_EMAIL` | `helpdesk@hfmg.net` | Recipient of every notification |
| `EMAIL_FROM` | `reminder@hfmg.net` | Sender for SendGrid; fallback sender for the internal API |
| `ENABLE_EMAIL_NOTIFICATIONS` | `true` | Master switch |

To switch to the internal API:

```
EMAIL_PROVIDER=hfmg_internal
ORG_BASE=http://172.22.6.188:177
DEFAULT_FROM_EMAIL=<mailbox the mail API is logged in as>
```

Restart the backend afterwards. The provider is created once at startup.

## 3. The internal mail API

Endpoints (from its Swagger document, `{ORG_BASE}/swagger/v1/swagger.json`). All take `multipart/form-data`:

| Function in `app/services/hfmg_mail_service.py` | Endpoint | Form fields |
|---|---|---|
| `send_email()` | `POST /api/Values/mail/send` | `To`*, `Cc`*, `Subject`, `BodyHtml`, `FromEmail`, `Attachments` |
| `send_reminder()` | `POST /api/Values/mail/SentAsRemainder` | `To`*, `Cc`*, `Subject`, `BodyHtml`, `Attachments` |
| `reply_email()` | `POST /api/Values/mail/reply` | `MessageItemId`, `ReplyBodyHtml`, `To`*, `Cc`*, `TargetUserEmail`, `IsReplyAll`, `Attachments` |
| `forward_email()` | `POST /api/Values/forwardmail` | `MessageItemId`, `Subject`, `Body`, `ToList`*, `CcList`*, `targetUserEmail`, `Attachments` |

`*` repeated: one form part per address, never a comma-joined string.

Wire rules the service enforces:
- **Multipart always.** List fields are repeated once per address.
- **`Attachments` is always sent.** With no files it is one empty file part (empty filename, empty content). With files, one part per file. The API does not require the part (a send without it succeeded), but it is sent by default as specified.
- **`FromEmail` is required by `/mail/send`.** Without it the API answers `400 At least one recipient is required`, a misleading message. `send_email()` therefore always supplies one: the `from_email` argument, else `DEFAULT_FROM_EMAIL`, else `EMAIL_FROM`.
- **Field names are the API's own**, including the lowercase `targetUserEmail` on forward and the spelling `SentAsRemainder`.
- **Bodies are HTML.** The ticket notification builds plain text, and `HfmgInternalMailProvider` HTML-escapes it and converts line breaks to `<br>`, so caller-supplied text cannot become markup. If you call `send_email()` or the other functions directly, escape untrusted text yourself.
- **Subjects are collapsed to one line** by the provider, so a CR/LF in a subject cannot add mail headers.

Only `send_email()` is used by the app today (through the provider). `send_reminder()`, `reply_email()` and `forward_email()` are implemented and tested for future use. Nothing in the app calls them yet.

### Authentication

The API declares no auth scheme, and `GET` calls to it are answered without credentials. It sends through Microsoft Graph as a mailbox that must already have been authorized through the API's own login flow (`/api/auth/login`, a Microsoft sign-in). That login is managed outside this project. Consequences:
- If that mailbox login lapses, sends will fail even while the health tile is green (see section 5).
- Anyone who can reach `ORG_BASE` can call it. Keep both it and this app on the internal network.

## 4. Failure behavior

Every function returns `True` on success and `False` on any failure, and none raises.

| Situation | Behavior |
|---|---|
| `ORG_BASE` empty | Warning logged, nothing sent, returns `False` |
| No recipient, or a missing required id | Warning logged, no request made |
| API unreachable (connection refused, DNS, connect timeout) | Retried up to `HFMG_MAIL_MAX_RETRIES` with backoff, then an `ERROR` is logged and `False` returned |
| Read timeout | **Not retried**, `ERROR` logged. The API may already have handed the message to Graph, and a retry would send a duplicate |
| HTTP 4xx or 5xx | **Not retried**, `ERROR` logged with the status code only |
| Any other exception | Logged with traceback, returns `False` |

Logs never contain recipients, subjects, bodies or response bodies, only the action name, HTTP status and error class. A ticket whose notification fails is still created normally. There is no retry queue, so a notification that fails is not re-sent later.

## 5. Health check

`GET /api/v1/health/dependencies` and the dashboard **Email** tile use `hfmg_mail_service.check_health()` when `EMAIL_PROVIDER=hfmg_internal`:

- `GET {ORG_BASE}/`. Any HTTP answer below 500 is `operational` (the API's root returns 404, which still proves it is up). A connection failure, timeout or 5xx is `down`. `ORG_BASE` empty is `down`.
- Nothing is sent and no credentials are used, and results are cached for 30 seconds like the other checks.
- **Limit:** a green tile means the host is reachable, not that the mailbox login is valid. Confirm with a real test send (section 6).
- **Rules:** operational needs `EMAIL_PROVIDER=hfmg_internal`, `ORG_BASE` set, and an HTTP answer below 500. Down is a missing `ORG_BASE`, an unreachable host, a timeout or a 5xx. The reason is logged (`HFMG mail API health check ...`), so a red tile can be traced in the backend log.
- **A red tile after switching usually means the running backend does not have the setting yet.** `EMAIL_PROVIDER` and `ORG_BASE` are read from `backend/.env` at startup, and the deployed code must include this integration. See Round 3 of [docs/reviews/EMAIL_TEST_REPORT.md](docs/reviews/EMAIL_TEST_REPORT.md).

`GET /api/v1/settings/status` shows the provider as configured when `ORG_BASE` is set, with `masked_key: null` and a detail line naming the sender. The internal URL is not exposed there.

## 6. Verifying it end to end

Send a test message to yourself. Run this from `backend/` with the virtualenv active. It sends one real email:

```powershell
$env:ORG_BASE = "http://172.22.6.188:177"
python -c "import asyncio; from app.services import hfmg_mail_service as m; print(asyncio.run(m.send_email(to='YOU@hfmg.net', subject='HFMG Help Desk test', body_html='<p>Test from the AI Help Desk.</p>', from_email='MAILBOX@hfmg.net')))"
```

`True` and a message in your inbox means the whole path works. `False` means check the backend log line that starts with `HFMG mail API`. Then set `EMAIL_PROVIDER=hfmg_internal`, restart, create a ticket, and confirm the email reaches `HELPDESK_EMAIL`.

## 7. Tests

`backend/tests/test_hfmg_mail.py` (37 tests) mocks the network with `httpx.MockTransport`, so nothing contacts the real API or sends email. It covers: multipart shape and repeated `To`/`Cc`, the always-present `Attachments` part, all four endpoints and their field names, retry rules, graceful failure and log hygiene, the health check, the provider's HTML escaping and sender precedence, factory switching, the settings endpoint, and an end-to-end ticket notification through the internal provider. `tests/conftest.py` forces `EMAIL_PROVIDER=sendgrid` and an empty `ORG_BASE` for every test, so a developer `.env` can never make the suite send real mail.

## 8. Known limitations

- No retry queue: a notification lost during an outage is not re-sent.
- The health tile cannot detect an expired mailbox login.
- Success is judged by HTTP 2xx only, because the API documents just `200 OK`. If it ever returns 200 with an error in the body, the app would report success.
- Live tests on 2026-09-21 ([docs/reviews/EMAIL_TEST_REPORT.md](docs/reviews/EMAIL_TEST_REPORT.md)) covered send, reminder, Cc, a real attachment, HTML, and a full ticket notification. `reply_email()` and `forward_email()` have only been unit-tested, because they need the Graph id of an existing message.
- Which address the recipient sees in the From line has not been confirmed. The API sends through Microsoft Graph as a signed-in mailbox and may override `FromEmail`.
- The multipart body is built by hand rather than by httpx, because httpx drops an empty `filename=""` and would send the empty `Attachments` part as a plain form field.

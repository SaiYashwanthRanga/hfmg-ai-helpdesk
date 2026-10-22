# IT Ticket Analysis Reports

An IT team member uploads a Freshworks ticket export, the AI prioritizes the open tickets, they review a short,
employee-wise action report on the dashboard, and — only when they press **Send Email** — the same report goes to the IT
team's distribution list. The report says what to work on next and who has what; it is not a copy of the ticket data.

Dashboard: **AI Insights → Analyze Freshworks tickets** (`/ai-insights/ticket-analysis`).

## Setup

It is **off by default**. This API has no authentication (like every other route here), so with the feature on, anyone who
can reach the server can use AI credit and email the IT distribution list. Turn it on only on a trusted network.

```
ENABLE_TICKET_REPORTS=true
IT_REPORT_DISTRIBUTION_EMAIL=it-team@hfmg.net      # the IT team's distribution list
```

For testing, several addresses can be listed, separated by commas (at most 10): one email is sent, addressed to all of them.
Each must be valid, and a repeated address is dropped.

It also needs what the rest of the app already uses:

- an AI provider (`LLM_PROVIDER=openai` and `OPENAI_API_KEY`), and
- an email provider (`EMAIL_PROVIDER=sendgrid` with `SENDGRID_API_KEY`, or `hfmg_internal` with `ORG_BASE`).

Run `alembic upgrade head` (adds the `ticket_reports` table). Optional limits are in `backend/.env.example`
(`TICKET_REPORT_*`, including `TICKET_REPORT_EMAIL_MEDIUM_ITEMS`). The recipient is read only from the backend's configuration — it is shown in the confirmation dialog
but can never be chosen in the browser.

## Deploying to the server

After `git pull` of this branch (`feature/ticket-analysis-report`, or `main` once it is merged), on the server:

1. **Python packages:** `pip install -r requirements.txt` in `backend/`. New: `openpyxl` (reads `.xlsx`) and `python-multipart`
   (file uploads; the call simulator already needed it).
2. **Database:** `alembic upgrade head` in `backend/`. This branch carries two new migrations, applied in order on top of
   whatever the server has: `d8f3b6a2c415` (the `PROVIDER` caller type and the *Providers* category, for the voice flow in
   `CALL_FLOW.md` 2.5, which stays off unless `VOICE_PROVIDER_FLOW=true`) and `e9a4c7b13d62` (the `ticket_reports` table).
   They only add things; no existing data is changed. A server that is further behind also gets the older pending ones.
3. **`.env`** (see `backend/.env.example`):
   - `ENABLE_TICKET_REPORTS=true` (off by default) and `IT_REPORT_DISTRIBUTION_EMAIL=` the IT team's address (several, comma
     separated, are allowed for testing: one email goes to all of them).
   - Already needed by the app: `OPENAI_API_KEY`, and the email provider (`EMAIL_PROVIDER` with its credentials).
   - Optional: `TICKET_REPORT_*` limits (upload size and rows, batch sizes, retention, how many Medium tickets the dashboard's
     short list holds).
4. **Dashboard:** no new npm packages. Rebuild it (`npm run build` in `frontend/`) so the new page is served; run `npm ci`
   first only if `node_modules` is missing.
5. **Restart** the backend.

What changes for users: **AI Insights → Analyze Freshworks tickets** is a new page (upload an export, *Analyze with AI*, review,
then *Send Email*). Nothing else in the dashboard, the ticket flow, or the voice agent changes. A report saved by an earlier
build of this feature has an older layout and cannot be sent: upload the file again.

## Using it

1. **Upload** a `.csv` or `.xlsx` export. The page shows the file name, row count, how each column was read, and any
   problems. **Analyze with AI** stays disabled until the file is usable. Replace the file at any time before analyzing.
2. **Analyze with AI.** Progress is shown while it runs (you can leave the page; the report is waiting when you return).
3. **Review** the report (layout below).
4. **Send Email.** A dialog shows the recipient and a summary; nothing is sent until you confirm. The delivery status and
   time are shown once the server confirms. After a failed send, the button becomes **Retry Send Email**.

`.xls` (old Excel) is not supported; save it as `.xlsx` or `.csv`.

## The report

**Summary** — six figures and at most three short statements:

| Figure | Where it comes from |
|---|---|
| Total tickets | the file |
| Open / unresolved | the file: open tickets plus any whose status could not be read |
| Overdue | the file: an *open* ticket whose *Due by* time has passed |
| Unassigned | the file: not resolved and no agent. Shown as "Unassigned", never as a person |
| AI Critical, AI High | the AI's recommendations, counted after validation (of the tickets it analyzed) |

The statements are written from those numbers, for example "*N* open tickets; *M* overdue", who has the highest
workload, and what to review first. None is fixed text, and a figure that cannot be calculated says so.

**Employee workload** (the main section) — Employee | Open tickets | Overdue | AI Critical | AI High | Remaining.
Every open ticket is in exactly one row, so the rows add up to the summary's open total; tickets with no agent are in an
**Unassigned** row. Busiest first: Critical plus High, then overdue, then total. *Remaining* is every open ticket that is
not AI Critical or High (including any the AI did not analyze).

**Action items** — Ticket | Assigned to | Subject | Freshworks priority | AI priority | Why | Next action. AI Critical first,
then High, Medium, Low; within a level, overdue first, then the oldest. By default it shows the short list: every
Critical and High ticket plus the first `TICKET_REPORT_EMAIL_MEDIUM_ITEMS` (5) Medium ones (the setting only affects the dashboard now). *Show all* opens every open
ticket, with search and filters. With no Critical or High, it says so and lists the top tickets by overdue status and the
other evidence — it never makes up a Critical or High. The reason and the next action are one short sentence each.

**Recurring issues** (collapsed, optional) — at most three, each with its ticket IDs and one short note, worded as a
possibility. **Data limitations** (collapsed) — what the file could not tell us, and the assumptions behind the numbers.

### Weekly follow-up

Right after the summary, the dashboard groups the open tickets for the IT team's weekly follow-up. **The email carries
*Waiting on vendor* and *OM/User issues*** (see below); the *Priority issues* group is dashboard-only. A ticket can appear
in more than one group.

| Group | What it holds | Columns |
|---|---|---|
| **Waiting on vendor** | Tickets that *show* they are blocked on an outside organization: a vendor was contacted and a reply is pending, an external support case or RMA is open, a replacement / repair / vendor action is pending, or the next step depends on an outside party. A status such as "Waiting on Third Party" counts as evidence | Ticket, Assigned to, Issue, Days open, Status, Evidence, Next action |
| **OM/User issues** | Issues affecting a healthcare provider's ability to work: cannot reach eClinicalWorks or another clinical system, login / authentication / permissions, a clinical workstation or application, patient visits or documentation | Ticket, Assigned to, OM/User issue, Days open, Business impact, AI priority, Next action |
| **Priority issues affecting daily work** | The tickets the AI recommended as Critical or High, overdue first, then the oldest | Ticket, Assigned to, Issue, Days open, AI priority, Freshworks priority, Why, Next action |

- **Evidence is required.** A ticket that only *mentions* a vendor is **Needs review**, not Waiting on vendor; if the AI says
  "waiting" without giving evidence, the report downgrades it to Needs review. Mentioning a doctor does not make a ticket a
  OM/user issue, or urgent: the impact the ticket describes is what counts, and "Impact not stated" is shown when it says none.
- **How it is found.** A second AI pass reads each open ticket (the same set as the priorities, up to
  `TICKET_REPORT_MAX_AI_TICKETS`) with a longer excerpt of its description: up to about 2,200 characters, **the start and the
  end** of a long text, since a pasted email thread says where things stand at the end. It also sees the Status and the Tags
  column. It is separate from the priority pass, so the priorities are unchanged, and if it fails the rest of the report is
  intact: the vendor and OM/user groups say so instead of looking empty. It makes extra AI requests (five tickets each;
  `TICKET_REPORT_FOLLOWUP_BATCH_SIZE`).
- **Days open** is counted from the Created time to the time of the analysis; "Not available" without one.
- **Sorted:** Waiting on vendor before Needs review, then longest open first; OM/user issues by AI priority, then longest open.
- The export has no conversation or notes column, so evidence comes from the Description, Status and Tags. A file with little
  of that will show few or no vendor tickets; that is the rule working, not a fault.

### How the AI prioritizes

For each open ticket the AI sees the subject, a shortened description, the Freshworks priority and status, impact and
urgency when the export has them, age, and whether (and by how long) it is overdue or breached its SLA. It weighs whether
patient care or a provider's essential work is blocked, whether employees cannot work, how many people are affected,
security and access risk, outages, and the due date and age. Its meanings:

- **Critical** — a major outage, a serious security incident, or an immediate and substantial disruption to patient-care operations.
- **High** — a significant operational blockage, a provider unable to do essential work, or a widespread service disruption.
- **Medium** — meaningful impact, with a workaround available.
- **Low** — a routine request, a minor inconvenience, or non-urgent maintenance.

Being overdue is evidence, not a verdict, and a Freshworks "Low" does not make a ticket low impact. When a ticket says too
little to prioritize with confidence, the AI says what is missing and the report marks it **Limited information**; nothing
is invented. These are recommendations for a person to review: the Freshworks priority and status are kept as exported,
the AI's priority is stored separately, and nothing is written back to Freshworks or reassigned.

A number the export cannot establish is shown as **Not available**, never as 0. Open and resolved come from the Status
column (a resolution date only when the status is not recognized; an unreadable status counts as neither, and is included
in the open total). An old ticket is not assumed to be overdue: it needs a *Due by* time that has passed.

## The email

Subject **HFMG IT Ticket Priorities and Workload Report**, built from the saved analysis (the AI is not called again). It
contains **four sections and nothing else**, and it says **what the issues are, never what to do about them**: the IT team
knows the next steps, so there is no next-action or follow-up column anywhere in the email (the dashboard keeps them). The
ticket-by-ticket analysis, priority issues, recurring issues, summary statements and data limitations also stay in the dashboard.

- **Team Summary** — Metric | Count: Open Tickets, Overdue Tickets, Unassigned Tickets, AI Critical, AI High.
- **Employee Workload** — Employee | Unresolved | Critical/High | Overdue | Observation, one row per employee. *Critical/High*
  is the tickets the AI recommended as Critical or High. *Observation* is calculated ("17 unresolved tickets (34% of all
  unresolved tickets); 13 overdue; oldest is 22 days old."). Tickets with no agent are the **No Agent** row (the dashboard
  calls it "Unassigned").
- **Waiting on Vendor** — Ticket ID | Issue | Assigned To | Days Open | Status | Evidence. *Status* is Waiting on Vendor, or
  Needs Review for a ticket that only hints at a vendor.
- **OM/User Issues** — Ticket ID | OM/User Issue | Assigned To | Days Open | Business Impact.

The two issue sections list the same tickets, in the same order, as the dashboard (a ticket with no agent shows "No Agent").
An empty group says so in a sentence ("No ticket shows evidence of waiting on a vendor."). If the vendor/OM-user check
failed, or the report was saved before it existed, the sections say "Not available" rather than looking empty; a partial
check says how many tickets it covered. At most 25 rows are listed per section, with a pointer to the dashboard.

The email is signed as the help desk agent that sends it ("Regards, HFMG AI Help Desk Agent, Horizon Family Medical Group IT
Help Desk"), in both the HTML and the plain-text version; it is a sign-off, not a section. Under the title: "As of *date*
(UTC). AI recommendations are for a person to review; no Freshworks ticket was changed." A
figure that cannot be calculated says "Not available" (and with no Assigned-to column the workload section says so). It is
plain nested HTML tables (Outlook-safe) with a plain-text version. A report saved with an older layout is not sent; upload the
file again.

## Data handling

- **Never read:** requester names, emails and phone numbers (those columns are listed as ignored).
- **Sent to the AI:** ticket ID, subject, a shortened description, Freshworks priority and status, category, impact and
  urgency, age, and whether it is overdue. The weekly follow-up pass also gets a longer, masked excerpt of the description and
  the Tags. Agent names are never sent: for the follow-ups the AI sees each employee only as
  `E1`, `E2` … with counts (unresolved, Critical/High, overdue, oldest age), and the answers are mapped back afterwards.
- **Masked** in subjects and descriptions before storing: emails, phone numbers, SSNs, dates of birth, record / account /
  member numbers. A patient's *name* written in free text cannot be recognized and may remain; the report says so.
- **Ticket text is treated as untrusted:** it is fenced as data in the prompt, and the answers are validated — a ticket ID
  the model invents is dropped, an invalid priority is dropped, a recurring issue must name at least two real tickets.
- **Stored:** the uploaded rows only until the analysis finishes, then deleted. The saved report keeps subjects and the
  results for open tickets (resolved tickets are only counted). Reports are deleted `TICKET_REPORT_RETENTION_DAYS` (30)
  after upload. Logs carry ticket counts and statuses, never ticket text or addresses.
- **Limits:** 5 MB, 2000 rows, 200 columns; at most 120 open tickets go to the AI (overdue, then higher Freshworks priority,
  then oldest first; the rest are counted and listed as not analyzed), in batches of 8, three at a time.

## Sending is safe to repeat

- Starting an analysis and starting a send are single conditional updates in the database, so a double click, a retried
  request or a second tab runs/sends **once**. A second send after success sends nothing and says so.
- The send is recorded as *sent* only after the mail service accepts it. A failure is recorded as failed ("Nothing was
  sent"), and can be retried.
- If the server stops in the middle of a send, the report stays "sending" and may be retried after
  `TICKET_REPORT_SEND_STALE_SECONDS` (10 minutes) — that earlier attempt may or may not have been delivered.
- Sending uses the saved analysis; the AI is not called again.

## API

All under `/api/v1/reports`; every route except `/config` returns 404 while the feature is off.

| | |
|---|---|
| `GET /config` | enabled, limits, whether the AI and email are configured, the recipient |
| `POST /upload` | multipart `file` → 201 with the report id, columns, row count, validation. 413 too big, 415 wrong type, 422 unreadable |
| `POST /{id}/analyze` | 202, analysis runs in the background; 409 if already running or done; 422 if the file has errors; 429 if others are running |
| `GET /{id}` | status, progress, validation, the analysis, email status (`can_send` / `blocked_reason`) |
| `POST /{id}/send` | 200 sent (or already sent); 409 not ready or already sending; 503 email not configured; 502 the mail service refused |

## Known limitations

- No authentication (see Setup). Treat it like the other dashboard actions: trusted network only.
- The `hfmg_internal` mail API accepts one HTML body, so that provider sends the HTML version only; SendGrid sends plain
  text and HTML.
- Recurring issues cover open and unresolved tickets only; resolved tickets are counted, not analyzed.
- Slash dates are read month/day/year, and dates without a time zone as UTC; the report lists this as an assumption.
- Uploaded cells are never written to a spreadsheet, so there is no CSV-formula-injection exposure; all text shown or
  emailed is HTML-escaped.

## Tests

`backend/tests/test_ticket_reports.py` (upload and parsing, the figures, the AI step with a scripted model, sending with a
fake mail provider) and the migration tests in `test_voice_state_migrations.py`; `frontend/tests/ticketReport.test.ts` for
the page's logic. The AI and the mail service are replaced by fakes in the tests, so no test calls OpenAI or sends email.

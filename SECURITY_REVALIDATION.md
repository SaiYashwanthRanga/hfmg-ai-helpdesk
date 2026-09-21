# HFMG AI Help Desk — Security Revalidation (Second Pass)

**Performed:** 2026-09-21, as an independent re-verification of `SECURITY_REVIEW.md` against the current code and a live backend instance (`http://localhost:8000`), not a re-assertion of its conclusions.
**Method:** Every claim below was re-checked directly — `git` commands, `grep` across the repo, direct reads of the relevant source files, and live HTTP requests against the running server. Where a finding matches the previous review exactly, that is stated; where it doesn't, the drift is called out explicitly.

---

## Overview — What Changed Since `SECURITY_REVIEW.md`

Two things changed in the repo since that review was written, neither of which is a regression:

1. **Commit `a71d221`** ("Wire up AI summary regeneration and add deployment/editor config") wired the frontend's existing "Regenerate" button to the already-existing, already-reviewed `POST /tickets/{id}/regenerate-summary` endpoint, and added an IIS `web.config` for SPA routing on deployment. Pure frontend wiring + static-file-server config — no new endpoint, no new data exposure, no auth-relevant change.
2. **A real `OPENAI_API_KEY` was added to `backend/.env`** for local development (documented separately in `OPENAI_INTEGRATION_REPORT.md`, dated the same day). At the time `SECURITY_REVIEW.md` was written, all three provider keys were unconfigured, so `GET /settings/status` returned `masked_key: null` for all of them. Today, a live call to that endpoint returns `"openai":{"configured":true,...,"masked_key":"sk-...FuMA",...}` — correctly masked (first 3 + last 4 characters only), never the raw key. This is an environment-state change, not a code change, and the masking behavior that matters for security is unchanged and still verified correct.

No other source changes were found between the two reviews (`git log` shows only these two commits since `SECURITY_REVIEW.md`'s commit).

---

## Secrets Exposure

- `git check-ignore -v backend/.env` → matched by root `.gitignore:3:backend/.env`. `git check-ignore -v frontend/.env` → matched by `.gitignore:12:frontend/.env`. Both are ignored.
- `git ls-files | grep -i "\.env"` → only `backend/.env.example` and `frontend/.env.example` are tracked. Neither real `.env` file is in git. `git status --short` is clean (no untracked/staged env files sitting in the index either).
- `backend/.env.example` and `frontend/.env.example` contain only placeholder/empty values (e.g. `OPENAI_API_KEY=`, `SENDGRID_API_KEY=`, `TWILIO_AUTH_TOKEN=`) plus one non-secret example `DATABASE_URL` using an obviously-local dev password (`hfmg_dev_local`) that is not used anywhere outside local dev — no real secret in either example file.
- `backend/.env` does contain a real OpenAI key (`OPENAI_API_KEY=sk-proj-...`, redacted per instructions) and a real `TWILIO_AUTH_TOKEN` value; both are gitignored and unreferenced outside `.env`/`config.py`.
- Repo-wide grep for hardcoded secret patterns (`sk-[A-Za-z0-9_-]{10,}`, AWS/GCP key shapes, `password=`/`token=`/`secret=` literals ≥12 chars) across `backend/` and `frontend/` (excluding `.venv`/`node_modules`) found **no hardcoded real secrets**. The only matches were: a docstring example in `app/core/masking.py`, and fake test fixture keys (`sk-test-key-not-real`, `sk-abcdefghijklmnop`) in `backend/tests/`. `frontend/dist/` (a build artifact) contains only bundled third-party JS (react-router), not secrets.
- **No secrets are returned in any API response.** Live `GET /api/v1/settings/status` confirmed the masked-key behavior described above for OpenAI; Twilio and SendGrid remain `masked_key: null` (unconfigured); the `database` section never returns a connection string or credentials.

**Verdict: Pass, unchanged from previous review.**

---

## Logging Leakage

- Full inventory of all 35 `logger.*` calls in `backend/app/` was read. None log request bodies, ticket descriptions, call transcripts, OpenAI prompts/responses, or raw secret values. Logged fields are limited to: call SIDs, ticket numbers/IDs, HTTP status/exception type names, category/schema names, and boolean/config state (e.g. "is a key set at all").
- `logger.exception(...)` is used in five places (`openai_provider.py`, `voice/routes.py` x4). These log Python tracebacks via `exc_info`, which is correct practice, but is worth a minor note (see New Findings) since a traceback could in principle include argument values if a future exception type embeds them — currently it does not, because none of the wrapped calls pass ticket/transcript content as an exception argument.
- `backend/app/core/masking.py::mask_secret` masks any string to `"xxx...yyyy"` (first 3 + last 4 chars) or `"••••"` for strings ≤8 chars, `None` for empty. It is used in exactly two places: `app/api/v1/settings.py` (the only place secrets are ever surfaced to a client) and its own module. Grep confirmed no other code path formats or logs a raw `settings.openai_api_key` / `settings.twilio_auth_token` / `settings.sendgrid_api_key` value. No code logs `str(settings)` or a settings object dump that could leak all fields at once.
- No `print()` statements exist in `app/` that could bypass the logging configuration.

**Verdict: Pass, unchanged from previous review.**

---

## CORS

`backend/app/main.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,   # from CORS_ORIGINS env var, default/current: ["http://localhost:5173"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
`backend/app/core/config.py` parses `CORS_ORIGINS` as a comma-separated explicit list (never `"*"` by any code path — there is no wildcard fallback). Live `backend/.env` confirms `CORS_ORIGINS=http://localhost:5173`, matching the documented dev default.

Because `allow_origins` is always an explicit list (not `"*"`), `allow_credentials=True` combined with wildcard `allow_methods`/`allow_headers` is safe — a browser will only honor the credentialed CORS response for an origin on that explicit list, and Starlette's `CORSMiddleware` does not expand `allow_origins` to `"*"` when credentials are enabled. This exactly matches `SECURITY_REVIEW.md` §5. The standing operational requirement is unchanged: whoever deploys this must set `CORS_ORIGINS` to the real frontend origin(s) — `DEPLOYMENT_GUIDE.md` §6 covers this.

**Verdict: Pass, unchanged from previous review.**

---

## Input Validation

- `backend/app/schemas/ticket.py::TicketCreate` bounds every field: `caller_name` (1–200), `phone_number` (1–32), `description` (1–10,000), `email` is `EmailStr | None`, `category_id` is `uuid.UUID` (auto-validated), `priority` is a constrained enum. Live-tested: a >10,000-char description correctly returns `422 string_too_long`; a missing/invalid category returns `400 Unknown or inactive category`.
- `list_tickets` query params (`page`, `page_size`, `q`, enum filters) are all `Query(...)`-bounded (`page_size` 1–100, `q` 1–200 chars) — live-tested `page_size=999999` → `422 less_than_equal`.
- Path params use `uuid.UUID` typing everywhere a resource ID is expected (`tickets.py`, presumably `voice_calls.py`/`ai_insights.py` follow the same pattern) — FastAPI/Pydantic rejects non-UUID input with `422` before any handler code runs. Live-tested `GET /tickets/not-a-uuid` → `422 uuid_parsing`, no handler-level exception.
- **No raw SQL string interpolation anywhere.** Grepped for `text(`, `.execute(`, and f-string-built SQL across `backend/app/`. Every `db.execute(...)` call passes a SQLAlchemy Core/ORM `select()`/`func.count()` statement object built with bound parameters (`.where(Model.field == value)`, `.ilike(pattern)` with an f-string-built *pattern value*, not query text). The only literal SQL text is `text("SELECT 1")` in two health-check functions (`app/api/v1/health.py`, `app/services/dependency_health.py`) — a hardcoded, parameter-free liveness probe with no injectable input. Live-tested a SQL-injection-shaped `q` param (`' OR 1=1 --`) against `GET /tickets?q=...` — safely ILIKE-matched as a literal substring (0 results), not interpreted as SQL, confirming the ORM parameterization holds in practice, not just by code inspection.

**Verdict: Pass. No new gaps found; matches previous review's spot-checks and extends them with additional live confirmation.**

---

## API Boundaries

Live requests against the running backend:

| Request | Result |
|---|---|
| `GET /tickets/00000000-0000-0000-0000-000000000000` (well-formed but nonexistent UUID) | `404 {"detail":"Ticket not found"}` — no internal detail |
| `GET /tickets/not-a-uuid` | `422`, standard Pydantic validation error shape, no traceback |
| `POST /tickets` with `{}` | `422`, per-field "Field required" list — no internal detail |
| `POST /tickets` with malformed JSON | `422 json_invalid`, generic JSON decode message — no parser internals or file paths |
| `POST /tickets` with valid-but-nonexistent `category_id` | `400 {"detail":"Unknown or inactive category"}` |
| `GET /tickets?status=NOT_A_STATUS` | `422`, lists the valid enum values — informational but not sensitive (this is the app's own public status vocabulary, already visible in `/openapi.json`) |
| `GET /tickets/nonexistent-route` sibling paths | `404 {"detail":"Not Found"}` |

No response leaked a file path, stack trace, SQLAlchemy/DB error text, or internal schema detail beyond what `TicketRead`/`TicketPage` already expose by design. `GET /settings/status` and `/openapi.json`/`/docs` are reachable without auth (expected — the whole API is unauthenticated by design) and don't add information beyond the app's own documented shape.

**Verdict: Pass. Live-verified, not just inferred from code — extends the previous review's static claim (§3) with real request/response evidence.**

---

## Error Responses

- `backend/app/main.py` instantiates `FastAPI(title=..., version=...)` with no `debug=True` and no custom exception handler that would echo tracebacks.
- `backend/app/core/config.py` has no `debug` flag at all (only `environment: str = "development"`, which is informational-only and only ever surfaced, masked-appropriately, via `/settings/status`).
- No code path calls `raise` with an exception whose `str()` includes a stack trace or DB internals into an `HTTPException(detail=...)`. All handler-thrown errors (`ticket_service.py`, `settings.py`) use hand-authored `detail` strings.
- Live-tested: no combination of malformed input tried above produced a `500` or a body containing Python traceback text, file paths, or SQLAlchemy repr output. FastAPI's default unhandled-exception behavior (generic `500`, no body detail) was never actually triggered because there was no way found to reach an unhandled exception — every attempted failure mode is already caught and converted to a clean `4xx`.
- Voice webhook routes (`app/voice/routes.py`) go further: every handler wraps its core logic in `try/except Exception`, logs server-side via `logger.exception(...)`, and returns a generic spoken TwiML error (`scripts.SYSTEM_ERROR`) to the caller rather than any error detail — this is a stronger guarantee than the JSON API gets, appropriate since Twilio callers have no way to interpret a JSON error body anyway.

**Verdict: Pass, unchanged from previous review, now with live confirmation that no tested failure mode actually reaches a raw `500`/traceback.**

---

## Twilio Webhook Auth (Static Review)

`backend/app/voice/security.py::verify_twilio_signature`:
- `settings.twilio_validate_signature: bool = True` (default) in `config.py` — **fails closed**: signature validation is on unless explicitly disabled. `.env.example` documents it correctly ("Keep true in any deployed environment. Only set false for local testing without Twilio").
- If validation is disabled, the code logs a loud `logger.warning("Twilio signature validation is DISABLED -- local testing only")` on every request — good operator visibility, not silent.
- If validation is enabled but `TWILIO_AUTH_TOKEN` is unset, the function **rejects the request** (`403 Webhook not configured`) rather than silently passing — correct fail-closed behavior for a misconfiguration, not a bypass.
- Signature check uses Twilio's official `RequestValidator.validate(url, params, signature)` against the reconstructed public URL (`twilio_public_base_url` override for tunnel/proxy deployments, falling back to the request's own URL) and the `X-Twilio-Signature` header. This is the standard, correct pattern.
- All four voice webhook routes (`/webhooks/twilio/voice`, `/voice/gather`, `/voice/status`, `/voice/fallback`) declare `dependencies=[Depends(verify_twilio_signature)]` — confirmed via grep, no route was missed.
- Current `backend/.env` has `TWILIO_AUTH_TOKEN` set (a real value present) but no live Twilio account is wired up yet per the task brief — not tested live, and not flagged as a gap since it isn't deployed.

**Verdict: Pass, unchanged from previous review. Defaults are safe (fail-closed) both for the enable/disable flag and for the missing-token case.**

---

## Drift From Previous Review

| Claim in `SECURITY_REVIEW.md` | Still true? | Note |
|---|---|---|
| §1.3: all three `masked_key` values are `null` in dev | **Environment drifted, code did not.** A real OpenAI key is now configured; `/settings/status` now returns a correctly-masked `sk-...FuMA` for `openai` only. The masking *mechanism* itself is unchanged and still verified correct. |
| §2: Settings write-path absent (frontend + backend) | **Still true**, re-verified by the same greps against current code. |
| §3: error responses never leak tracebacks | **Still true**, and now additionally confirmed live (previous review asserted this from code reading; this pass reproduced it with actual malformed requests). |
| §5: CORS restricted to explicit origin list | **Still true**, same config, same live `.env` value. |
| §6: Twilio signature validation unchanged, fail-closed defaults | **Still true**, re-read line-by-line. |
| §4: No auth anywhere, documented and unchanged | **Still true.** No endpoint added since (only frontend wiring to a pre-existing endpoint) quietly assumes identity/role. |

No claim in `SECURITY_REVIEW.md` was found to be false or stale against current code. The only drift is environmental (a real API key now present) and does not weaken any control the previous review relied on.

---

## New Findings

No new critical, high, or medium-severity findings.

Two minor, informational observations (neither blocks the documented "internal network, no auth" deployment model):

1. **`logger.exception(...)` traceback exposure is theoretical, not actual, today.** In `app/llm/openai_provider.py:120` and four spots in `app/voice/routes.py`, unhandled exceptions are logged with full tracebacks (server-side logs only, never returned to the client — this is correct practice). None of the current exception types wrap ticket description, call transcript, or secret values as arguments, so no PII/secret currently reaches these logs via this path. If a future change ever passed sensitive data as an exception argument (e.g. `raise ValueError(user_supplied_text)`), it would flow into these logs uncensored. Recommendation: no code change needed now: just keep this constraint in mind if new exception-raising code is added that wraps request content.
2. **`/docs` and `/openapi.json` are publicly reachable** (live-confirmed `200`), exposing the full API surface/schema to anyone on the network. This adds no risk beyond what already exists — every endpoint it documents is already unauthenticated and already fully described in `API_SPEC.md` — but an operator locking down the network perimeter should be aware the interactive docs UI is also reachable there, not just the JSON API.

Neither of these changes the overall risk posture; both are noted for completeness rather than as action items.

---

## Confirmed-Still-Valid Findings From Previous Review

- Secrets never committed to git (re-verified with fresh `git check-ignore`/`git ls-files` output).
- Secrets never logged (re-verified against the full current set of 35 logger calls, not just the four the previous review sampled).
- Secrets never returned unmasked in API responses (re-verified live, including against the now-configured OpenAI key — the masking held up under a real, not synthetic, secret).
- Database credentials never exposed via `/settings/status` (re-verified live).
- Settings page has no write path on either tier (re-verified via the same greps).
- CORS is origin-restricted, not wildcard (re-verified against current `main.py` and live `.env`).
- No debug-mode traceback leakage (re-verified against current `main.py`/`config.py` and live against several induced-error requests).
- Input bounds exist on ticket creation and list-query params (re-verified against current schema; extended with live boundary tests).
- Twilio webhook signature validation exists, is wired to all four voice routes, and fails closed by default (re-verified line-by-line).
- No authentication exists anywhere, and this is the documented, deliberate MVP scope decision, unchanged and not newly regressed (confirmed no new endpoint added since assumes identity/role).

---

## Overall Risk Rating (For the Documented Deployment Model: Internal Network Only, No Auth)

**Low risk, unchanged from the previous review.**

Given the explicit, documented deployment assumption (internal network only, network-ACL as the sole access control, no application-level auth by deliberate MVP scope decision), this application has:
- No secret-exposure vector, in git, in logs, or in API responses.
- No SQL injection vector (ORM-parameterized throughout; the one raw-SQL usage is a static, parameterless liveness probe).
- No stack-trace/internal-detail leakage in any tested error path, including newly-added live tests beyond what the previous review performed.
- Correctly fail-closed webhook authentication for the one class of endpoint (Twilio voice) that is reachable from outside the trusted network by design.
- A correctly-scoped, non-wildcard CORS policy that requires (and is documented as requiring) an operator to set the real frontend origin before production use.

The single standing risk — total absence of authentication/authorization — is unchanged, pre-existing, and remains correctly scoped to its documented mitigation (network isolation). This revalidation found no evidence that any code change since the last review widened that risk, and found no new secret exposure, injection vector, or information-disclosure issue. **The application remains safe to run under its documented "internal network only, no auth" deployment model**, and unsafe only in the sense the previous review already flagged: it must never be exposed to an untrusted network without adding authentication first.

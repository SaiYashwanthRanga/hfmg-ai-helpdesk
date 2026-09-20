# HFMG AI Help Desk — Security Review

**Performed:** Final pre-production review pass
**Method:** Every finding below is verified against running code and, where applicable, a live request against the running backend — not asserted from documentation. Commands used are included so findings are reproducible.

---

## 1. Secret Handling

### 1.1 Secrets never committed to git
```bash
$ git ls-files | grep -E "\.env$"
(no output)
```
Both `backend/.gitignore` and the root `.gitignore` exclude `backend/.env`/`frontend/.env`. No `.env` file is tracked. **Pass.**

### 1.2 Secrets never logged
```bash
$ grep -rn "logger\." app --include="*.py" | grep -iE "description|transcript|turns|api_key|auth_token|password|secret"
app/llm/openai_provider.py:136: logger.warning("OPENAI_API_KEY is not set; cannot call the model")
app/llm/openai_provider.py:180: logger.warning("OPENAI_API_KEY is not set; cannot call the model")
app/voice/security.py:37:       logger.error("TWILIO_AUTH_TOKEN is not set; rejecting webhook")
app/notifications/sendgrid_provider.py:54: logger.warning("SENDGRID_API_KEY is not set; cannot send email")
```
All four matches log *that* a secret is missing, never the secret's value. A second pass specifically searching for the secret variables themselves inside log calls (`grep -rn "logger\." app | grep -iE "settings\.(openai_api_key|twilio_auth_token|sendgrid_api_key)"`) returned zero matches. **Pass.** This also matches `ARCHITECTURE.md` §8.1's stated policy of never logging PHI-adjacent or credential data — confirmed by code, not assumed from the doc.

### 1.3 Secrets never returned in API responses
```bash
$ curl -s http://localhost:8000/api/v1/settings/status | python3 -m json.tool
```
returned `masked_key` values that are either `null` or a masked string (e.g. `sk-...a1b2` in the design spec; in the current dev environment, all three provider keys are unconfigured so every `masked_key` is `null` — verified live). The masking function (`app/core/masking.py`) only ever sees the raw value inside its own scope and returns a truncated string; no code path in `app/api/v1/settings.py` or `app/services/dependency_health.py` returns the raw `settings.openai_api_key`/`twilio_auth_token`/`sendgrid_api_key` values. **Pass.**

### 1.4 Database credentials never exposed
`GET /settings/status`'s `database` section returns `{"configured": true, "detail": "PostgreSQL", "masked_key": null}` — no connection string, host, or credential fragment. Confirmed live. **Pass.**

---

## 2. Settings Page — Write-Path Absence (highest-scrutiny item)

This page is a live vulnerability the moment it gains a write path with no auth in front of it (`DESIGN.md` §12), so it received the most direct verification in this review, not just a design-intent check.

```bash
$ grep -rniE "<input|<form|onSubmit|method=[\"']post|fetch\(.*method.*(POST|PATCH|PUT)" \
    frontend/src/pages/SettingsPage.tsx frontend/src/components/settings/ frontend/src/api/settings.ts
frontend/src/components/settings/ReadOnlyConfigPanel.tsx:8: * contain an `<input>`, a `<form>`, a save/submit button, or any call to a
```
The only match is the guard component's own documentation comment, not code. **Zero editable elements exist anywhere in the Settings component tree.**

```bash
$ grep -n "@router\.\(post\|patch\|put\|delete\)" backend/app/api/v1/settings.py
(no output)
```
The backend `settings` router has exactly one route (`GET /status`) and no mutation methods at all. **Pass — this is not a "disabled button," it is the complete absence of a write path on both tiers.**

---

## 3. API Response Hygiene

- **Error responses** use FastAPI's standard `{"detail": "..."}` shape with human-authored messages (e.g. "Cannot transition ticket from NEW to CLOSED") — never a raw exception message or stack trace. `FastAPI(...)` is instantiated in `app/main.py` with no `debug=True`, so unhandled exceptions return a generic `500` with no traceback leaked to the client. **Pass.**
- **New Tier 0–4 endpoints all bound their inputs**: `page_size` (1–100), `days` (1–365), `limit` (1–50) are all `Query(..., ge=..., le=...)`-constrained, preventing a trivial resource-exhaustion query (e.g. `?days=999999999`). **Pass.**
- **Dependency health checks (`/health/dependencies`) never leak upstream provider errors** — every `check_*` function catches its own exceptions and returns a `DependencyCheck(status="down", ...)`; no OpenAI/Twilio/SendGrid error body or status code is ever forwarded to the client. **Pass.**

---

## 4. Authentication & Authorization

**No authentication exists anywhere in this system.** This is not a finding from this review — it is a documented, deliberate MVP scope decision (`IMPLEMENTATION_PLAN.md`'s MVP Scope Decision), unchanged by any work in this engagement. Every endpoint added in Tiers 0–4 is open, consistent with every endpoint that existed before. This review's job is to confirm that decision is still being honored consistently (it is — no new endpoint quietly assumes a user identity or role that doesn't exist) and that the deployment-level mitigation is still documented (`DEPLOYMENT_GUIDE.md` §1/§6: network ACL, not application auth, is the only current control). **No new risk introduced; existing risk unchanged and still correctly documented.**

---

## 5. CORS Configuration

```python
allow_origins=settings.cors_origin_list,   # default: ["http://localhost:5173"]
allow_credentials=True,
allow_methods=["*"],
allow_headers=["*"],
```
Origins are restricted to an explicit list (not `"*"`), which is the one CORS setting that actually matters for security here — `allow_methods`/`allow_headers` wildcards are safe *given* origin restriction. **Pass**, with a note: **production deployment must set `CORS_ORIGINS` to the real frontend origin(s)** — the default is a localhost dev value. This is an existing configuration requirement, not a new finding, but is called out here since it's the one CORS knob an operator must not forget to change (`DEPLOYMENT_GUIDE.md` should already cover this — confirmed it does, §6).

---

## 6. Twilio Webhook Authentication (unchanged, re-verified)

`app/voice/security.py`'s signature validation was not touched by this engagement's work. Re-verified: `TWILIO_VALIDATE_SIGNATURE=false` is documented as dev-only (`backend/README.md`), and the four voice webhook routes remain the only endpoints in the system with any inbound request authentication at all. **Pass, unchanged.**

---

## 7. Findings Summary

| Area | Result |
|---|---|
| Secrets in git | ✅ Pass |
| Secrets in logs | ✅ Pass |
| Secrets in API responses | ✅ Pass |
| Settings write-path | ✅ Pass — confirmed absent by grep, not just by design intent |
| Error response hygiene | ✅ Pass |
| Input bounds on new endpoints | ✅ Pass |
| Dependency-check error leakage | ✅ Pass |
| Auth | ⚠️ Known, documented, unchanged — no auth exists anywhere; network ACL is the only control |
| CORS | ✅ Pass — requires production operator to set `CORS_ORIGINS` correctly (existing requirement) |
| Twilio webhook auth | ✅ Pass, unchanged |

**No critical or high-severity security finding was introduced by this engagement's work.** The one standing risk (no authentication) is pre-existing, deliberate, and already documented with its required mitigation (network isolation) — this review did not discover it, it confirmed it remains correctly scoped and that nothing built in Tiers 0–4 or Frontend Phases 4–8 quietly widened it (e.g., by adding a Settings write path, which was the most plausible way this could have regressed, and was specifically checked).

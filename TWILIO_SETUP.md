# HFMG AI Help Desk — Twilio Setup

**Audience:** the engineer connecting a phone number to the deployed voice agent
**Prerequisite:** the app is deployed and reachable at a public HTTPS URL ([DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md))
**Design background:** [TWILIO_ARCHITECTURE.md](TWILIO_ARCHITECTURE.md), [CALL_FLOW.md](CALL_FLOW.md)

---

## 1. Before You Buy a Number

**Sign a BAA with Twilio first.** Callers describe IT problems that may reference patients, scheduling, or clinical systems; the transcripts are PHI-adjacent. Twilio offers a HIPAA-eligible configuration, but it must be requested and enabled — it is not the default on a new account. Contact Twilio sales/support to execute the BAA and have HIPAA eligibility applied to the account *before* the number receives real calls.

Use a **separate Twilio project (sub-account) per environment**. Production and staging must not share an auth token, because the auth token *is* the webhook authentication credential.

## 2. Buy and Configure the Phone Number

1. Twilio Console → **Phone Numbers → Manage → Buy a number**.
2. Filter by the HFMG area code, capability **Voice**. (SMS is not used by this system.)
3. Buy it, then open **Phone Numbers → Manage → Active numbers → <your number> → Configure**.

Set exactly this, replacing the hostname with yours:

| Field | Value | Method |
|---|---|---|
| **A call comes in** → Webhook | `https://helpdesk.hfmg.net/api/v1/webhooks/twilio/voice` | `HTTP POST` |
| **Primary handler fails** → Webhook | `https://helpdesk.hfmg.net/api/v1/webhooks/twilio/voice/fallback` | `HTTP POST` |
| **Call status changes** → Webhook | `https://helpdesk.hfmg.net/api/v1/webhooks/twilio/voice/status` | `HTTP POST` |

Save.

Notes:
- **`POST`, not `GET`.** The app reads form-encoded POST bodies; a GET webhook produces errors on every call.
- You do **not** configure the `/voice/gather` endpoint anywhere. The app puts that URL in the TwiML it returns, and Twilio calls it automatically for each conversational turn.
- Leave "Configure with" set to **Webhooks**, not a Studio Flow or TwiML Bin — conversation logic lives in the app.

## 3. Get the Auth Token

Console → **Account → API keys & tokens → Auth Token** (click to reveal).

Set it in the app's `.env` as `TWILIO_AUTH_TOKEN` and restart. This token is what proves an inbound webhook genuinely came from Twilio.

```bash
TWILIO_AUTH_TOKEN=<the auth token>
TWILIO_VALIDATE_SIGNATURE=true
TWILIO_PUBLIC_BASE_URL=https://helpdesk.hfmg.net
```

Treat the auth token like a password: it is the *only* authentication on a publicly-reachable endpoint that creates tickets. If it leaks, rotate it in the console and update `.env` immediately.

## 4. How Webhook Authentication Works

Twilio signs every request with an `X-Twilio-Signature` header: an HMAC of the full request URL plus the sorted POST parameters, keyed by your auth token. The app recomputes that signature and rejects mismatches with `403`.

This means **the URL must match exactly** — scheme, host, path, and any query string. A proxy that makes the app think it was called over `http://` or at an internal hostname will produce a signature mismatch on every single call.

## 5. `TWILIO_PUBLIC_BASE_URL` — Set It

This is the most common cause of "every call fails with 403."

Behind Nginx, the app sees the request as arriving at `http://127.0.0.1:8000/...`, but Twilio signed `https://helpdesk.hfmg.net/...`. Recomputing the signature against the wrong URL always fails.

Setting `TWILIO_PUBLIC_BASE_URL=https://helpdesk.hfmg.net` tells the app which URL to validate against, making signature checking correct regardless of proxy configuration. **Set it in every deployed environment.** Omit the trailing slash.

## 6. Local and Staging Testing with ngrok

Twilio must reach your server over public HTTPS, so a laptop needs a tunnel.

```bash
# Terminal 1 — the app
cd backend && source .venv/bin/activate
uvicorn app.main:app --port 8000

# Terminal 2 — the tunnel
ngrok http 8000        # note the https URL it prints
```

Then in `backend/.env`:

```bash
TWILIO_AUTH_TOKEN=<auth token of your TEST project>
TWILIO_VALIDATE_SIGNATURE=true
TWILIO_PUBLIC_BASE_URL=https://<your-subdomain>.ngrok-free.app
```

Point a **test** Twilio number's webhooks at `https://<your-subdomain>.ngrok-free.app/api/v1/webhooks/twilio/voice` (and the fallback/status URLs).

The free ngrok URL changes on every restart — update both `.env` and the Twilio console each time.

### Testing without Twilio at all

To exercise the flow with no Twilio account, set `TWILIO_VALIDATE_SIGNATURE=false` and POST directly:

```bash
curl -X POST http://localhost:8000/api/v1/webhooks/twilio/voice \
  -d "CallSid=CA-test-1" -d "From=%2B18455550142" -d "To=%2B18455559999"

curl -X POST http://localhost:8000/api/v1/webhooks/twilio/voice/gather \
  -d "CallSid=CA-test-1" -d "SpeechResult=my eClinicalWorks is not opening" -d "Confidence=0.92"
```

Each response is the TwiML the caller would hear. **`TWILIO_VALIDATE_SIGNATURE=false` disables the only authentication these endpoints have — never set it anywhere reachable from the internet.**

## 7. First Real Call — Acceptance Checklist

Place an actual call to the number and verify each step. Automated tests mock the speech layer, so this is the only way to catch pronunciation, pacing, and timing problems.

- [ ] The call connects within a couple of seconds (no dead air, no Twilio error tone)
- [ ] You hear: *"Thank you for calling Horizon Family Medical Group IT Help Desk. How can I assist you today?"*
- [ ] The voice sounds natural (neural voice, not the flat default)
- [ ] Describe a problem — e.g. *"I can't log into eClinicalWorks"* — and the agent reflects it back and asks your name
- [ ] **It does not ask for your phone number** (caller ID captured silently — this is intended)
- [ ] Give your name → it asks for your email
- [ ] Say *"skip"* → it proceeds without arguing
- [ ] It reads a ticket number back **spelled out** — "H F M G, 2 0 2 6, 0 0 0 0 0 1" — not "one" or "two thousand twenty-six"
- [ ] "eClinicalWorks" is pronounced acceptably (if mangled, see §9)
- [ ] Say "no" to *"anything else"* → it says goodbye and hangs up
- [ ] The ticket exists in the dashboard with `source = PHONE`, correct category and priority, and the transcript in the description
- [ ] The notification email arrived at `helpdesk@hfmg.net`
- [ ] The AI summary populates within ~30 seconds

Then verify escalation explicitly — it's the path that matters most when the agent fails:

- [ ] Call again and immediately say *"can I talk to a real person?"*
- [ ] It agrees without argument, promises a callback at your number, and reads back a ticket number
- [ ] That ticket is **HIGH or URGENT** priority and its description begins `CALLBACK REQUESTED`

## 8. Costs

Per ~3-minute call, roughly: inbound voice ~$0.0085/min, speech recognition ~$0.02, TTS a fraction of a cent, plus ~6–10 model calls. **Order of magnitude: a few cents per call**, dominated by the LLM turns.

Two things scale the bill: call volume, and how many turns each call takes. A rising average-turns metric (runbook §3) costs money as well as caller patience.

Set a **billing alert** in the Twilio console. An unexpected spike usually means either a misconfigured loop or abuse of the public number.

## 9. Tuning the Voice

Configured in `.env`, no code changes:

| Variable | Default | Effect |
|---|---|---|
| `VOICE_TTS_VOICE` | `Polly.Joanna-Neural` | Any Twilio-supported Polly/Google neural voice |
| `VOICE_LANGUAGE` | `en-US` | Speech recognition language |
| `VOICE_SPEECH_MODEL` | `experimental_conversations` | Tuned for open-ended speech; `phone_call` is the alternative |
| `VOICE_GATHER_TIMEOUT` | `6` | Seconds to wait for the caller to begin speaking |

If **"eClinicalWorks" is mispronounced**, the fix is in `backend/app/voice/scripts.py` (all spoken copy lives there) — spelling it phonetically, e.g. "e Clinical Works", usually resolves it. That's a copy change, not a logic change.

If **callers are cut off mid-sentence**, raise `VOICE_GATHER_TIMEOUT`. If there are **long awkward pauses**, lower it.

## 10. Troubleshooting

### Every call fails — caller hears "an application error has occurred"

Check the Twilio Console → **Monitor → Logs → Errors** for the error code:

| Code | Meaning | Usual cause here |
|---|---|---|
| **11200** | HTTP retrieval failure | App returned 5xx, or took >15s. Check `journalctl -u hfmg-api` |
| **11205** | HTTP connection failure | Twilio can't reach the URL — DNS, firewall, or Nginx down |
| **12100** | Document parse failure | Malformed TwiML returned (rare; indicates a bug) |
| **12300** | Invalid content type | App returned JSON where TwiML was expected — usually a FastAPI error response leaking through |

A `{"detail": ...}` JSON body reaching Twilio almost always means the app raised an HTTP error before reaching the TwiML layer — most often the `403` below.

### Every webhook returns 403

In order of likelihood:

1. `TWILIO_PUBLIC_BASE_URL` not set, or doesn't exactly match the URL in the Twilio console (§5).
2. `TWILIO_AUTH_TOKEN` empty, wrong, or from a different Twilio project than the number.
3. Auth token rotated in the console but not updated in `.env`.
4. Nginx not sending `X-Forwarded-Proto https`.

Confirm from the logs: `journalctl -u hfmg-api | grep hfmg.voice.security`. "Rejected Twilio webhook with invalid signature" means the token/URL is wrong; "TWILIO_AUTH_TOKEN is not set" means it's missing entirely.

### Caller hears the greeting, then silence or an error on their first reply

The greeting is static TwiML; the *reply* is the first turn requiring the LLM. So this pattern points squarely at the NLU layer:

- `OPENAI_API_KEY` missing, invalid, or out of credit
- OpenAI API unreachable from the server (egress firewall)
- NLU latency exceeding `VOICE_NLU_TIMEOUT_SECONDS`

Check `journalctl -u hfmg-api | grep hfmg.voice.nlu`. With no working key, every caller gets escalated to a callback after three failures — degraded but safe.

### Every caller gets escalated

Same root cause as above: the agent can't understand anyone, hits three failures, and hands off. Verify the OpenAI key before suspecting the conversation logic.

### The agent asks for a phone number even though caller ID is present

Expected when the caller's ID is withheld, blocked, or not a normal 10/11-digit number (`anonymous`, `restricted`, some international or SIP callers). The agent asks only in that case — see [CALL_FLOW.md](CALL_FLOW.md) §4.

### Tickets are created but land in the wrong category

Classification quality, not a configuration problem. The voice agent picks from six fixed values (`VOICE_AGENT_DESIGN.md` §4). Note that password/lockout issues route to **Password** even when they concern a specific system — that's deliberate routing-by-owner, not a bug. Persistent misrouting is a prompt-tuning item; collect examples first.

### Calls connect but no ticket appears

The ticket is created only after collection completes or escalation fires. If callers hang up mid-flow, the status callback salvages a ticket **only if** a problem description was already captured. Check `voice_call_sessions` for `state = 'ABANDONED'`:

```sql
SELECT twilio_call_sid, state, escalated, ticket_id, created_at
FROM voice_call_sessions ORDER BY created_at DESC LIMIT 20;
```

A high abandonment rate before `COLLECT_NAME` suggests callers are giving up during the greeting — worth listening to Twilio's call logs for timing problems.

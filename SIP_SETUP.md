# SIP Voice Setup

**Status:** Current. This replaces the earlier Twilio integration, which has been removed.

## 1. Call path

```
Caller ──► Nextiva (SIP trunk) ──► SIPSorcery gateway (.NET 8) ──► Backend (FastAPI) ──► Freshworks / ticket pipeline
                                   SIP + RTP, speech-to-text,        conversation state machine,
                                   text-to-speech, silence handling  ticket creation, notifications
```

The gateway owns everything audio: it registers with Nextiva, answers the call, transcribes the caller, and speaks the lines the backend returns. The backend owns the conversation only. It never sees audio and never talks to Nextiva. There are no public webhooks; the gateway calls the backend over the internal network.

## 2. Configuration

### Backend (`backend/.env`)

| Variable | Purpose |
|---|---|
| `VOICE_SIP_GATEWAY_TOKEN` | Shared secret. The gateway sends it as `Authorization: Bearer <token>`. **Empty disables the SIP endpoints** (they return `403`). |
| `OPENAI_API_KEY` | Needed for the NLU that interprets caller answers. Without it callers are escalated to a human callback. |

The other voice tuning values (`VOICE_NLU_TIMEOUT_SECONDS`, `VOICE_NLU_MAX_RETRIES`, `VOICE_MAX_MISUNDERSTANDINGS`, `VOICE_MAX_EMAIL_ATTEMPTS`) have working defaults. No Twilio settings exist any more.

### Gateway (`appsettings.json`)

| Setting | Purpose |
|---|---|
| Nextiva SIP credentials | SIP server, username, password, and auth username for registration. These live only in the gateway. |
| `Backend.GatewayToken` | Must equal the backend's `VOICE_SIP_GATEWAY_TOKEN`. |
| Backend URL | Base URL of the backend, e.g. `http://172.22.6.98:8001`. |

Keep the token and SIP password out of source control.

## 3. Gateway ↔ backend contract

All endpoints are under `/api/v1/voice/sip`, authenticated by the bearer token. Bodies and responses are JSON.

### `POST /start`
Turn 0. Creates the call session, idempotent on `call_id`.

```json
{ "call_id": "abc-123", "from_number": "+18455550142", "to_number": "+18455559999" }
```

### `POST /turn`
One caller utterance per request.

```json
{ "call_id": "abc-123", "utterance": "my eClinicalWorks won't open", "confidence": 0.92 }
```

Send an empty `utterance` when the caller said nothing. The backend counts it as a failed turn and re-prompts or escalates.

### Response for `/start` and `/turn`

```json
{ "lines": ["Thanks. May I have your name?"], "expect_reply": true, "ticket_id": null }
```

- Speak every entry in `lines`, in order.
- If `expect_reply` is `true`, listen for the caller and post the transcript to `/turn`. If `false`, hang up after speaking.
- `ticket_id` is set once a ticket exists.
- If the backend hits an internal error it still returns `200` with a single apology line and `expect_reply: false`, so the caller never hears silence.

### `POST /status`
Call ended, for any reason. Returns `204`.

```json
{ "call_id": "abc-123", "reason": "hangup" }
```

If the caller hung up after describing the problem but before intake finished, the backend salvages a ticket marked `INCOMPLETE VOICE INTAKE`.

### Responsibilities that moved to the gateway
- **Silence and no-input timeout:** decide when the caller has stopped talking, and post an empty utterance after a no-input timeout.
- **Barge-in, speech-to-text and text-to-speech:** owned by the gateway, not configured in the backend.
- **Retries:** the backend tolerates replays. Sessions are keyed on `call_id` and ticket creation checks `ticket_id` first, so a retried `/start` or `/turn` won't duplicate a ticket.

## 4. Ticket workflow

Unchanged. When intake completes, the orchestrator calls the same `ticket_service.create_ticket()` as the web form with `source: "PHONE"`. The AI summary and helpdesk notification then run as background tasks. Freshworks ticket creation is not affected by the SIP migration.

## 5. Testing

Without a phone, with `VOICE_SIP_GATEWAY_TOKEN=devtoken` set:

```bash
curl -X POST http://localhost:8000/api/v1/voice/sip/start \
  -H "Authorization: Bearer devtoken" -H "Content-Type: application/json" \
  -d '{"call_id":"test-1","from_number":"+18455550142"}'

curl -X POST http://localhost:8000/api/v1/voice/sip/turn \
  -H "Authorization: Bearer devtoken" -H "Content-Type: application/json" \
  -d '{"call_id":"test-1","utterance":"my eClinicalWorks is not opening","confidence":0.92}'
```

Automated coverage: `backend/tests/test_sip_routes.py` (auth and JSON contract) and `backend/tests/test_voice_agent.py` (conversation logic).

Live check before go-live: place a real call to the Nextiva number and confirm a ticket appears with source `PHONE` and the transcript attached.

## 6. Security

- Keep the backend port reachable only from the gateway host and the internal network. Do not expose `/api/v1/voice/sip/*` to the internet.
- Use a long random `VOICE_SIP_GATEWAY_TOKEN`. Comparison is constant-time.
- Rotate the token by changing it in both places and restarting both services.

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Gateway gets `403` | `VOICE_SIP_GATEWAY_TOKEN` is empty on the backend. |
| Gateway gets `401` | Token mismatch between backend and `Backend.GatewayToken`. |
| Caller hears only the apology line | Check backend logs for `sip call=...`; usually an unknown `call_id` (no `/start`) or a database error. |
| Every call escalates | `OPENAI_API_KEY` missing or unreachable. |
| No calls arrive | Check Nextiva registration in the gateway logs. |
| Dashboard shows the SIP indicator as `down` | `VOICE_SIP_GATEWAY_TOKEN` is unset. This check only confirms configuration, not gateway reachability. |

## 8. Database migration note

`voice_call_sessions.twilio_call_sid` was renamed to `call_id`. Run `alembic upgrade head` on every deployed database when deploying this change. Deploy the backend and frontend together, because the API field and the `sip` health/settings keys changed.

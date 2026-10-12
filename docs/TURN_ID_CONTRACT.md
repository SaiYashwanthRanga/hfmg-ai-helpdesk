# `turn_id` contract for the SIP gateway

The backend side of making `POST /api/v1/voice/sip/turn` safe to retry. Everything below the
"Verified" line in the next section is covered by backend tests (`tests/test_turn_contract.py`,
`tests/test_turn_idempotency.py`). Nothing has been run against the real gateway.

## Status

**Verified (backend tests):** request and response shapes, status codes, error strings, headers,
replay, conflict, in-progress, malformed ids, legacy requests, lock-wait bound.

**Not verified:** any interaction with the new gateway build. Its source and build were not
available when this was written; the gateway on the backend host is the older build and does not
send `turn_id`. The items under "To confirm on the gateway side" are assumptions to check before
idempotency is switched on.

## Request

```
POST /api/v1/voice/sip/turn
Authorization: Bearer <VOICE_SIP_GATEWAY_TOKEN>
Content-Type: application/json
```

| Field | Type | Notes |
|---|---|---|
| `call_id` | string, required | unchanged |
| `utterance` | string | unchanged; `""` for silence |
| `confidence` | number or null | unchanged; not part of the retry check |
| **`turn_id`** | string, optional | 1-64 characters from `A-Z a-z 0-9 . _ : -`; unique within a call |

**GUID formats:** `Guid.ToString()` / `"D"` (`6b1f0c52-3a4e-4d0a-9d2c-5f6a9c7e1b10`, any case) and
`"N"` (32 hex digits) are accepted. **`"B"` (`{...}`) and `"P"` (`(...)`) are rejected** with 422.
The backend compares the id exactly (case-sensitive) and echoes it back unchanged, so send the
same string on every retry.

## Response (HTTP 200)

```json
{"lines": ["..."], "expect_reply": true, "ticket_id": null, "stt_prompt": "...", "turn_id": "6b1f0c52-3a4e-4d0a-9d2c-5f6a9c7e1b10"}
```

`lines`, `expect_reply`, `ticket_id` and `stt_prompt` are unchanged. **`turn_id` is the request's
`turn_id`, echoed on every 200 response to a request that carried one**: a normal turn, a replay, a
turn escalated after an internal error, and the plain "couldn't save your request" line. A request
without `turn_id` gets exactly the old body, with no `turn_id` key.

## Behaviour and errors

One canonical status code and error string per case. Error bodies use FastAPI's `detail`, and
`detail.code` is the string to switch on.

| Situation | Status | `detail.code` | Other | Gateway should |
|---|---|---|---|---|
| First time this `turn_id` is seen on the call | 200 | | | use the reply |
| Repeat after the first finished | 200 | | saved body, `Idempotent-Replayed: true`, same `turn_id` | use the reply; it is the original |
| Repeat while the first is still running | **409** | **`turn_in_progress`** | `Retry-After: 1`; arrives within `VOICE_TURN_LOCK_WAIT_SECONDS` | wait, resend the **same** body and `turn_id` |
| Same `turn_id`, different `utterance` | **409** | **`turn_payload_conflict`** | no `Retry-After` | **do not retry**; a gateway bug |
| Malformed `turn_id` | 422 | | FastAPI validation: `detail` is a list | do not retry; fix the id |
| Turn could not be saved and escalation failed | 200 | | the "wasn't able to save your request" line, `expect_reply: false`, `turn_id` echoed | speak it and end the call. **Nothing was saved**: resending the same `turn_id` runs the turn again |
| Bad or missing bearer token | 401 / 403 | | unchanged | |

The two 409s differ only by `detail.code`; `turn_payload_conflict` never carries `Retry-After`.

```json
{"detail": {"code": "turn_in_progress", "message": "An earlier request for this call is still being processed. Retry with the same turn_id."}}
{"detail": {"code": "turn_payload_conflict", "message": "This turn_id was already used for a different utterance on this call."}}
```

A replay never runs the conversation logic, changes state, creates a ticket, or starts a
summary/email. A turn and its recorded reply are saved in one transaction, so if the backend's
connection fails around the commit, a retry with the same `turn_id` either finds the turn (it was
saved) or runs it (it was not).

## Timing

| Setting | Value | Where |
|---|---|---|
| Gateway HTTP timeout, one attempt | 8 s | gateway; mirrored as `VOICE_GATEWAY_ATTEMPT_TIMEOUT_SECONDS=8` |
| How long a duplicate waits for the running turn | **5 s** (was 10) | `VOICE_TURN_LOCK_WAIT_SECONDS` |
| Time left to deliver the 409 or the replay | 3 s | rule: wait <= attempt timeout - 2 s |

The backend **refuses to start** if `VOICE_TURN_LOCK_WAIT_SECONDS` is above the attempt timeout
minus 2 s (or not above 0), so a duplicate can never be held longer than the gateway would wait for
its answer. If the gateway's attempt timeout changes, change `VOICE_GATEWAY_ATTEMPT_TIMEOUT_SECONDS`
with it.

A slow turn, step by step (first attempt takes 9 s):

```
t=0   attempt 1 sent, turn starts
t=8   attempt 1 times out at the gateway; attempt 2 sent with the same turn_id
t=9   turn 1 commits; attempt 2 gets the call's lock, finds the saved turn, replies (replay)
```

If the first attempt is still running 5 s after a retry arrives (t=13 above), the retry gets
`409 turn_in_progress` and the gateway resends after `Retry-After`.

**Total retry deadline.** A turn can run longer than one gateway attempt: each language-model call
is allowed up to `VOICE_NLU_TIMEOUT_SECONDS` (4 s) and a turn can make more than one. The gateway's
total deadline must therefore cover the slowest turn plus a polling interval, not just one attempt.
No turn duration has been measured for this document; the gateway owner should check the deadline
against the slowest turns in the gateway's logs. If the first attempt *fails* before the retry gets
the lock, the retry runs the turn itself and has the rest of its 8 s attempt to finish; if it runs
out, the next retry finds it saved or runs it again.

## Requests without `turn_id` (current gateway)

Processed on every arrival, as before; the response has no `turn_id`. The backend never invents an
id, so a retried utterance without one is **not** deduplicated. Such requests still run one at a
time per call, which prevents duplicate tickets from overlapping requests but not from a retry that
arrives after the first finished. They also wait for a busy call as long as they always did; the
5 s bound applies only to requests with a `turn_id`.

## Deployment

Order matters; each step is safe with the previous gateway.

1. **Backend first.** Deploy, then `alembic upgrade head` (adds `voice_turns`; head is
   `e6f0b4c8d213`). Requests without `turn_id` behave as before.
2. **Check settings.** `VOICE_TURN_LOCK_WAIT_SECONDS` unset or between 0 and 6;
   `VOICE_GATEWAY_ATTEMPT_TIMEOUT_SECONDS=8`. A bad value stops the backend at startup, in the log.
3. **Verify the contract against staging with the gateway's idempotency still off**, using the
   sequence below.
4. **Switch the gateway's idempotency on**, then watch the backend log: `sip call=... turn_id=<id>`
   instead of `turn_id=None`, and `replayed` lines when retries happen.
5. **Rollback:** switch the gateway feature off. The backend keeps working (requests without
   `turn_id` are unchanged); `voice_turns` rows can stay.

Verification sequence (any HTTP client; use a fresh `call_id` after `/start`, with the call at a
state that accepts any utterance):

| # | Send | Expect |
|---|---|---|
| 1 | turn with a new GUID `A`, utterance `X` | 200, body has `"turn_id": "A"`, no `Idempotent-Replayed` |
| 2 | the same request again | 200, identical body, `Idempotent-Replayed: true` |
| 3 | `A` again with utterance `Y` | 409, `detail.code` = `turn_payload_conflict` |
| 4 | two identical requests at once with a new GUID `B` | both 200, same body, one with `Idempotent-Replayed: true` |
| 5 | turn with `turn_id` = `{` + GUID + `}` | 422 |
| 6 | turn with no `turn_id` | 200, body without `turn_id` |

## To confirm on the gateway side

These are what the backend does; whether the gateway expects exactly this is **not verified**.

1. The error string is read from `detail.code` of the JSON body (FastAPI's shape), not a top-level
   field or a header.
2. In-progress is `409` + `turn_in_progress` (+ `Retry-After: 1`). It was not in the list of known
   mismatches, so it is assumed to match.
3. The echoed field is named `turn_id` and compared with the id sent, case-sensitively.
4. The gateway sends GUIDs as `"D"` or `"N"`, never `"B"` or `"P"`.
5. The total retry deadline covers the slowest turn (see Timing).
6. A 200 "couldn't save" reply (nothing saved) is spoken and the call ended, not retried.

## Guarantees and limits

- **Database-enforced:** a `turn_id` runs at most once per call (unique on `(call, turn_id)` in
  PostgreSQL). A repeat cannot change conversation state or create a second ticket.
- **Not exactly-once email.** The summary and helpdesk email start as a background task after the
  turn commits. A replay never starts one. But there is no durable queue: if the process stops
  between the commit and that task, the ticket exists with no summary or email. One narrow path can
  repeat an email: a commit that failed *after* succeeding while an identical request also finished.
- **Not ordering.** A turn that arrives after a newer one is processed as the newest.
- **Client disconnects.** What the backend does with a request whose client timed out was not
  tested end to end. Either outcome is safe: a committed turn replays; an uncommitted one is rolled
  back.
- **Retention.** Records are deleted with their call; there is no separate pruning.

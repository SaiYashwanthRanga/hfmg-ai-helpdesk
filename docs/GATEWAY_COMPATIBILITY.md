# Gateway compatibility: audit findings and contract notes

Audited against backend `177c719` (branch `feature/sip-only`). The gateway developer's review
inspected `de7ceb0` (2026-09-30), which is an ancestor of this branch but **20+ commits older**: the
`turn_id` work, patient intake, the helpdesk email changes and the employee directory all came after it.
Re-inspect `177c719` or later. Nothing here was tested against the real gateway or Nextiva; every
statement below is about the backend, from its code and its tests (`tests/test_gateway_compat.py`).

## What the backend receives

Only these fields reach it (`app/voice/sip_routes.py`):

| Route | Fields |
|---|---|
| `POST /voice/sip/start` | `call_id`, `from_number`, `to_number` |
| `POST /voice/sip/turn` | `call_id`, `utterance`, `confidence`, `turn_id` (optional) |
| `POST /voice/sip/status` | `call_id`, `reason` |

No other SIP metadata (P-Asserted-Identity, display name, an extension field) is passed or read.

## 1. `/status` and ticket creation: confirmed defect, fixed

When the gateway reports a hangup, a call that never finished intake becomes a ticket only if the caller
had described a problem **and** the helpdesk could call them back. The reachability test used
`session.from_number`, which is stored as the string `"unknown"` whenever no number is sent, so it was
always true: calls with a blank, `anonymous`, `unknown`, `restricted`, `private` or `+266696687` caller ID
produced tickets with phone `unknown`.

Now: reachable means a number the caller gave during the call, **or** a caller ID that is a real number
(`caller_id_is_usable`). This is the same rule the caller-says-goodbye path already used. Unchanged: a
call that did describe a problem and has a usable number is still salvaged into exactly one ticket (a
repeated hangup report creates no second one), and a call with no description never makes one.

Business decision to confirm: an unreachable dropped call now leaves no ticket (its transcript stays on the
Calls page). The alternative is a ticket flagged "no callback number"; say if you prefer that.

`caller_id_is_usable` is also stricter: a run of one repeated digit (`0000000000`, `1111111111`,
`+10000000000`) is not a number. Placeholder words were already rejected (they have no digits).

## 2. Caller ID formats

- A SIP/`tel:` URI (`sip:+18455550142@host`), or anything longer than the 32-character column, is now
  reduced to the telephone number it carries (or cut to fit). Before, such a value made `/start` fail
  with a database error. A bare number is stored exactly as sent.
- `to_number` is cut to 32 characters for the same reason.
- **`call_id` is limited to 64 characters.** At 65 or more, `/start` fails with a database error (HTTP 500).
  It is not truncated, because two calls could then collide. The gateway should send a GUID or another
  identifier of 64 characters or fewer.

## 3. Internal extensions: not supported by the current contract

A caller ID shorter than 10 digits is "not a usable number": no directory lookup and the normal questions
apply. That is deliberate: the backend cannot tell an extension from any other short value and does not
guess. The directory service itself already finds extensions (`find_by_extension`; 751 of the 761 loaded
records have one), so the missing piece is only a signal from the gateway.

Minimum backward-compatible change, **not implemented** until the gateway confirms what it can read from
SIP and when it is certain the caller is an on-net extension:

- `POST /voice/sip/start` gains an optional `from_extension` (string, digits). Absent = today's behaviour.
- When present, the backend looks the extension up once, at start, with the same proposal rules as a
  caller-ID match (a live entry with a name that can be said aloud is asked about; ambiguous, unknown or
  shared lines are not).
- An extension is not a telephone number: the callback number is still asked.
- Extensions are reused across sites (8 values in the current file belong to two records each), so
  duplicates are expected and resolve as "ambiguous, ask who is calling".

## 4. `turn_id`: implemented in this branch, not yet proven with the gateway

Present at `177c719`: the optional `turn_id` field, saved replay (`Idempotent-Replayed: true`), the echo on
every 200, `409 turn_in_progress` and `409 turn_payload_conflict`, a lock wait that fits the gateway's 8 s
attempt timeout. It is covered by `tests/test_turn_idempotency.py` and `tests/test_turn_contract.py`, and
documented in `docs/TURN_ID_CONTRACT.md`. The six "to confirm on the gateway side" items in that document
are still open. **Keep the gateway's strict idempotency mode off** until the staging sequence in that
document passes against a real gateway.

## 5. Employee directory (Phase 2): what was checked

- `/start` looks nobody up. The caller ID is looked up once, when the call is routed to the employee flow
  (during a `/turn`); patient and provider calls are never looked up (CALL_FLOW.md 2.2). A second `/start` that
  arrives while the first is still starting used to run the start twice; `/start` takes the call's row lock like
  `/turn` and `/status`.
- A directory name is only proposed, never assumed; only the caller's yes confirms it, and nothing
  authenticates anyone.
- Ambiguous, unknown, unavailable, shared-line and deactivated results never produce a proposed name.
- Patient intake, concurrent calls, ticket creation and the notification email are unaffected
  (`tests/test_directory_identity.py`).
- Dashboard: call list and detail return the new `CONFIRM_IDENTITY` state; the frontend type-checks.
- Data note: the call-detail API returns the whole stored call state, which now includes the matched
  directory entry (name label, email, extension, teams, location) while a proposal is pending. With the merged
  directory file it also includes that entry's phone number, clean name, department, site, job title,
  credentials, provider flag, account type and status. Only employee calls carry it. The API has no
  authentication in this version; that is the larger issue and is unchanged.

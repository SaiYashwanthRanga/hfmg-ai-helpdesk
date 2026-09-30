# HFMG AI Help Desk — Call Flow (Phase 2)

**Status:** Approved 2026-09-19 — implemented in Phase 2
**Companion docs:** [SIP_SETUP.md](SIP_SETUP.md) (infrastructure), [VOICE_AGENT_DESIGN.md](VOICE_AGENT_DESIGN.md) (prompts and NLU)

This document defines the conversation state machine: every state, every transition, what the caller hears, and what happens when things go wrong.

**Second entry point:** the AI Call Simulator ([VOICE_SIMULATOR.md](VOICE_SIMULATOR.md)) drives this same state machine (`orchestrator.start_call` / `handle_turn` / `escalate` / `salvage_abandoned_call`) from a browser instead of the SIP gateway. Everything here applies to simulated calls unchanged, except that their tickets get source `SIMULATOR`. Changing a transition changes both.

---

## 1. Flow Overview

```
                    ┌──────────────────────┐
   incoming call ──►│      GREETING         │  "Thank you for calling Horizon Family
                    │  (turn 0, no input)   │   Medical Group IT Help Desk. How can
                    └──────────┬───────────┘   I assist you today?"
                               │ caller describes issue
                               ▼
                    ┌──────────────────────┐
                    │ COLLECT_DESCRIPTION   │◄── re-prompt on no/unclear input
                    └──────────┬───────────┘
                               │ description captured + classified, plus anything
                               │ volunteered (name, department, when, can-you-work)
                               ▼
                    ┌──────────────────────┐   only if start time or work status
                    │   COLLECT_DETAILS     │   was NOT volunteered; asked once,
                    └──────────┬───────────┘   never retried (optional)
                               │
                               ▼
                    ┌──────────────────────┐   "name and department" in one question;
                    │    COLLECT_NAME       │◄── skipped if already said; department
                    └──────────┬───────────┘   asked once if only the name is known
                               │ unfamiliar name / low recognizer confidence only
                               ▼
                    ┌──────────────────────┐   read back SPELLED; "no" → spell it, then
                    │    CONFIRM_NAME       │   the corrected name is read back again
                    └──────────┬───────────┘   (max 2 rounds, then accepted + flagged)
                               │
                               ▼
                    ┌──────────────────────┐   caller ID present → SKIPPED entirely
                    │    COLLECT_PHONE      │   2 failed tries → carry on without one
                    └──────────┬───────────┘   (never an escalation)
                               │
                               ▼
                    ┌──────────────────────┐
                    │    COLLECT_EMAIL      │──► CONFIRM_EMAIL ──┐
                    │  (optional, 2 tries)  │◄───────────────────┘
                    └──────────┬───────────┘   skip after 2 failed tries
                               │
                               ▼
                    ┌──────────────────────┐   the whole ticket read back: who, what, since
                    │   CONFIRM_SUMMARY     │   when, priority AND WHY, spoken phone number.
                    └──────────┬───────────┘   "no" / "yes, but…" → change it → read again
                               │ yes            (max 2 corrections, then filed + flagged)
                               ▼
                    ┌──────────────────────┐
                    │   CREATING_TICKET     │  TicketService.create_ticket(source=PHONE)
                    └──────────┬───────────┘  + async AI summary + email notification
                               │
                               ▼
                    ┌──────────────────────┐
                    │      READ_BACK        │  "Your ticket number is H-F-M-G,
                    └──────────┬───────────┘   2-0-2-6, 0-0-0-4-8-2."
                               │
                               ▼
                    ┌──────────────────────┐
                    │    ANYTHING_ELSE      │──► yes: back to COLLECT_DESCRIPTION
                    └──────────┬───────────┘       (new ticket, same call)
                               │ no
                               ▼
                         ┌───────────┐
                         │ COMPLETED │  goodbye + hangup
                         └───────────┘

   ANY state ──► ESCALATED   when: caller asks for a person
                             or:   misunderstanding_count reaches 3
                             or:   unrecoverable system error

   ANY state ──► COMPLETED   when: caller says goodbye mid-intake ("Thank you. Bye bye."):
                             a ticket is filed from what was collected, at the priority
                             assessed (not bumped), flagged if intake was incomplete

   ANY state ──► ABANDONED   when: caller hangs up (status callback)
```

## 2. States

**Correction (Tier 5 documentation alignment, verified against `backend/app/db/models.py`):** `CREATING_TICKET` and `READ_BACK` in the table below are narrative steps, not persisted `voice_call_state_enum` values — the implemented enum has 11 members, not 13 (see `docs/archive/DOCS_GAP_REPORT.md`). In the running orchestrator, ticket creation and the read-back prompt happen inline during the same transition that exits `CONFIRM_CATEGORY` (or wherever collection completes), landing directly on `ANYTHING_ELSE`/`ESCALATED`/`COMPLETED` — there's no intermediate row update recording "now creating the ticket" as its own state. The sequence of events described here is still accurate; the state *names* for those two rows are not things `GET /api/v1/voice-calls`'s `state` field will ever return.

| State | Caller is being asked | Exits to |
|---|---|---|
| `GREETING` | Open-ended: "How can I assist you today?" | `COLLECT_DESCRIPTION` |
| `COLLECT_DESCRIPTION` | Problem description (often already answered by the greeting) | `COLLECT_DETAILS` / `COLLECT_NAME` / later states, whichever is the first thing still missing |
| `COLLECT_DETAILS` | "When did this start, and is it stopping you from working?" — **only if not volunteered**; optional, silence or an unclear answer moves on | `COLLECT_NAME` / later states |
| `COLLECT_NAME` | Caller's name and department (department asked once; optional) | `CONFIRM_NAME` |
| `CONFIRM_NAME` | Name read back **spelled**; "no" → spell first name, then last name (decoded letter by letter). Optional, never counts as a misunderstanding. `VOICE_CONFIRM_NAME=false` skips it | `COLLECT_PHONE` / `COLLECT_EMAIL` |
| `COLLECT_PHONE` | Callback number — **only entered when caller ID is unavailable**. A retry says what was wrong ("I only caught 5 digits"); after two tries the call carries on without a number (noted on the ticket), it never escalates over this | `COLLECT_EMAIL` |
| `COLLECT_EMAIL` | The part of the HFMG email **before the @, spelled** (a full address said aloud still works); `@hfmg.net` is added. Optional | `CONFIRM_EMAIL` / `CONFIRM_CATEGORY` |
| `CONFIRM_EMAIL` | "Did I get that right?" read-back | `CONFIRM_SUMMARY` / back to `COLLECT_EMAIL` |
| `CONFIRM_SUMMARY` | The whole ticket read back — name, department, the problem, since when, **priority and the reason for it**, and the callback number if it was spoken (not if caller ID gave it). "No" → "What should I change?"; "yes, but…" is a correction, not a yes. Two corrections at most, then it is filed and noted. `VOICE_CONFIRM_SUMMARY=false` skips it | `CREATING_TICKET` |
| `CONFIRM_CATEGORY` | Legacy: only used when `VOICE_CONFIRM_SUMMARY=false` and classifier confidence is low. With the read-back on, an uncertain category is instead mentioned in it ("I'm filing it under Network") and can be corrected there | `CREATING_TICKET` |
| `CREATING_TICKET` | (no input — ticket is created) | `READ_BACK` |
| `READ_BACK` | (no input — number is read) | `ANYTHING_ELSE` |
| `ANYTHING_ELSE` | "Anything else I can help with?" | `COLLECT_DESCRIPTION` / `COMPLETED` |
| `ESCALATED` | (no input — callback promised) | terminal |
| `COMPLETED` | (no input — goodbye) | terminal |
| `ABANDONED` | — (caller hung up) | terminal |

Terminal states end with `<Hangup/>`. The session row persists for audit and metrics.

## 3. The Greeting Is a Real Question

The required greeting — *"Thank you for calling Horizon Family Medical Group IT Help Desk. How can I assist you today?"* — is open-ended, so most callers answer it with their actual problem. The flow takes advantage of that: `GREETING`'s first caller utterance feeds straight into `COLLECT_DESCRIPTION`, so a typical caller never hears a redundant "please describe your problem."

If the caller answers with something that isn't a problem description ("hi, is this IT?"), `COLLECT_DESCRIPTION` asks the explicit follow-up instead. This costs one turn only for callers who need it.

## 4. Slot Collection Order — Rationale

Description first, contact details after. Callers phone a help desk to report a problem; asking "what's your name" before "what's wrong" reads as bureaucratic and increases early hang-ups. It also means that if the caller abandons mid-call, we already hold the most valuable field (see §8, abandoned-call salvage).

**Phone is captured silently from caller ID and never asked about**, per the requirement. The gateway supplies `from_number` on `/start`; when it's a usable number, the agent stores it and says nothing. The `COLLECT_PHONE` state is entered *only* when caller ID is absent, blocked, or anonymous.

This is a deliberate revision: an earlier draft had the agent confirm the number out loud ("I have your number as 845-555-0142 — is that right?"). That cost a full turn on every single call to verify data the SIP trunk already gave us reliably, and every turn is a chance for STT to fail. Silent capture means the typical call is **three questions: problem, name, email.**

**Revision (2026-09-28, voice latency/accuracy work — `docs/reviews/VOICE_LATENCY_ACCURACY_REPORT.md`):** the agent now also records department, when the problem started, and whether the caller can work, because priority depends on them and help desk staff asked for them. To keep the call short:
- the first description is mined for anything volunteered (name, department, timing, work status), and nothing already said is asked again;
- timing and work status are asked together in one optional question, name and department in another;
- "can't work at all" is a deterministic floor of High priority.

A caller who volunteers everything still hears three questions; a terse caller hears four. Each question is chosen by `_next_step()` in `orchestrator.py` as "the first thing still missing".

**Revision (2026-09-29, conversation review — `docs/reviews/VOICE_CONVERSATION_REVIEW.md`):**
- **The priority and the reason for it come from one rule** (`app/voice/priority.py`), so the agent can no longer say "minor issue" next to "high priority". Only facts the caller stated decide it: can they work, who is affected, is patient care affected.
- **Confidence is per entity.** A name is read back only if it is unfamiliar or the recognizer was unsure; a department is matched against HFMG's list and flagged when it isn't on it.
- **Nothing is filed without a read-back**, and every correction is repeated back rather than silently applied.
- **Wording varies and is shorter**, and the acknowledgement uses the caller's own thing ("Sorry to hear about your laptop's Bluetooth") or nothing at all.

The trade-off is that a caller phoning from a shared clinic extension gets that extension recorded as their callback number. Mitigations: the caller's name and email are still collected, and the transcript is on the ticket, so an agent has what they need. If shared-line callbacks become a real problem in practice, the cheap fix is to confirm the number only for extensions on a known-shared list rather than for everyone.

## 5. Retry and Failure Rules

A **failure** is any of:
- The gateway posts an empty utterance (caller silent / timeout)
- STT confidence below threshold *and* the NLU can't extract the required slot
- The NLU returns "could not determine" for a required slot
- The model call itself errors or times out

On failure in a state collecting a **required** slot (description, name, phone):
1. Increment `misunderstanding_count`.
2. Re-prompt with *different, simpler* wording (see `VOICE_AGENT_DESIGN.md` §7 — never repeat the identical sentence; it's the clearest signal to a caller that they're talking to a broken machine).
3. On the 3rd cumulative failure → `ESCALATED` with reason `REPEATED_MISUNDERSTANDING`.

The counter is **cumulative across the whole call**, not per-state. Three strikes total, per the requirement. A caller who struggles once at each of three different questions is struggling — they get a human.

Email is exempt (§7).

## 6. Escalation

Two triggers, one destination.

**Trigger A — caller requests a person.** Checked on *every* turn before slot extraction, so it works at any point in the call. Detection is intent-based, not keyword-matching alone (`VOICE_AGENT_DESIGN.md` §6) — "can I talk to a real person", "get me someone who knows what they're doing", "operator", "I'd rather speak to somebody" all qualify.

**Trigger B — three failures**, per §5.

**What happens (both):**
1. Session marked `escalated=true` with the reason.
2. A ticket is created from whatever was collected, with gaps filled safely:
   - `caller_name` → collected value, else `"Unknown caller (voice escalation)"`
   - `phone_number` → caller ID, else the number given (this is why phone is nearly always available)
   - `description` → collected description, plus an explicit escalation note: *"CALLBACK REQUESTED — caller asked to speak with a person"* or *"CALLBACK REQUESTED — automated intake could not understand the caller after 3 attempts"*, followed by the raw transcript so the human agent has full context
   - `category` → classified value, else `Other`
   - `priority` → **escalated one level above the assessed priority, minimum `HIGH`** — a caller who couldn't be served by automation shouldn't wait in a normal queue
3. Email notification fires immediately (same pipeline, and the subject line makes the callback nature obvious).
4. Caller hears: the ticket number, plus confirmation that a team member will call them back at the number on file.
5. `<Hangup/>`.

The caller always leaves with a ticket number, even on escalation. Nothing is lost, and there's no dead-end "sorry, goodbye."

**Revision (2026-09-28, from real test calls):**
- **A callback needs a number.** If the caller asks for a person and there is no caller ID and no number yet, the agent first asks *"Of course. What's the best number for our IT team to call you back on?"*, once. It escalates after that answer whether or not a number was heard, and that question never counts as a misunderstanding.
- **The problem is kept.** "My password was reset and I can't fix it — have IT call me back" records the description and classification on the callback ticket before escalating, instead of an empty ticket.
- **No false apology.** A caller who asked for a person is never told "I'm having trouble understanding"; with no number they hear *"Of course. I'll ask a member of our IT team to follow up with you."*
- **Greetings aren't failures.** "Hi." or "Hello, is this the help desk?" gets *"Sure. Can you tell me briefly what's going wrong?"* without a model call or a misunderstanding count (once per call).
- **"Is this about Other?" is never asked:** the catch-all category is not confirmed.

## 7. Email Handling (Optional Field)

Email is genuinely hard over the phone — spoken addresses are the single most error-prone thing to transcribe. The design accounts for that rather than pretending otherwise:

1. `COLLECT_EMAIL` asks for it and mentions it's skippable.
2. Whatever is extracted is **read back for confirmation** in `CONFIRM_EMAIL` (spelled out at domain boundaries: "j-smith at h-f-m-g dot net — is that correct?").
3. On "no", one retry (back to `COLLECT_EMAIL`).
4. After 2 total attempts, the agent gracefully gives up: *"No problem — we'll follow up by phone instead."* `email` is stored `NULL` (the schema permits it) and the call continues.

**Email failures never increment `misunderstanding_count`.** Failing to capture an optional field is not a failure to serve the caller, and escalating a successful call because an email address was hard to hear would be a bad trade.

## 8. Abandoned Calls

The status callback fires when a call ends. If the session isn't already `COMPLETED` or `ESCALATED`:

- **Description + phone already collected** → create a ticket anyway, priority as assessed, description prefixed *"INCOMPLETE VOICE INTAKE — caller disconnected before intake finished."* The caller had a real problem and a reachable number; an agent can call back.
- **Otherwise** → mark `ABANDONED`, log it, create nothing. A caller who hung up during the greeting has not asked for anything.

This is a deliberate safety net, not a requirement — but losing a caller's issue because they lost signal mid-call is exactly the failure mode that erodes trust in an automated help desk.

## 9. Idempotency

The gateway may retry a request it considers failed, so every turn must be safe to replay:

- `voice_call_sessions.call_id` is unique — turn 0 replays load the existing session instead of creating a second one.
- `CREATING_TICKET` checks `session.ticket_id` first. If a ticket already exists, it is **not** created again; the read-back lines are simply returned again with the existing number.
- A replayed `/turn` for an already-advanced state returns that state's lines rather than advancing twice.

## 10. Example: Happy Path

> **Agent:** Thank you for calling Horizon Family Medical Group IT Help Desk. How can I assist you today?
> **Caller:** Yeah, hi — none of us can log into eClinicalWorks this morning. It just says the session expired and kicks us right back out.
> **Agent:** I understand you're having an issue with eClinicalWorks. May I have your name?
> **Caller:** Maria Lopez.
> **Agent:** Thank you, Maria. What email address should we use for updates? You can say "skip" if you'd rather not.
> **Caller:** m-lopez at h-f-m-g dot net.
> **Agent:** I have m-lopez at h-f-m-g dot net — is that correct?
> **Caller:** That's right.
> **Agent:** Thank you. Since this is affecting multiple people, I'm marking it high priority. Let me create that ticket for you — one moment.
> *(ticket created: category eClinicalWorks, priority HIGH, source PHONE; summary + email dispatched)*
> **Agent:** Your ticket number is H-F-M-G, 2-0-2-6, 0-0-0-4-8-2. I've sent it to our IT team and they'll follow up with you. Is there anything else I can help you with?
> **Caller:** No, that's it. Thanks.
> **Agent:** You're welcome. Thank you for calling Horizon Family Medical Group IT Help Desk. Goodbye.

## 11. Example: Caller Requests a Person

> **Agent:** Thank you for calling Horizon Family Medical Group IT Help Desk. How can I assist you today?
> **Caller:** Honestly, can I just talk to an actual person?
> **Agent:** Of course. I'll have a member of our IT team call you back at 845-555-0142. Let me get that request logged — one moment.
> *(escalation ticket created: category Other, priority HIGH, description flagged CALLBACK REQUESTED)*
> **Agent:** Your callback request is ticket number H-F-M-G, 2-0-2-6, 0-0-0-4-8-3. Someone will reach out to you shortly. Thank you for calling Horizon Family Medical Group IT Help Desk. Goodbye.

Note there's no argument and no retry loop — the agent hands off immediately and without friction. Making callers fight an automated system to reach a human is the fastest way to make them distrust the whole help desk.

## 12. Example: Three Failures

> **Agent:** Thank you for calling Horizon Family Medical Group IT Help Desk. How can I assist you today?
> **Caller:** *(heavy background noise, unintelligible)*
> **Agent:** I'm sorry, I didn't catch that. Could you tell me briefly what's going wrong? *(failure 1)*
> **Caller:** *(unintelligible)*
> **Agent:** I'm still having trouble hearing you. In a few words — what problem are you having? *(failure 2)*
> **Caller:** *(silence)*
> **Agent:** I'm sorry — I'm having trouble understanding, and I don't want to keep you. I'll have someone from our IT team call you back at 845-555-0142 directly. *(failure 3 → escalate)*
> *(escalation ticket created, priority HIGH, transcript attached)*
> **Agent:** Your callback request is ticket number H-F-M-G, 2-0-2-6, 0-0-0-4-8-4. Thank you for calling, and sorry for the trouble. Goodbye.

## 13. Gateway Turn Shape (illustrative)

Every conversational turn is one JSON exchange between the SIPSorcery gateway and the backend (see [SIP_SETUP.md](SIP_SETUP.md) §3):

```json
// gateway -> POST /api/v1/voice/sip/turn
{ "call_id": "abc-123", "utterance": "Maria Lopez", "confidence": 0.94 }

// backend -> gateway
{ "lines": ["Thanks Maria. What's the best email for you?"], "expect_reply": true, "ticket_id": null }
```

The gateway speaks every entry in `lines`, then listens if `expect_reply` is true. Silence handling and barge-in belong to the gateway; when the caller says nothing it posts an empty `utterance`, which the backend counts as a failure. Terminal states return `expect_reply: false` and the gateway hangs up.

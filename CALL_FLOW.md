# HFMG AI Help Desk — Call Flow (Phase 2)

**Status:** Approved 2026-09-19 — implemented in Phase 2
**Companion docs:** [TWILIO_ARCHITECTURE.md](TWILIO_ARCHITECTURE.md) (infrastructure), [VOICE_AGENT_DESIGN.md](VOICE_AGENT_DESIGN.md) (prompts and NLU)

This document defines the conversation state machine: every state, every transition, what the caller hears, and what happens when things go wrong.

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
                               │ description captured + classified
                               ▼
                    ┌──────────────────────┐
                    │    COLLECT_NAME       │◄── re-prompt
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐   caller ID present → SKIPPED entirely
                    │    COLLECT_PHONE      │   caller ID blocked → ask for it
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │    COLLECT_EMAIL      │──► CONFIRM_EMAIL ──┐
                    │  (optional, 2 tries)  │◄───────────────────┘
                    └──────────┬───────────┘   skip after 2 failed tries
                               │
                               ▼
                    ┌──────────────────────┐
                    │  CONFIRM_CATEGORY     │  only if classifier confidence is low
                    └──────────┬───────────┘
                               │
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

   ANY state ──► ABANDONED   when: caller hangs up (status callback)
```

## 2. States

**Correction (Tier 5 documentation alignment, verified against `backend/app/db/models.py`):** `CREATING_TICKET` and `READ_BACK` in the table below are narrative steps, not persisted `voice_call_state_enum` values — the implemented enum has 11 members, not 13 (see `TWILIO_ARCHITECTURE.md` §6, `docs/archive/DOCS_GAP_REPORT.md`). In the running orchestrator, ticket creation and the read-back prompt happen inline during the same transition that exits `CONFIRM_CATEGORY` (or wherever collection completes), landing directly on `ANYTHING_ELSE`/`ESCALATED`/`COMPLETED` — there's no intermediate row update recording "now creating the ticket" as its own state. The sequence of events described here is still accurate; the state *names* for those two rows are not things `GET /api/v1/voice-calls`'s `state` field will ever return.

| State | Caller is being asked | Exits to |
|---|---|---|
| `GREETING` | Open-ended: "How can I assist you today?" | `COLLECT_DESCRIPTION` |
| `COLLECT_DESCRIPTION` | Problem description (often already answered by the greeting) | `COLLECT_NAME` |
| `COLLECT_NAME` | Caller's name | `COLLECT_PHONE` / `COLLECT_EMAIL` |
| `COLLECT_PHONE` | Callback number — **only entered when caller ID is unavailable** | `COLLECT_EMAIL` |
| `COLLECT_EMAIL` | Email address (optional) | `CONFIRM_EMAIL` / `CONFIRM_CATEGORY` |
| `CONFIRM_EMAIL` | "Did I get that right?" read-back | `CONFIRM_CATEGORY` / back to `COLLECT_EMAIL` |
| `CONFIRM_CATEGORY` | Disambiguation — **only when classifier confidence is low** | `CREATING_TICKET` |
| `CREATING_TICKET` | (no input — ticket is created) | `READ_BACK` |
| `READ_BACK` | (no input — number is read) | `ANYTHING_ELSE` |
| `ANYTHING_ELSE` | "Anything else I can help with?" | `COLLECT_DESCRIPTION` / `COMPLETED` |
| `ESCALATED` | (no input — callback promised) | terminal |
| `COMPLETED` | (no input — goodbye) | terminal |
| `ABANDONED` | — (caller hung up) | terminal |

Terminal states end with `<Hangup/>`. The session row persists for audit and metrics.

## 3. The Greeting Is a Real Question

The required greeting — *"Thank you for calling Horizon Family Medical Group IT Help Desk. How can I assist you today?"* — is open-ended, so most callers answer it with their actual problem. The flow takes advantage of that: `GREETING`'s `<Gather>` result feeds straight into `COLLECT_DESCRIPTION`, so a typical caller never hears a redundant "please describe your problem."

If the caller answers with something that isn't a problem description ("hi, is this IT?"), `COLLECT_DESCRIPTION` asks the explicit follow-up instead. This costs one turn only for callers who need it.

## 4. Slot Collection Order — Rationale

Description first, contact details after. Callers phone a help desk to report a problem; asking "what's your name" before "what's wrong" reads as bureaucratic and increases early hang-ups. It also means that if the caller abandons mid-call, we already hold the most valuable field (see §8, abandoned-call salvage).

**Phone is captured silently from caller ID and never asked about**, per the requirement. Twilio supplies `From` on every webhook; when it's a usable number, the agent stores it and says nothing. The `COLLECT_PHONE` state is entered *only* when caller ID is absent, blocked, or anonymous.

This is a deliberate revision: an earlier draft had the agent confirm the number out loud ("I have your number as 845-555-0142 — is that right?"). That cost a full turn on every single call to verify data Twilio already gave us reliably, and every turn is a chance for STT to fail. Silent capture means the typical call is **three questions: problem, name, email.**

The trade-off is that a caller phoning from a shared clinic extension gets that extension recorded as their callback number. Mitigations: the caller's name and email are still collected, and the transcript is on the ticket, so an agent has what they need. If shared-line callbacks become a real problem in practice, the cheap fix is to confirm the number only for extensions on a known-shared list rather than for everyone.

## 5. Retry and Failure Rules

A **failure** is any of:
- `<Gather>` returns no speech (caller silent / timeout)
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

Twilio retries webhooks it considers failed, so every turn must be safe to replay:

- `voice_call_sessions.twilio_call_sid` is unique — turn 0 replays load the existing session instead of creating a second one.
- `CREATING_TICKET` checks `session.ticket_id` first. If a ticket already exists, it is **not** created again; the `READ_BACK` TwiML is simply re-rendered with the existing number.
- Each turn records the `SpeechResult` it consumed; an identical replayed payload for an already-advanced state re-renders that state's TwiML rather than advancing twice.

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

## 13. TwiML Shape (illustrative)

Every conversational turn is the same construct — a `<Gather>` wrapping the prompt, with a `<Redirect>` after it that catches silence:

```xml
<Response>
  <Gather input="speech"
          action="/api/v1/webhooks/twilio/voice/gather"
          method="POST"
          speechTimeout="auto"
          speechModel="experimental_conversations"
          language="en-US"
          bargeIn="true"
          timeout="6">
    <Say voice="Polly.Joanna-Neural">Can I get your name, please?</Say>
  </Gather>
  <!-- reached only if the caller said nothing: counts as a failure -->
  <Redirect method="POST">/api/v1/webhooks/twilio/voice/gather?timeout=1</Redirect>
</Response>
```

`bargeIn="true"` lets callers interrupt the prompt once they know what to say — a small detail that makes the agent feel far less robotic. Terminal states drop the `<Gather>` and end with `<Hangup/>`.

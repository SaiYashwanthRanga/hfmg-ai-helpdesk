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
                    ┌──────────────────────┐   "I have your callback number as X ... is this the
                    │ CONFIRM_CALLBACK_NUMBER│   best number?" always asked once, before the read-back.
                    └──────────┬───────────┘   "no" → COLLECT_ALTERNATE_CALLBACK_NUMBER → read the new
                               │                number back once; refusal / 2 failed tries → keep the
                               ▼                number we had (never an escalation)
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
| `IDENTIFY_CALLER` | The opening question when `VOICE_ASK_CALLER_TYPE=true`: "May I know if you are a patient or an employee?" A clear answer routes the call; an unclear one is asked about once, then the call carries on in the older way (describe the problem). Silence and "hello?" never count as failures. See 2.3 | `PATIENT_INTENT` (patient) / `COLLECT_DESCRIPTION` (employee) |
| `PATIENT_INTENT` | **Patients only.** "Are you calling about booking an appointment or an existing appointment?" Recognizes book, reschedule, cancel, check, trouble booking, or another request; asked about once if unclear, then kept in the caller's own words | `PATIENT_DETAILS` / `COLLECT_NAME` |
| `PATIENT_DETAILS` | **Patients only.** One optional question about the request (which appointment, a preferred time). "No" or "I don't know" moves on | `COLLECT_NAME` |
| `CONFIRM_IDENTITY` | **Employee IT calls only**, in place of the name question, when the caller ID matched exactly one live directory entry with a name that can be said aloud: "I found a directory entry for X. Am I speaking with X?" Yes → that name is **caller-confirmed** (not authenticated) and the call moves on; no → the proposal is discarded and `COLLECT_NAME` asks. Unclear or silent → asked once more, then treated as "no". An email, phone number or extension given instead is looked up once more (at most two proposals per call). Never counts toward escalation. See 2.2 | `COLLECT_NAME` / `COLLECT_PHONE` / later states |
| `COLLECT_NAME` | Caller's name and department (department asked once; optional) | `CONFIRM_NAME` |
| `CONFIRM_NAME` | Name read back **spelled**; "no" → spell first name, then last name (decoded letter by letter). Optional, never counts as a misunderstanding. `VOICE_CONFIRM_NAME=false` skips it | `COLLECT_PHONE` / `COLLECT_EMAIL` |
| `COLLECT_PHONE` | Callback number — **only entered when caller ID is unavailable**. A retry says what was wrong ("I only caught 5 digits"); after two tries the call carries on without a number (noted on the ticket), it never escalates over this | `COLLECT_EMAIL` |
| `CONFIRM_CALLBACK_NUMBER` | "I have your callback number as X. If our IT team needs to reach you regarding this ticket, they will call this number. Is this the best number to contact you on?" (a patient call hears "support team" and "your request" instead, see 2.3) — entered from the end of collection, whether the number came from caller ID or was spoken. A number offered with the "no" ("No, it's 214…") is read back directly: "I have your callback number as Y. Is that correct?". Unclear twice → the number we have stands. `VOICE_CONFIRM_CALLBACK=false` skips it | `CONFIRM_SUMMARY` / `COLLECT_ALTERNATE_CALLBACK_NUMBER` |
| `COLLECT_ALTERNATE_CALLBACK_NUMBER` | "What number would you like our IT team to call instead?" (a patient call hears "support team" instead, see 2.3) A retry says what was wrong ("I only caught 5 digits"); a refusal ("I'd rather not") or `VOICE_MAX_PHONE_ATTEMPTS` failed tries keeps the number we had, noted on the ticket | `CONFIRM_CALLBACK_NUMBER` / `CONFIRM_SUMMARY` |
| `COLLECT_EXTENSION` | Employee calls only: "Do you have an office extension number?" Asked once, and never when a directory entry the caller confirmed already has one (that one is reused). Read in code (no model call): 2-6 digits is the extension ("Got it, extension 1 0 0 4."); "no" / "I don't have one" is accepted ("No problem."); a bare "yes" gets "What's the extension number?" once; a ten-digit answer gets one "that sounds like a phone number" and is never kept as the extension or the callback number; silence or an unclear answer is asked about once, then the call moves on. Never counts as a misunderstanding. `VOICE_ASK_EXTENSION=false` skips it | `COLLECT_EMAIL` |
| `COLLECT_EMAIL` | The part of the HFMG email **before the @, spelled** (a full address said aloud still works); `@hfmg.net` is added. Optional | `CONFIRM_EMAIL` / `CONFIRM_CATEGORY` |
| `CONFIRM_EMAIL` | "Did I get that right?" read-back | `CONFIRM_SUMMARY` / back to `COLLECT_EMAIL` |
| `CONFIRM_SUMMARY` | The whole ticket read back — name, department, the problem, since when, **priority and the reason for it**, (the callback number is not repeated here once it has been confirmed in `CONFIRM_CALLBACK_NUMBER`; it is still read here when `VOICE_CONFIRM_CALLBACK=false` and the caller spoke it). "No" → "What should I change?"; "yes, but…" is a correction, not a yes. Two corrections at most, then it is filed and noted. `VOICE_CONFIRM_SUMMARY=false` skips it | `CREATING_TICKET` |
| `CONFIRM_CATEGORY` | Legacy: only used when `VOICE_CONFIRM_SUMMARY=false` and classifier confidence is low. With the read-back on, an uncertain category is instead mentioned in it ("I'm filing it under Network") and can be corrected there | `CREATING_TICKET` |
| `CREATING_TICKET` | (no input — ticket is created) | `READ_BACK` |
| `READ_BACK` | (no input — number is read) | `ANYTHING_ELSE` |
| `ANYTHING_ELSE` | "Anything else I can help with?" | `COLLECT_DESCRIPTION` / `COMPLETED` |
| `ESCALATED` | (no input — callback promised) | terminal |
| `COMPLETED` | (no input — goodbye) | terminal |
| `ABANDONED` | — (caller hung up) | terminal |

Terminal states end with `<Hangup/>`. The session row persists for audit and metrics.

### 2.3 Patient or employee first (`VOICE_ASK_CALLER_TYPE`)

The first thing the caller hears is *"Thank you for calling the helpdesk. May I know if you are a patient or an
employee?"* The answer is read by rules (`app/voice/caller_routing.py`): who the caller says they are first
("I'm a patient", "I work at the clinic"), then the bare words, then what the call is about ("an appointment",
"my printer"). Cues for both sides are never guessed at; the caller is asked. The language model
(`classify_caller_type`) is consulted only when the answer has no cue at all ("I'm Dr. Patel's nurse"), and only
trusted when it is sure. The type is stored on the call (`PATIENT_SUPPORT` = patient, `INTERNAL_IT` = employee),
never asked again, and cannot be shared between calls.

| Caller | What happens |
|---|---|
| **Employee** | Straight into the existing IT flow, unchanged: problem, when it started, name and department, directory confirmation, email, ticket, summary, notification. If the answer already holds the problem ("I'm an employee, my printer is jammed") it is used, not asked for again |
| **Patient** | `PATIENT_INTENT` → one optional `PATIENT_DETAILS` question → name → callback number (confirmed) → a **patient support request** in the patient categories. No IT questions, no IT language model, no IT intake details, no IT ticket |

**What the patient flow can and cannot do.** There is no appointment or scheduling system behind this line. The
agent collects the request and passes it to the support team for a call back, says so once ("I can't book,
change, or look up appointments myself on this call, but I can pass your request to our team"), and never says
an appointment was booked, changed, cancelled or found. Only the request itself and the details the caller
chooses to give are kept; no medical details are asked for.

A caller who says "actually, I'm an employee" (or a patient) at the first question of the other flow is moved
to the right one, and the other flow's answers are dropped. Only an explicit "I'm a ..." counts, and never after
a ticket exists; a sentence that merely mentions a patient or an appointment does not.

The call simulator is unaffected: it tests the employee IT intake and opens at the problem question.

Worked examples of each path (employee, patient, unclear and ambiguous answers, and a correction) are under
[Example Call Transcripts](#example-call-transcripts) below.

#### Example Call Transcripts

> **Agent lines reflect the scripts in `scripts.py`; caller lines are invented examples, not recordings of real
> calls. Some agent responses may rotate between equivalent scripted wordings.**

Reading the transcripts:

- **Agent** lines are the scripted wording, as the caller would hear it. **Caller (example)** lines are made up.
- Text in *(italic parentheses)* is a note, not speech. Ticket and request numbers are read out digit by digit
  (`H F M G, 2 0 2 6, 0 0 0 0 0 1`) and email addresses are read back in spoken form (`d s a m p l e at h f m g dot net`).
- The examples assume the settings in `backend/.env.example` (`VOICE_ASK_CALLER_TYPE`, `VOICE_CONFIRM_CALLBACK` and
  `VOICE_CONFIRM_SUMMARY` on). The caller types are `INTERNAL_IT` (employee) and `PATIENT_SUPPORT` (patient).
- Tickets and patient requests are local records. There is no Freshservice integration and no appointment or
  scheduling system behind this line.

**Rules the examples show**

- **The opening answer is read by rules, in order**: who the caller says they are ("I'm a patient", "I work here"),
  the bare words ("patient", "employee"), then what the call is about ("an appointment", "my printer"). A clear cue
  on one side routes the call, so a topic can route it at the opening question (example 4). Cues for both sides
  are never guessed at: the caller is asked (example 4).
- **The language model is the last resort** at the opening: it is consulted only when the answer has no cue at all,
  and its answer is used only when it is sure (`CLASSIFICATION_CONFIDENCE_THRESHOLD`).
- **An unclear answer is asked about once more; then the call falls back** to "tell me how I can help you", and the
  type is worked out from the problem described, as before this feature (examples 3 and 4). "Hello?" and silence
  are never counted as an unclear answer.
- **A correction needs an explicit "I'm a ..."** ("actually, I'm an employee"). A sentence that only *mentions* the
  other side, such as a patient saying "my printer is jammed", never changes who is calling. A correction is
  honoured only at the other flow's first question (the patient's request or its one details question, or the
  employee's problem question before a problem is recorded) and never once a ticket or request exists for the call
  (example 5).
- **The type is stored on the call and never asked again.** The patient flow never reaches the IT language model or
  creates an IT ticket; the employee flow is the existing IT flow, unchanged.

##### 1. Employee with an IT problem, caller ID matched in the directory

> **Agent:** Thank you for calling the helpdesk. May I know if you are a patient or an employee?
> **Caller (example):** I'm an employee.
> **Agent:** Thanks. Please briefly tell me what IT problem you're having.
> **Caller (example):** My Outlook won't open, it just spins.
> **Agent:** Sorry to hear about your Outlook. When did this start, and is it stopping you from working?
> **Caller (example):** It started this morning, but I can still work.
> **Agent:** I found a directory entry for Dana Sample. Am I speaking with Dana Sample?
> **Caller (example):** Yes.
> **Agent:** Thanks, Dana. And which department are you with?
> **Caller (example):** Billing.
> **Agent:** Thanks, Dana. What's your HFMG email? Just spell the part before the at sign, or say skip.
> **Caller (example):** d s a m p l e
> **Agent:** I have d s a m p l e at h f m g dot net. Is that correct?
> **Caller (example):** Yes.
> **Agent:** I have your callback number as 8 4 5, 5 5 5, 0 3 0 1. If our IT team needs to reach you regarding this ticket, they will call this number. Is this the best number to contact you on?
> **Caller (example):** Yes.
> **Agent:** Okay Dana, let me make sure I have this right. You're in Billing, and you're having trouble with your Outlook. It started this morning. Since you can still work, I'll log it as medium priority. Is that all correct?
> **Caller (example):** Yes.
> **Agent:** Great, I'm filing that now. Your ticket number is *(read out digit by digit)*. I've sent it to our IT team and they'll follow up with you. Is there anything else I can help you with?
> **Caller (example):** No, thanks.
> **Agent:** Thank you for calling Horizon Family Medical Group. Goodbye.

*Behind the scenes.* The call is stored as `INTERNAL_IT` and the existing IT flow runs unchanged. The directory entry
for the caller ID is only a proposal: the details question still comes first, and the caller's "yes" makes the name
*caller-confirmed*, never authenticated (see 2.2). The caller ID is also the callback number being confirmed, so no
phone question is asked. The ticket is a local record, and the helpdesk notification email lists the caller,
callback number, email, issue, summary and ticket number.

*Variations.*

- **The directory entry has a usable email** (and the confirmed name matches it): the email question is replaced by
  *"I have d a n a dot s a m p l e at e x a m p l e dot org on file for you. Is that still the best email for
  updates?"* (`EMAIL_DIRECTORY_CONFIRM`). Only a yes uses it; a caller who declines it is asked for their own.
- **No directory match** (not found, ambiguous, shared line, deactivated, or no usable caller ID): the identity
  question is replaced by the normal name question, *"May I have your name and department?"* or *"Who am I
  speaking with, and which department are you in?"*.
- **The first answer already holds the problem** ("I'm an employee, my printer is jammed"): the problem question is
  skipped and the agent goes straight on: *"Got it, your printer. When did this start, and can you still get your
  work done?"*
- **The caller says "skip" at the email question**: no email is kept, and the notification shows `Email: Not provided`.

##### 2. Patient asking about an appointment

> **Agent:** Thank you for calling the helpdesk. May I know if you are a patient or an employee?
> **Caller (example):** I'm a patient.
> **Agent:** How can I help you today? Are you calling about booking an appointment or an existing appointment?
> **Caller (example):** I need to reschedule my appointment.
> **Agent:** I can't book, change, or look up appointments myself on this call, but I can pass your request to our team so they can call you back. Which appointment is it? The date and the doctor would help.
> **Caller (example):** Thursday with Dr. Patel.
> **Agent:** May I have your name, please?
> **Caller (example):** Maria Lopez.
> **Agent:** I have your callback number as 8 4 5, 5 5 5, 0 1 4 2. If our support team needs to reach you regarding your request, they will call this number. Is this the best number to contact you on?
> **Caller (example):** Yes.
> **Agent:** Let me create a support request for you. Your request number is *(read out digit by digit)*. I've sent it to our support team and someone will follow up with you. Is there anything else I can help you with?
> **Caller (example):** No.
> **Agent:** Thank you for calling Horizon Family Medical Group. Goodbye.

*Behind the scenes.* The call is stored as `PATIENT_SUPPORT`. A **patient support request** is filed in the patient
categories: no IT questions are asked, the IT language model is never run, and no IT ticket or IT intake details
are created. Nothing is booked, changed, cancelled or looked up: the agent says so once and passes the request to the
support team for a call back. No medical details are asked for.

*Variations.*

- **The one optional question depends on the request**: booking, *"Do you have a preferred day or time, or a
  particular doctor?"*; changing or cancelling, *"Which appointment is it? The date and the doctor would help."*;
  checking, *"Which appointment are you asking about? The date and the doctor would help."*; trouble booking
  online, *"What happens when you try to book? For example, an error message, or the page not loading."* A request
  with no such question goes straight to the name.
- **The first answer already holds the request** ("I'm a patient and I need to cancel my appointment"): the
  "How can I help you today?" question is skipped.
- **The request is not clear**: *"Sorry, I want to be sure I help with the right thing. Is it about booking a new
  appointment, changing or cancelling one, or checking an existing appointment?"* is asked once; if it is still
  not clear, the request is kept in the caller's own words for the team.
- **The callback questions are worded for who is calling.** A patient hears "our support team" and "your request"
  (`PATIENT_CALLBACK_CONFIRM`); an employee hears "our IT team" and "this ticket" (`CALLBACK_CONFIRM`, example 1).
  If the caller says no to the number, the follow-up is *"What number would you like our support team to call
  instead?"* for a patient (`PATIENT_CALLBACK_ASK_OTHER`) and *"What number would you like our IT team to call
  instead?"* for an employee (`CALLBACK_ASK_OTHER`); it is asked again in the same wording if the caller turns down
  the number they offered. The read-back of the number, the yes / no handling, the retry prompts and the limit on
  tries are the same for both. The wording follows the caller type already stored on the call, which these steps
  only read: a call with no type, or one that is not exactly `PATIENT_SUPPORT`, gets the employee wording.
- **The patient asks for a person, or says goodbye early**: the same callback request or saved request as for an
  employee, worded for the support team and a request number (see the table in section 6).

##### 3. Unclear answer to the opening caller-type question

> **Agent:** Thank you for calling the helpdesk. May I know if you are a patient or an employee?
> **Caller (example):** Um, hello?
> **Agent:** Hello. Are you a patient, or an employee?
> **Caller (example):** Uh, I'm calling about something.
> **Agent:** Sorry, I didn't catch that. Are you a patient, or an employee?
> **Caller (example):** I work at the clinic.
> **Agent:** Thanks. Please briefly tell me what IT problem you're having.

*Behind the scenes.* "Hello?" is a greeting, so it is answered with a gentler repeat and not counted as an unclear
answer. "I'm calling about something" has no cue on either side, so it is the one case where the language model may
be asked, and a model that is not sure changes nothing. That counts as the first unclear answer, so the question is
asked once more. "I work at the clinic" is an explicit statement and routes the call to the employee flow.

*Variations.*

- **Still unclear after the second ask**: the agent carries on rather than leave the caller stuck.
  *(Caller: "Well, it's complicated, you know.")* **Agent:** *"No problem. Please tell me briefly how I can help you
  today."* The type is then worked out from the problem described (the older flow, see example 4).
- **"I want to talk to a person"** at any of these questions escalates, as everywhere else in the call.

##### 4. Ambiguous caller intent requiring clarification

> **Agent:** Thank you for calling the helpdesk. May I know if you are a patient or an employee?
> **Caller (example):** My appointment screen is frozen on my work computer.
> **Agent:** Sorry, I didn't catch that. Are you a patient, or an employee?
> **Caller (example):** I work here.
> **Agent:** Thanks. Please briefly tell me what IT problem you're having.

*Behind the scenes.* The sentence has a cue for each side ("appointment" and "work computer"), so the rules do not
pick one and the language model is not asked either: cues for both sides are a question for the caller, never a
guess. The answer "I work here" routes the call to the employee flow (`INTERNAL_IT`).

*Variations.*

- **A cue on one side routes the call without asking**, even when the caller might have meant the other: *"My
  portal isn't working."* is read as a patient request ("portal" is a patient cue), and the agent goes on with
  *"I can't book, change, or look up appointments myself on this call, but I can pass your request to our team so
  they can call you back. May I have your name, please?"* "It's about an appointment." routes the same way;
  "My printer is jammed." routes to the employee flow.
- **The older flow** (`VOICE_ASK_CALLER_TYPE=false`, or after the fallback in example 3) works the type out from the
  problem described, and only there does the agent ask the classification question, when the language model's
  confidence is below 0.80:

  > **Agent:** Thank you for calling Horizon Family Medical Group support. Please briefly tell me how I can help you today.
  > **Caller (example):** My portal isn't working.
  > **Agent:** Got it, your portal. Just so I can help you in the right way: are you calling about a patient service such as appointments or the patient portal, or is this about an employee IT issue?
  > **Caller (example):** The patient portal, I'm a patient.
  > **Agent:** Let me get that noted for you. May I have your name, please?

##### 5. Caller correcting their identity mid-call

> **Agent:** How can I help you today? Are you calling about booking an appointment or an existing appointment?
> **Caller (example):** Actually, I'm an employee. My laptop won't turn on.
> **Agent:** Sorry to hear about your laptop. When did this start, and is it stopping you from working?

*(Earlier in the call the caller had said "I'm a patient", so the call was `PATIENT_SUPPORT`.)*

*Behind the scenes.* "I'm an employee" is an explicit statement made at the patient flow's first question, so the
call moves to `INTERNAL_IT`, the patient answers are dropped, and the problem in the same sentence is used instead of
being asked for again. The call is not a patient request any more: it follows the employee IT flow from here.

*Variations.*

- **A correction with no problem in it**: *(Caller: "Actually, I'm an employee.")* **Agent:** *"Okay, thanks for
  letting me know. Thanks. Please briefly tell me what IT problem you're having."* The same works the other way
  round, from the employee's problem question to the patient flow.
- **A mention is not a correction**: a patient who answers the first question with *"My printer is jammed."* is not
  moved to the employee flow. The agent asks the patient question again: *"Sorry, I want to be sure I help with the
  right thing. Is it about booking a new appointment, changing or cancelling one, or checking an existing
  appointment?"* and the call stays `PATIENT_SUPPORT`.
- **Too late**: once a request has been filed for the call, "actually, I'm an employee" no longer changes the type of
  that call, and it also does not apply later in the flow, after the first question.

### 2.2 Employee directory (`USER_DIRECTORY_CSV_PATH`)

On the first `/start` of a call, if the gateway sent a usable caller ID, it is looked up once in the
employee directory (`app/services/user_directory.py`); only the outcome is kept, in that call's
session (`collected["directory"]`). The greeting, `/start` and `/turn` request and response shapes are
unchanged. A retried `/start` never repeats the lookup.

| Lookup outcome | What the caller experiences |
|---|---|
| One live entry with a name the agent can say | After the issue is described, **instead of** the name question: "Am I speaking with X?" (`CONFIRM_IDENTITY`) |
| One entry that is a shared line, a room, a role, or deactivated | The normal name question. The entry is kept for audit only and never proposed |
| Several entries share the number (`ambiguous`) | The normal name question. Which entries matched is not kept; none is ever picked |
| `not_found`, `unavailable`, or no usable caller ID | The normal name question; no lookup at all without a caller ID |

Rules that do not bend:

- A match is a **proposal**. Only the caller's spoken yes makes it the caller's name, and even then it is
  *caller-confirmed*, never authenticated: nothing is authorized by caller ID or by that yes, and the ticket
  says so (`Identity: directory entry confirmed by the caller; not authenticated`).
- The decision is deterministic (the directory service's result plus the rule-based yes/no): no model call.
- Patients and other non-employee callers never see it: the question exists only in the employee IT flow, and
  directory membership never classifies a call.
- The directory's Name column holds phone-system labels ("1001 Exec First Last", "Lab Slate Hill"), so the name said
  aloud is derived conservatively (`app/voice/directory_identity.py`): an extension prefix and the entry's own
  site/team words are stripped, and the result must be exactly two plain words. Anything else is asked, not guessed.
- Confirmed values (name, extension, teams, location, active) are kept in call context and written to the ticket's
  intake details. The directory's email is **not** put on the ticket (many entries are placeholders or use a
  misspelled domain); the usual email question still applies. The directory itself is never modified.

**Office extension (`VOICE_ASK_EXTENSION`, employee calls).** A directory entry the caller said yes to
carries its extension, and it is reused: the caller is not asked. Otherwise, after the name and callback number,
the agent asks once, "Do you have an office extension number?" (see `COLLECT_EXTENSION` in the states table).
What the caller says is checked against the directory only to describe it on the ticket, never to change who
they are, and another employee's name is never said or written:

| Ticket intake line | When |
|---|---|
| `Extension: 3001` | from the directory entry the caller confirmed |
| `Extension: 1004 (given by the caller; matches the directory entry for this caller's name)` | the directory has it under the name the caller gave |
| `Extension: 4004 (given by the caller; the directory lists this extension for a different entry)` | the directory has it under another entry |
| `Extension: 1004 (given by the caller; not found in the directory)` | not in the directory |
| `Extension: none (the caller has no office extension)` | the caller said they have none |
| `Extension: not captured (the caller's answer was unclear)` | no usable answer after one retry |

**A one-word department** ("Billing.", "the IT department", "HR") after the identity is confirmed is matched
against the department list in code; only an answer code cannot place goes to the language model.

### 2.1 Strict extraction: asking about impact (`VOICE_STRICT_EXTRACTION=true`)

By default the agent records `work_blocked`, `patient_care_affected` and `affected_scope` from what the
model infers. With strict extraction they are recorded **only when the caller said them**
([VOICE_AGENT_DESIGN.md](VOICE_AGENT_DESIGN.md) §5.1), and anything not said is *unknown* and asked about.
No new state: the questions reuse `COLLECT_DETAILS`, with a `pending_question` key in the session.

| Question (first wording) | Asked when | A yes means |
|---|---|---|
| "Is this stopping you from doing your work?" | work impact unknown, unless patients-blocked or a whole-site outage already fixes the priority | blocked |
| "Is this stopping you from checking in or seeing patients right now?" | patients were mentioned, or the caller is blocked on an eClinicalWorks issue, and patient care is unknown | patient care affected |
| "Is anyone else affected besides you?" | scope unknown **and** it can change the priority (the caller is blocked, or the system is shared: Network, Printer, eClinicalWorks) | several people |

Rules that keep it short and loop-free:
- If both *when it started* and *can you work* are missing, one question asks both and invites an explicit statement ("are you able to keep working, or is it stopping you?"). If only *can you work* is missing, the clean question above is asked directly.
- Each fact is asked at most **twice** (the second time with different wording); "I'm not sure" is a final answer.
- The whole call asks at most **`VOICE_MAX_CLARIFICATION_TURNS` (2)** clarification questions after the details question.
- A bare "yes"/"no" is read deterministically; a longer reply ("yes, I can still work") is interpreted by the model against the question's fixed meaning of yes. A direct answer is a trusted fact.
- Unclear answers and silence never count as misunderstandings and never escalate.
- A caller asking for a person during a question escalates as usual.

Measured on the voice scenarios this costs **0.73 extra turns per call** on average; callers who state their impact clearly are asked nothing extra.

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

The quoted lines above are the employee wording. A patient, or a caller whose type is not known yet, hears the
wording below.

**Wording by caller type.** The escalation works exactly as above for every caller (same callback request, same
note, same priority rule, same notification email, same hang-up); only the words change. A patient hears about
"our support team" and a "request", never the IT team or a ticket. A caller whose type is **not known** hears
neutral lines: "our team" and a "request", with no guess at who they are and no promise that a call has been
arranged. That is a caller who asks for a person at the opening "patient or employee?" question, a caller in the
older flow (`VOICE_ASK_CALLER_TYPE=false`) who asks before their problem is classified, or a call whose stored type
is not exactly `INTERNAL_IT` or `PATIENT_SUPPORT`.

| Situation | Employee (`INTERNAL_IT`) hears | Patient (`PATIENT_SUPPORT`) hears | Not known hears |
|---|---|---|---|
| Asked for a person, number on file | "Of course. I'll have a member of our IT team call you back at X." | "Of course. I'll have a member of our support team call you back at X." | "Of course. I'll pass your request to our team with your number, X, so they can call you back." |
| Asked for a person, no number yet (asked once) | "Of course. What's the best number for our IT team to call you back on?" | "Of course. What's the best number for our support team to call you back on?" | "Of course. What's the best number for our team to reach you on?" |
| Asked for a person, still no number | "Of course. I'll ask a member of our IT team to follow up with you." | "Of course. I'll ask a member of our support team to follow up with you." | "Of course. I'll pass your request to our team." |
| Could not be understood, number on file | "…I'll have someone from our IT team call you back at X directly." | "…I'll have someone from our support team call you back at X directly." | "…I'll pass your request to our team with your number, X, so they can call you back." |
| Could not be understood, no number | "…I'll log a request for our IT team to follow up." | "…I'll log a request for our support team to follow up." | "…I'll pass your request to our team." |
| Then | "Your callback request is ticket number N. Someone will reach out to you shortly." | "Your request number is N." | "Your request number is N." |
| Says goodbye mid-call after the details were saved | "No problem. I've saved what you told me. Your ticket number is N. Take care." | "No problem. I've saved what you told me. Your request number is N. Take care." | the employee line (unchanged) |

The patient and neutral read-backs give only the number: the line before already says what happens next, and when
there is no number to call nobody can promise to reach the caller. The caller type is only read here, never set or
guessed. The callback-number questions (`CONFIRM_CALLBACK_NUMBER`, `COLLECT_ALTERNATE_CALLBACK_NUMBER`) and the
goodbye line still give a call with no known type the employee wording.

**A stored caller type that is not a `CallerType`** (for example `"PATIENT"`, `"patient_support"`, or a value
written by older code) never stops a ticket being created: the ticket is saved with no caller type (NULL, "unknown"),
the call's own record is left as it was, the problem is logged, and it is never treated as a patient request.

**What "escalation" means in this system.** The callback request is a ticket saved in this application's own
database, numbered `HFMG-…`, and the helpdesk is told about it by the notification email. That is all the
hand-off there is: the call is not transferred to a person, no human has accepted it when the caller hears the
line, and there is no Freshservice (or other external ticketing) integration. "I'll have a member of our team call
you back" means the request and the number are passed on for a call back, not that a call has been arranged.

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
> **Agent:** Of course. I'll have a member of our IT team call you back at 845-555-0142.
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

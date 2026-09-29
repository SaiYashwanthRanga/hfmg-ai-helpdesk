# Voice conversation review: flow, state, extraction, priority, caller experience

Scope: the voice agent's conversation logic (`backend/app/voice/*`), reviewed against four real simulator calls
(`SIM-2026-79A66954`, `185A0C0D`, `48EC118F`, `68A67058`). The calls were replayed line for line against the old
and new agent, using the real model, to produce the transcripts below.

## 1. What was wrong, and why

### Conversation flow
| Symptom | Root cause |
|---|---|
| Ticket created with no read-back | `_finish_collection` went straight to `CREATING_TICKET`. Only email and (rarely) category were confirmed. |
| Felt scripted | Fixed sentences ("I understand you're having an issue with laptop Bluetooth"), the same one on every call, built by pasting the model's phrase into a template. |
| Callers escalated over a phone number | Every failed digit attempt counted towards the generic "trouble understanding" limit, and the third one escalated. A caller with a bad line got a callback request instead of a ticket. |
| "Thank you. Bye bye." was taken as an email | The email step had no notion of a caller leaving. The ticket was filed, the call ended `ABANDONED`, and the priority was bumped as if the call had been dropped. |

### State management
| Symptom | Root cause |
|---|---|
| A corrected name was never checked | `CONFIRM_NAME` accepted the corrected spelling and moved on. There was no second read-back, so a mis-heard correction went into the ticket. |
| "Can you add H in the last?" was ignored | The name-correction step only understood a full re-spelling, so a partial edit fell back to "spell it one letter at a time". |
| No state for "the caller is about to be filed" | Nothing separated "collected" from "confirmed". |

### Information extraction
| Symptom | Root cause |
|---|---|
| Every name was treated the same | There was no per-entity confidence. A spelled-out "Yashwant" and a mumbled "Chindun Natakarani" both went through as certain, or both got the same read-back. |
| Department accepted verbatim | "Development", "AI Developer" and "AI Director" went onto the ticket unchecked. A mis-heard department would route wrongly and nobody would know. |

### Priority
The priority and its explanation came from two places. The model rated the priority. A template then built the
reason from the model's free-text `impact`. So "one user, minor issue" (the model's impact wording) was spoken next
to "high priority" (raised because `work_blocked` was true). Both were individually defensible; together they
contradicted each other.

### Caller experience
- Long unbroken prompts with the same wording each time.
- No acknowledgement of the caller's own words.
- A digit count was never said, so a retry sounded identical to the first ask.
- The agent read the ticket number back but had never read back the ticket.

## 2. What changed

**Priority (`voice/priority.py`).** One function returns the priority, the reason and the impact together, so they
can't disagree. Only facts the caller stated decide it:

| Caller said | Priority | Spoken reason |
|---|---|---|
| Patient care, or the whole site, affected | Urgent | "patient care is affected" / "the whole site is affected" |
| They can't work | High | "you can't work" |
| Their team can't work | High | "your team can't work" |
| One person, can still work | at most Medium | "you can still work" |
| A team or the site is affected but the caller can work | the model's rating | none stated |
| Nothing stated | the model's rating | none stated |

The agent only states a reason for High and Critical ("Since you can't work, I'll mark this high priority"). A test
sweeps every combination of inputs and asserts that the words and the priority never disagree.

**Entity confidence (`voice/names.py`, `voice/departments.py`).**
- A name is "high" confidence when the caller spelled it, when it is a common first/last name, or when the model and
  the recognizer both report high confidence. Otherwise it is "low" and is read back spelled.
- A department is matched against `VOICE_DEPARTMENTS` (aliases, then fuzzy matching that must share a first letter,
  so "radiology" never becomes "cardiology"). Anything that doesn't match is read back and marked unverified on the
  ticket rather than being rejected.

**Name confirmation.** `CONFIRM_NAME` reads the name back spelled. On "no", the caller can correct it as a whole
("no, it's Y-A-S-H-W-A-N-T-H"), as an edit ("add an H at the end") or by spelling it. The corrected name is read
back again. After two rounds the name is accepted as spoken and flagged for the technician.

**Summary before creation.** `CONFIRM_SUMMARY` reads back who, what, since when, the priority and why, and the
callback number if it was spoken. "No" opens "What should I change?". "Yes, but…" counts as a correction, not a yes.
Two corrections at most, then the ticket is filed with a note. Set `VOICE_CONFIRM_SUMMARY=false` to skip it.

**Phone.** The retry says what went wrong ("I only caught 5 digits, and I need all ten"). After two tries the call
carries on without a number, which is noted on the ticket. It no longer escalates.

**Goodbye.** A caller who says goodbye mid-intake gets a ticket filed from what was collected, at the priority that
was assessed. If the name or the problem is missing, an incomplete ticket is filed and flagged.

**Wording.** Short, varied prompts (`scripts.pick` rotates between phrasings), one question at a time, and an
acknowledgement that uses the caller's own words or none.

## 3. Before and after

Full transcripts for all four calls: `backend/eval/results/real_calls_transcripts.md`. The caller lines are
identical in both columns; only the agent differs. Two of them are shown here.

### Call 1: contradictory priority, corrected name never re-checked (SIM-2026-79A66954)

**Before**

> **Caller:** It started today morning and I can't able to connect my AirPods to my laptop and I can't able to work.
> **Agent:** May I have your name and department?
> **Caller:** My name is Yashvant and I'm from Development department.
> **Agent:** I have your name as Y A S H V A N T. Is that right?
> **Caller:** No, it's not correct. My name is Y-A-S-H-W-A-N-T-H.
> **Agent:** What's the best phone number for us to reach you?
> …
> **Caller:** I don't want to add any email address right now.
> **Agent:** **Since this is affecting one user, minor issue, I'm marking it high priority.** Let me create that ticket for you. One moment. Your ticket number is…

**After**

> **Caller:** My name is Yashvant and I'm from Development department.
> **Agent:** Just to be sure I have your name right: Y A S H V A N T. Is that correct?
> **Caller:** No, it's not correct. My name is Y-A-S-H-W-A-N-T-H.
> **Agent:** Thanks. So that's Y A S H W A N T H. Is that right?
> **Caller:** Yes, that's right.
> …
> **Caller:** I don't want to add any email address right now.
> **Agent:** Okay Yashwanth, let me make sure I have this right. You're in Development, and you're having trouble with your laptop's Bluetooth. It started this morning. **Since you can't work, I'll mark this high priority.** I'll reach you at 2 1 4, 8 8 5, 9 0 8 9. Does that sound right?
> **Caller:** Yes, that's right.
> **Agent:** Great, I'm filing that now. Your ticket number is…

Ticket: High before and after, but now with a consistent reason ("High: you can't work"), the corrected name
confirmed, and the department marked *unverified: not on HFMG's department list*.

### Call 2: escalated over phone digits (SIM-2026-185A0C0D)

**Before**

> **Caller:** Number is like 11006.
> **Agent:** Could you say that number again, one digit at a time?
> **Caller:** 1-0-0-6
> **Agent:** Sorry, what's the best callback number, one digit at a time?
> **Caller:** 866-882-085-444
> **Agent:** I'm sorry, I'm having trouble understanding. I'll log a request for our IT team to follow up… *(ESCALATED, High)*

**After**

> **Caller:** Number is like 11006.
> **Agent:** I only caught 5 digits, and I need all ten. Could you say the whole number again, slowly?
> **Caller:** 1-0-0-6
> **Agent:** That's okay, we'll follow up another way. Thanks, Chandu. What's your HFMG email? …
> **Caller:** Skip.
> **Agent:** Okay Chandu, let me make sure I have this right. You're in Development, and you're having trouble with your laptop's hotspot. It started today. **Since you can still work, I'll log it as medium priority.** Does that sound right?

Ticket: `COMPLETED`, **Medium** ("Callback number: not captured"). Before, this call was `ESCALATED` and
rated **High** for a caller who said they could work.

### Call 3: a partial name correction (SIM-2026-48EC118F)

Before, "Can you add H in the last?" got "Sorry about that. Please spell your first name, one letter at a time."
After, the edit is applied and read back: "Thanks. So that's Y A S H W A N T H. Is that right?"

### Call 4: goodbye taken as an email (SIM-2026-68A67058)

Before, "Thank you. Bye bye." was filed as the email answer and the call ended `ABANDONED`. After, the call is
`COMPLETED` at **Low**, with "No problem. I've saved what you told me. Your ticket number is…" and the ticket notes
that the read-back wasn't done because the caller ended the call.

## 4. Regression results

| Check | Result |
|---|---|
| Backend test suite | 389 passed |
| New conversation tests (`tests/test_voice_conversation.py`) | 71 passed: priority property test, name confidence, department validation, summary flow and corrections, phone give-up, goodbye, wording |
| Frontend | type check, tests and build clean |
| Migrations | `b8d4f0a26c35` upgrades and downgrades; `alembic check` shows no drift |
| Audio regression (`eval.voice_eval e2e --realistic`, label `conversation-v2`) | see section 4.1 |

Legacy voice tests run with both read-backs off (`_voice_flow_defaults` in `conftest.py`) so they still test what
they were written to test. The read-back paths have their own tests.

### 4.1 Audio regression

16 synthetic calls (9 voices and accents, 5 of them with background noise) through the real speech-to-text,
model and text-to-speech, compared with the last run before this work (`final-spelling`).

| | Before (`final-spelling`) | After (`conversation-v2`) |
|---|---|---|
| Caller turns | 119 | 124 (the summary read-back adds about one turn per call) |
| Unexpected escalations | 1 (`fast_printer`, over the phone digits) | 0 |
| Field accuracy: name / department / email / issue | 1.00 / 1.00 / 1.00 / 1.00 | 0.93 / 1.00 / 1.00 / 1.00 |
| Field accuracy: phone | 0.93 | 0.93 |
| Field accuracy: category / priority / started / work blocked | 0.94 / 0.94 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 / 1.00 |
| Wait after the caller stops speaking (avg / p95) | 2,593 / 5,190 ms | 2,577 / 4,827 ms |
| Speech-to-text (avg / p95) | 888 / 1,989 ms | 876 / 1,753 ms |
| Model (avg / p95) | 581 / 2,131 ms | 602 / 2,456 ms |

Latency is unchanged: the extra read-back turns don't make any single turn slower. The wait is still above the
2.5 s target on average (most of it is text-to-speech, see `VOICE_LATENCY_ACCURACY_REPORT.md`).

The one miss that is new is the `fast_printer` caller, who says "Tom Reilly"; the transcript came back as
"Tom Riley". Both are common names, so the agent treated the name as high confidence and did not read it back.
That is the trade-off in `VOICE_CONFIRM_NAME`'s design: a common name that is a homophone of another common name
gets no second chance. The same caller's phone number was also cut to 9 digits (a very fast talker); the agent
said so, asked once more, then carried on without a number, and the ticket still went through. Before this work,
that call was one of the two failures in the run and was rated wrongly.

## 5. Remaining risks

1. **The department list is a placeholder.** Until `VOICE_DEPARTMENTS` is set to HFMG's real list, every department
   is marked "unverified" (as in the transcripts above). This is harmless but noisy for technicians.
2. **One extra turn per call.** The summary read-back adds roughly one round trip (about 2 to 3 seconds of caller
   time). Turn it off with `VOICE_CONFIRM_SUMMARY=false` if that is not worth it.
3. **Twilio's recognizer is untested for spelled letters.** The simulator uses OpenAI transcription, with a
   letter-literal model for spelling turns. On a real phone line, Twilio's own speech recognition is used, and its
   accuracy on spelled names and emails has not been measured. `CONFIRM_NAME` and the email read-back are what
   protect against that.
4. **Priority depends on what the caller volunteers.** A caller who never says whether they can work gets the model's
   rating. The agent asks once ("can you still get your work done?") but does not press.
5. **Name lists are English-centred.** A common-name list decides which names skip the read-back. Names not on it
   just get one extra read-back turn.
6. **Backend restart needed.** The new states are database enum values; run `alembic upgrade head` first.
7. **Nothing is committed** on `feature/voice-simulator`.

# Voice Agent — Latency and Understanding Report

**Date:** 2026-09-28 · **Branch:** `feature/voice-simulator` (uncommitted) · **Method:** measured end to end with real OpenAI calls. Nothing below is estimated unless it says so.

## Summary

| | Before | After | Target |
|---|---|---|---|
| Caller wait per turn (avg / p95) | **6.3 s / 12.7 s** | **2.6 s / 5.2 s** | 2.5 s |
| Agent (NLU) time per turn, avg | 1.63 s | **0.58 s** (no model call on 65% of turns) | 1.2 s |
| STT avg | 0.83 s | 0.89 s | 0.8 s |
| TTS: time until the caller hears audio, avg / p95 | 3.8 s / 10 s (≈20% of replies timed out) | **1.1 s / 3.1 s** (40% of replies cached, instant) | 0.5 s |
| Ticket fields correct | **80/143 (56%)** | **161/165 (98%)** | — |
| Calls with every field right | 1/14 | **14/16** | — |
| Turns the agent misunderstood | 26 / 100 | 3 / 119 | — |
| Names correct | 85% | **100%** | — |
| Emails correct | 15% | **100%** | — |
| Priority correct | 46% | **93%** | — |
| Department / start time / can-you-work captured | 0% (never asked) | **100%** | — |

The accuracy problems are solved on the evaluation corpus.

Latency is 2.6× better but still above the 2.5 s target on average. From this development machine, a bare round trip to OpenAI takes about 0.6 s and the smallest possible model reply about 2 s (§1.3). Every remaining second is provider and network time, not our code. For the production phone path, where Twilio does STT and Polly does TTS, the agent's share is now 0.58 s on average.

## How this was measured

- **`backend/eval/`** is an evaluation harness. It synthesizes callers with OpenAI TTS, with distinct voices, Indian, Spanish and American accents, fast, slow and hesitant delivery, and noisy variants at about 10 dB SNR. It plays that audio through the simulator's real pipeline (`/audio` STT → `/process` orchestrator + NLU → streamed TTS) and scores every extracted field against ground truth.
- **Corpus:** 11 callers covering simple, long and run-on, hesitant, fast, two accents, self-correction, vague, "wants a human", digits-and-skip, and a caller modelled on the real tester (Indian name, `ranga.saiyashwanth@hfmg.net`). Five are replayed with background noise, for 16 calls in total.
- **"Before"** is the code as it was when the tester reported the problems, run on the same corpus. **"After"** is the final code, with callers who do what the agent asks (spell when asked, say "no" when a read-back is wrong).
- **The tester's own calls:** five real browser calls from the dev database were traced turn by turn (§2.2).
- **Component benchmarks** isolate TTS models and formats, STT models and prompts, NLU models, and spelling.
- **Cost:** a few dollars of OpenAI usage in total.

`python -m eval.voice_eval --help` reproduces everything.

---

## 1. Latency root cause

### 1.1 Where the time went (before, 100 caller turns)

| Stage | min | avg | p95 | max |
|---|---|---|---|---|
| Audio capture (push-to-talk, browser, tester's call) | 2 ms | 2 ms | 3 ms | 3 ms |
| STT (gpt-4o-mini-transcribe) | 557 | 828 | 1,365 | 2,246 |
| Prompt construction | < 1 ms (868 chars, no history) | | | |
| NLU / LLM (gpt-5-nano, 1 call every turn) | 1,066 | 1,632 | 2,809 | 4,016 |
| DB + session lock | 2 | 3 | 5 | 5 |
| Ticket creation | 2 | 3 | 10 | 10 |
| **TTS (gpt-4o-mini-tts, whole reply before any audio)** | **1,190** | **3,848** | **10,016** | **10,017** |
| Playback start (browser decode, tester's call) | 77 | 101 | 128 | 128 |
| **Caller wait** | 3,133 | **6,347** | **12,704** | 13,867 |

- **Biggest bottleneck: TTS.** `gpt-4o-mini-tts` has an extreme tail. In the benchmark, with no timeout, one request waited **160 s for its first byte**, and averages ranged from 2 to 22 s by format. In the app, the 10 s timeout fired on about 20% of replies, including the greeting on 4 of the tester's 5 calls.
- **Second: the LLM.** Every turn made a model call, including "Yes, please" and a phone number, which need no model at all.
- **Blocking and sequential work:** the reply was fully synthesized inside `/process` before the browser could start playing it.
- **Not the problem:** conversation history (none is sent), prompt size (about 870 characters), duplicate calls (exactly one per turn), the database (3 ms), and ticket creation (3 ms).

### 1.2 Fixes

| Fix | Effect (measured) |
|---|---|
| TTS `gpt-4o-mini-tts` → **`tts-1`**, mp3 | First audio avg 1.34 s, p95 1.88 s, no stalls (bench) |
| **Streamed TTS**: `/process` returns as soon as the agent decides; the browser plays `GET /speech/{turn}` as bytes arrive | Playback starts on the first bytes, about 0.4 s earlier than full synthesis (bench). *The eval harness buffers streams, so its TTS numbers are pessimistic by that much* |
| **TTS cache** by exact text | 40% of replies (greeting, fixed questions) play in 6 ms |
| **Deterministic fast paths** for yes/no, phone numbers, emails, spelled letters and greetings | 65% of turns make **no model call**; agent time on those is about 9 ms |
| **Voice NLU model** `gpt-4.1-mini` (`VOICE_NLU_MODEL`) | Same latency as nano models (network-bound), but 9/9 descriptions correct vs 5–6/9 |
| **Hedged NLU requests**: after 2.5 s, send a duplicate and take the first answer | Removes the "4 s budget exceeded → re-ask" failure seen in testing |
| **Connection pre-warm** at call start | Removes about 0.7 s of TLS setup from the first turn |
| **Context prompts** for STT | Faster as well as more accurate (avg 795 → 733 ms, p95 1,317 → 1,055 ms) |

### 1.3 The remaining floor

Measured from this development machine: an OpenAI API round trip (`models.retrieve`) has a **610 ms median**, and the smallest possible completion ("reply with: ok") a **1,978 ms median**. Any turn that needs STT, a model call and TTS pays at least three of those round trips. Where it now stands:

- **Rule-answered turns** (yes/no, phone, email, spelling): about **2.1 s** caller wait with the natural voice, and about **0.9 s** with the browser voice.
- **Model turns** (the problem description, name, timing): about **3.5 s**.

Getting under 2.5 s on every turn needs one of the following:

1. **Host the API near OpenAI** (a US region). Round trips drop to tens of milliseconds. This is the biggest remaining lever.
2. **The browser voice** in the simulator (Agent voice → Instant): TTS drops to ~0.
3. **Streaming STT/LLM** (Realtime API). This is an architecture change and was not done.

**The production phone path is different from the simulator.** Twilio does STT during the call and Polly speaks the TwiML, so neither OpenAI speech call exists there. The phone path's turn time is Twilio's end-of-speech detection, plus the agent (**now 0.58 s average, 0 s on rule turns**), plus Polly's first audio. The NLU improvements carry over to phone calls in full.

---

## 2. Understanding root cause

### 2.1 Where information was lost (before)

Each failure was traced through spoken → STT transcript → NLU → extracted slot → reply:

| Lost information | Stage | Example |
|---|---|---|
| Yes/no read as "unclear" | **NLU** | "Yes, please." / "Yeah, that's right." / "Yes, correct." to "Is that email correct?" came back `unable_to_determine`. The email was thrown away and re-asked in **12 of 13** calls |
| Phone numbers | **NLU** | "Eight four five five five five zero three three three" (no pauses) was misassembled three times, and the caller **was escalated** |
| Emails (15% correct) | **STT** mostly, then NLU | "saiyashwanth" came back as "siyashwan", "sichuan", "sayashwath". Spelled letters were merged and autocorrected: "m, l, o, p, e, z" became "MLOpec", "o, h, a, d, d, a, d" became "OHADDDAD". "@" was dropped and "hfmg" misheard as "hfmt"/"hfmc" |
| Names | **STT** | "Yashwanth" became "Yashwandh", "Reilly" became "Riley", "Haddad" became "Hadad". A spelled name was stored literally as "X, Y, A, S, H, W, A and T, H" and read back as "Thank you, X," |
| Department, start time, can-you-work | **Never asked** | 0% captured |
| Priority (46%) | **NLU** | "Outlook won't open" rated High for a caller who then said they could keep working; "can't log in at all" rated Medium for a calm caller |
| Volunteered details | **Context** | "This is James Carter from the front desk, since 8:30…" was all discarded, and the agent asked for the name again |
| A greeting read as a problem | **NLU** | "Hi." → "I understand you're having an issue with **Greeting**" |
| A callback request lost its problem | **Orchestrator** | "My Outlook password has been reset… I need IT experts to call back me" gave a ticket with no description, "I'm having trouble understanding", and **no callback number** |
| "I don't want any email updates" | **NLU** | Treated as a failed email twice |
| "Is this about Other?" | **Flow** | A pointless confirmation of the catch-all category |

### 2.2 The tester's calls

Five real browser calls, all on the pre-fix code (the running backend had not been restarted):

- **Every one** hit a 10 s TTS timeout, and 4 of 5 on the greeting.
- The failures in the table above marked "tester's call" come from them.
- One call's STT output came back in Telugu script. The agent is English-only by requirement, so this is now re-transcribed forced to English, and dropped as unheard if it's still not English.

### 2.3 Fixes

- **Rule-based understanding** for yes/no (including "No, it's not correct"), phone digits ("double five", "oh"), emails (including "@" repair and the misheard `@hfmg.net` domain), declines ("I don't want any email updates"), and greetings.
- **Email:** callers are asked to **spell the part before the @**, and `@hfmg.net` is added. Measured: *spoken* emails with Indian names were correct in 2 to 5 of 8 attempts **on every STT model tested**; *spelled* email names were correct in **12 of 12**.
- **Names:** read back **spelled** ("I have your name as Y A S H W A N T H. Is that right?"). A misheard name sounds right when said back, but not when spelled. On "no", the caller spells first name, then last name.
- **Spelled letters are decoded deterministically, never by a model.** Letter names as transcribed ("why" = Y, "are" = R, "and" between letters = N), "Y as in yellow", "double D", NATO, and hyphenated forms.
- **Spelling turns use `whisper-1` with a letters-only prompt.** The default transcriber merges letters into words: 34 of 36 spelled names correct with `whisper-1`, against 29 of 36 before.
- **One description call captures everything volunteered** (name, department, start time, work status, scope). Nothing already said is asked again; name and department are one question, start time and can-you-work another.
- **Priority rules on top of the model:** can't work at all → at least High; one person who can still work → at most Medium; site-wide → the model decides.
- **Voice-first prompts:** fillers, self-corrections ("Teams — actually Outlook"), run-ons, spelled letters, and recognizer errors are described, and "short answers are complete answers".
- **Twilio `<Gather hints>` per question** (e.g. "at hfmg dot net" when asking for email), plus STT context prompts in the simulator.
- **Escalation keeps the problem, asks for a callback number when there's none, and never tells a clear caller "I'm having trouble understanding".**

### 2.4 After: field accuracy (16 calls)

| Field | Before | After |
|---|---|---|
| Name | 85% | **100%** |
| Department | 0% | **100%** |
| Email | 15% | **100%** |
| Phone | 92% | 93% |
| Issue | 100% | 100% |
| Category | 85% | 93% |
| Priority | 46% | **93%** |
| Start time | 0% | **100%** |
| Work blocked | 0% | **100%** |
| Ticket created | 100% | 100% |

**Remaining misses (2 calls):**

- **The fast talker's phone number.** STT drops one digit from rapid-fire digits ("845-550-333"), even with one-digit-at-a-time re-prompts. That caller has no caller ID, so the call escalates. On the phone path, caller ID normally makes this question unnecessary.
- **A noisy "Outlook password was reset"** was classified Microsoft 365 rather than Password. That's defensible either way.

### 2.5 Response quality

- **Misunderstood turns:** 26 → 3 per ~100.
- **Calls fully correct:** 1/14 → 14/16.
- **Turns per call:** 7.6 before, 7.8 after. There are more questions now (details, name read-back, email read-back), but far fewer repeats. Spelling and read-back add about one turn per call, and in exchange every call's name and email are right.

---

## 3. Files changed

**Backend, the voice agent itself (affects real phone calls):**

- `app/voice/nlu.py`: fast paths, letter decoder, voice-first prompts, richer extraction, email repair, hedged model calls
- `app/voice/orchestrator.py`: next-question logic, details and name-confirmation states, priority rules, greeting handling, escalation that keeps the problem and asks for a callback number, recognition hints
- `app/voice/scripts.py`: new and shorter lines
- `app/voice/twiml.py`: `<Gather hints>`
- `app/llm/openai_provider.py`, `app/llm/base.py`: per-call model with its own defaults; connection warm-up
- `app/core/config.py`: `VOICE_NLU_MODEL`, `VOICE_NLU_HEDGE_AFTER_SECONDS`, `VOICE_CONFIRM_NAME`
- `app/db/models.py`: states `COLLECT_DETAILS` and `CONFIRM_NAME`
- Migrations: `f2a9c4d7e813` and `a7c3e9f15b24`. Both round-trip cleanly; `alembic check` is clean

**Backend, the simulator's speech:**

- `app/speech/context.py`: per-question recognition prompts and hints, spelling mode
- `app/speech/openai_speech.py`: streaming TTS, cache, spelling model, English-only retry, warm-up
- `app/simulator/*`: the streamed `GET /speech/{turn}` endpoint, speech provider selection, intents
- Config: `SPEECH_TTS_MODEL=tts-1`, `SPEECH_STT_SPELLING_MODEL`, `SPEECH_STT_CONTEXT`, `SPEECH_TTS_CACHE_ENTRIES`

**Frontend:**

- Streamed playback through one `<audio>` element
- "Instant (browser voice)" option
- New ticket fields (department, started, work blocked)
- New states

**Tests:**

- `tests/test_voice_understanding.py`, `tests/test_voice_spelling.py`, plus updates across the suites
- 312 backend tests pass; 28 frontend tests pass; type-check, lint and build are clean
- `tests/conftest.py` now refuses to run against a non-`_test` database (§5)

**Evaluation:**

- `backend/eval/`: the harness, corpus, and spelling and email benchmarks
- Results are in `eval/results/*.json`; the synthesized audio is git-ignored

---

## 4. Remaining risks

1. **Latency from this location is network-bound.** Average caller wait is 2.6 s against the 2.5 s target, with p95 5.2 s. Hosting near OpenAI, or streaming (Realtime API), is the remaining lever. It was not attempted here.
2. **Production STT is Twilio's, not OpenAI's.** Spelling mode, `whisper-1` and context prompts only apply in the simulator. On the phone path, the name read-back, spelled email, decoder, fast paths and prompts all apply, and Twilio gets per-question `hints`. But Twilio's recognizer on spelled letters is **unmeasured** and needs a live-call check before go-live.
3. **The evaluation audio is synthetic** (OpenAI TTS voices with accents). It is a good regression suite but not a substitute for real callers. The tester's real calls were used alongside it, and more real test calls are the best next input.
4. **Rapid-fire digits** can still lose a digit in STT.
5. **About one extra turn per call** for the spelled name and email read-backs. `VOICE_CONFIRM_NAME=false` turns the name read-back off if the business prefers speed over name accuracy.
6. **English only.** Non-English speech is treated as unheard, by requirement.

## 5. Incident during this work

The backend test suite used `DATABASE_URL` from `backend/.env`, which is the **developer's working database**, and its fixtures drop and truncate every table. Running the suite in this session **wiped the dev database's tickets, calls and categories**, and that caused the "Failed to fetch" errors in the tester's first call.

- The categories were re-seeded and the schema stamped current.
- The data itself was not recoverable from here.
- `tests/conftest.py` now uses `<db>_test` automatically and **refuses** any database not ending in `_test`. It has been verified to leave the dev database untouched.

## 6. Production readiness

| Area | Status |
|---|---|
| Understanding (names, emails, priority, details) | **Ready for live-call validation.** 98% of fields on the corpus; each real-call failure has a test |
| Latency, phone path | Agent share 0.58 s avg; Twilio STT and Polly TTS unmeasured. **Validate on a live Twilio call** |
| Latency, simulator | 2.6 s avg / 5.2 s p95 from this network. Acceptable for testing; the browser voice gives about 1.5 s |
| Tests and migrations | 312 + 28 pass; migrations round-trip |
| **Before go-live** | 1) One live Twilio call per corpus scenario, especially spelled names and emails. 2) Deploy the API in a region close to the OpenAI endpoint. 3) Restart the backend so these changes are actually running |

# AI Call Simulator — Performance Benchmark Report

**Date:** 2026-09-28 · **Branch:** `feature/voice-simulator` · **Method:** measured, not estimated. Every figure below comes from the runs described in §1.

## Summary

- **The platform adds little per turn.** With a zero-latency model, a whole turn costs about 10 ms on the server and 32 ms end to end for one caller.
- **Up to 10 concurrent callers,** latency is flat: agent time dominates and queue wait stays under 10 ms on average.
- **At 25 concurrent callers,** the default database pool is the bottleneck. Queue wait averages 533 ms (max 2.7 s), and p95 turn latency rises from 0.9 s to 2.5 s.
- **Raising the pool to 40 connections removes the bottleneck:** 32 ms average queue wait and 1.2 s p95.
- **This is not simulator-specific.** A Twilio call turn holds a pooled connection for its whole NLU round trip in the same way, so the production phone path has the same ceiling of about 15 concurrent turns per worker at default settings.
- **Correctness under load:** 25/25 callers passed in every configuration, with a 0% request error rate and zero cross-talk between sessions. The isolation checks passed after every run: 42 SIMULATOR tickets were created and none appeared in any operational view.

## 1. Method

| | |
|---|---|
| Host | Windows 11 Pro developer machine, local PostgreSQL, **one** uvicorn worker |
| Server | `uvicorn app.main:app` on a freshly migrated and seeded scratch database per configuration |
| Model | `LLM_PROVIDER=fake`, which answers with keyword rules after sleeping `FAKE_LLM_LATENCY_MS`. 800 ms stands in for a fast hosted model call; 0 ms isolates platform overhead |
| Speech | Off (`tts: false`, typed turns). STT/TTS provider latency is not part of these numbers |
| Isolation from OpenAI | `OPENAI_BASE_URL` pointed at a closed local port. No request left the machine |
| Driver | The frontend's own load-test runner (`frontend/src/lib/voiceSimulator/loadTest.ts`), run under Node 24 against the live server. Each caller is a seeded mock caller that answers whatever the agent is currently asking, for about 6 turns each |
| Per configuration | 1, 5, 10 and 25 concurrent callers, run in that order, plus a smoke test and isolation checks |

Turn latency is the browser-observed round trip of `POST /process`. Queue wait is the server's time to acquire a pooled connection and the session row lock. LLM is the whole `orchestrator.handle_turn`.

## 2. Results

### 2.1 Default pool (5 + 10 overflow = 15), 800 ms model

| Callers | Passed | Error rate | Turn avg | Turn p95 | Turn max | LLM avg | Queue avg | Queue max | Ticket create avg / max | Throughput |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1/1 | 0% | 830 ms | 843 ms | 843 ms | 803 ms | 3 ms | 3 ms | 3 / 3 ms | 1.2 turns/s |
| 5 | 5/5 | 0% | 855 ms | 900 ms | 901 ms | 819 ms | 6 ms | 9 ms | 9 / 11 ms | 5.5 turns/s |
| 10 | 10/10 | 0% | 859 ms | 908 ms | 1.00 s | 811 ms | 8 ms | 49 ms | 7 / 14 ms | 10.2 turns/s |
| **25** | 25/25 | 0% | **1.43 s** | **2.49 s** | **3.61 s** | 811 ms | **533 ms** | **2.73 s** | 10 / 30 ms | 13.6 turns/s |

### 2.2 Larger pool (20 + 20 = 40), 800 ms model

| Callers | Passed | Error rate | Turn avg | Turn p95 | Turn max | LLM avg | Queue avg | Queue max | Ticket create avg / max | Throughput |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1/1 | 0% | 852 ms | 878 ms | 878 ms | 821 ms | 3 ms | 5 ms | 3 / 3 ms | 1.2 turns/s |
| 5 | 5/5 | 0% | 860 ms | 908 ms | 908 ms | 813 ms | 7 ms | 17 ms | 9 / 14 ms | 5.4 turns/s |
| 10 | 10/10 | 0% | 861 ms | 925 ms | 991 ms | 813 ms | 8 ms | 52 ms | 8 / 16 ms | 10.1 turns/s |
| **25** | 25/25 | 0% | **963 ms** | **1.18 s** | **1.20 s** | 823 ms | **32 ms** | **111 ms** | 25 / 49 ms | **20.0 turns/s** |

### 2.3 Default pool, 0 ms model (platform overhead)

| Callers | Passed | Turn avg | Turn p95 | Turn max | Server avg | Queue avg | Queue max | Ticket create avg / max | Throughput |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1/1 | 32 ms | 43 ms | 43 ms | 10 ms | 3 ms | 3 ms | 3 / 3 ms | 25.8 turns/s |
| 5 | 5/5 | 74 ms | 162 ms | 166 ms | 37 ms | 10 ms | 15 ms | 8 / 17 ms | 36.8 turns/s |
| 10 | 10/10 | 173 ms | 393 ms | 421 ms | 104 ms | 45 ms | 274 ms | 17 / 34 ms | 39.1 turns/s |
| 25 | 25/25 | 320 ms | 501 ms | 1.01 s | 208 ms | 140 ms | 644 ms | 23 / 47 ms | 50.2 turns/s |

Without model latency, a single worker saturates at roughly 40–50 turns/s, mostly on database round trips (session lock, turn insert, two commits).

### 2.4 Observability roll-up after each run

`GET /voice-simulator/stats?hours=1` reported 44 sessions, 0 active, 42 tickets, 261 turns, 1 failed turn (0.38%), and LLM p95 of 853 ms (800 ms model) or 96 ms (0 ms model). The single failed turn is the smoke test's deliberate STT failure (the provider was unreachable by design). It was correctly counted, correctly re-prompted, and did not surface as an HTTP error.

## 3. Findings

**F1 — Connection-pool ceiling (applies to production phone calls too).** Every voice turn holds one pooled connection from its first query until commit, including the NLU call to the model:

- in the simulator, through the session row lock
- on the Twilio path, through `get_db`'s transaction

With SQLAlchemy's default 15 connections per worker, the 16th concurrent turn waits. At 25 callers that added up to 2.7 s, and on the phone path that delay is dead air for the caller. `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` are now configurable (defaults unchanged), and the 40-connection run shows the fix.

Before raising them in production, check Postgres `max_connections` against workers × (pool + overflow): 4 workers × 40 = 160 (see OPERATIONS_RUNBOOK.md "Database connections exhausted"). The structural fix is to release the connection during the NLU call, which is a refactor of the orchestrator's transaction handling. It is recorded in TECHNICAL_DEBT.md.

**F2 — TTS and STT are kept off the connection.** Early drafts synthesized speech while still inside the turn's transaction, and transcribed while holding a read transaction. Both would have added provider latency (0.5–2 s) to every connection hold. Both now run after commit or rollback. That is why the numbers above are unaffected by speech settings.

**F3 — Ticket creation is cheap.** 3–49 ms, including the category lookup and commit, under all loads. The simulator's random `SIM-` ticket numbers avoid the count-based numbering race that real `HFMG-` numbers have under concurrency (OPERATIONS_RUNBOOK.md "duplicate key on `ix_tickets_ticket_number`").

**F4 — Bundle.** The simulator page is lazy-loaded. It ships as a separate 100 KB chunk (30 KB gzipped) that operational users never download, and the main bundle did not grow.

## 4. What these numbers do not tell you

- **Real model latency.** The 800 ms fake model is a stand-in. Measure gpt-5-nano (or whichever model is set) with the page's latency panel, or by running the same driver against a server with a real key. That costs a small amount of API credit per turn.
- **STT/TTS latency.** Excluded here. It is also not representative of production, which uses Twilio's recognition and Polly (design decision D4).
- **Multiple workers or hosts.** One worker was measured. Session state is in Postgres, so more workers scale turn throughput, but each multiplies connection use (F1).
- **Browser-side timing.** Capture, playback and total-turn times come from the page in a real browser and were not part of this headless run.

## 5. Reproducing

1. Create a scratch database, `alembic upgrade head`, and run `python seed.py`.
2. Start the API against it with `ENABLE_VOICE_SIMULATOR=true LLM_PROVIDER=fake FAKE_LLM_LATENCY_MS=800 OPENAI_BASE_URL=http://127.0.0.1:9/v1 OPENAI_API_KEY=sk-dummy`.
   - On Windows PowerShell, `$env:OPENAI_API_KEY=''` *removes* the variable, and the key from `backend/.env` is used instead. Set a dummy value as shown.
3. Run the page's **Load test** at 1/5/10/25, or drive `runLoadTest` from Node with a `fetch`-based API object (`mockCaller`, `start`, `process`, `end`) pointed at the server.

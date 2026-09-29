# AI Call Simulator — Completion Report

**Date:** 2026-09-28 · **Branch:** `feature/voice-simulator` (from `new-frontend-changes`), **uncommitted, awaiting human review**
**Companion documents:** [VOICE_SIMULATOR.md](../../VOICE_SIMULATOR.md) (architecture, sequence diagrams, API, deployment, testing, operations, troubleshooting) · [design + deviations](../../VOICE_SIMULATOR_DESIGN.md) · [security review](VOICE_SIMULATOR_SECURITY_REVIEW.md) · [performance report](VOICE_SIMULATOR_PERFORMANCE.md)

## Overall result

| | |
|---|---|
| Phases complete | 1–5, all implemented. Audio-loopback WER was not built (see Phase 5) |
| Backend tests | **201 passed**, 0 failed (39 new simulator tests + 162 existing, including every suite the isolation filters touched) |
| Frontend | `tsc -b` clean · `oxlint` clean for new files (the only 2 warnings are pre-existing) · `vite build` OK · **28/28** unit tests (`npm test`) |
| Migration | `alembic upgrade → downgrade -1 → upgrade` verified on a scratch database; `alembic check` reports no model drift |
| End to end | Live server + real Postgres: smoke test, then load at 1/5/10/25 callers in three configurations: **100% pass, 0% request errors, isolation verified after every run** |
| Not verified | **The page has not been opened in a browser.** Microphone capture, VAD, playback, meters, layout at each breakpoint, and screen-reader behaviour were built to spec but not exercised. Also, no real-model (OpenAI) latency run was made |

**One unplanned external call:** during the first live run, an empty `OPENAI_API_KEY` in PowerShell removed the variable, so the server read the real key from `backend/.env`. That sent **one TTS request (the greeting) and one STT request (a 4-byte dummy upload)** to OpenAI. The load test itself used the fake model and no speech. All later runs pointed `OPENAI_BASE_URL` at a closed local port.

---

## Phase 1 — Backend foundation

**Built:** simulator sessions driving `orchestrator.handle_turn`, typed turns, gating, isolation, tracing, limits, idempotency, salvage on hang-up, and the observability roll-up.

- **New files:** `backend/app/simulator/{__init__,schemas,service,routes,metrics,mock_callers}.py`, `backend/app/core/trace.py`, `backend/app/llm/fake_provider.py`, `backend/alembic/versions/e41f7a2c9b10_add_voice_simulator.py`, `backend/purge_simulator_data.py`, `backend/tests/test_voice_simulator.py`
- **Modified:**
  - `db/models.py`: `SIMULATOR` source, `OPERATIONAL_SOURCES`, `is_simulated` and its index, two new models
  - `core/config.py`: simulator, speech and pool settings, plus the production validator
  - `db/base.py`: configurable pool
  - `services/ticket_service.py`: `source=` parameter, `SIM-` numbering, `ticket_create` span, queue excludes SIMULATOR
  - `services/analytics_service.py`: every query excludes simulator data
  - `services/voice_call_service.py`: list, summary and detail exclude simulated calls
  - `voice/orchestrator.py`: source from the session; uses `twiml.spoken_text`
  - `voice/twiml.py`: public `spoken_text` with unescaping; `ends_call`
  - `llm/openai_provider.py`: trace recording, failure reason captured
  - `llm/factory.py`: `fake` provider, refused in production
  - `api/v1/router.py`, `api/v1/settings.py`, `schemas/settings.py`: router registration and the `voice_simulator_enabled` flag
  - `main.py`: audio size middleware
  - `tests/conftest.py`: truncates the new tables
- **API changes:** 11 new endpoints under `/api/v1/voice-simulator`, all 404 unless enabled. `GET /tickets` hides SIMULATOR tickets unless `source=SIMULATOR`. `GET /settings/status` gains `environment.voice_simulator_enabled`.
- **Database changes:** enum value `SIMULATOR`; column `voice_call_sessions.is_simulated` with a partial index; tables `voice_simulator_sessions` and `voice_simulator_turns`. Additive; the downgrade removes simulator data only.
- **Tests:** 39 simulator tests, plus the unchanged suites that cover the modified services, all passing.
- **Known limitations:**
  - no authentication (same as the whole API)
  - global caps, not per-user limits
  - retention purge not scheduled automatically
- **Rollback:** set `ENABLE_VOICE_SIMULATOR=false` (instant, no data change). For the schema: `alembic downgrade dd3a82a4a05a`, which deletes simulator data and nothing else. For code: revert the branch. The shared-code changes (`ticket_service` `source=`, `twiml.spoken_text`, the pool settings) are behaviour-preserving for existing callers, and the existing tests pass unchanged.
- **Performance impact on existing paths:** one extra indexed predicate per analytics, queue and calls query, plus a context-variable lookup per LLM call. Both are negligible.
- **Security:** see the security review. Isolation is covered by tests and the live run; production refusal is covered by tests.
- **Risk carried forward:** the pool ceiling (F1) applies to real calls, not just the simulator.

## Phase 2 — Frontend shell

**Built:** route `/voice-simulator` (lazy-loaded), the conditional "Call Simulator" nav entry, the call state machine, timeline, typed input, live ticket preview, Conversation Inspector (caller and AI transcript, intent, classification and priority, ticket extraction diff, ticket payload, raw AI response with prompts, raw STT, processing times, API calls, errors), keyboard shortcuts, and loading/empty/error states.

- **New files:**
  - `frontend/src/types/voiceSimulator.ts` and `frontend/src/api/voiceSimulator.ts` (with the API call log)
  - `frontend/src/lib/voiceSimulator/{sessionReducer,latency,exports,replay,loadTest,audio}.ts`
  - `frontend/src/hooks/voiceSimulator/{useSimulatorSession,useMicrophone,useRecorder,useVoiceActivity,useAudioPlayer,useSimulatorShortcuts}.ts`
  - `frontend/src/components/voiceSimulator/*` (13 components) and `frontend/src/pages/VoiceSimulatorPage.tsx`
  - `frontend/tests/voiceSimulator.test.ts`
- **Modified:** `App.tsx` (lazy route), `navItems.ts` and `Sidebar.tsx` (conditional entry), `SourceBadge.tsx` and `types/ticket.ts` (SIMULATOR source, including the ticket filter), `types/settings.ts`, `package.json` (`npm test`).
- **Tests:** 28 unit tests: every reducer transition and ignored event, stats, exports, replay, load runner.
- **Known limitations:** light mode only (the app pins it on this branch); not run in a browser.
- **Rollback:** revert. With the backend flag off, the page shows "turned off" and the nav entry is hidden.
- **Performance:** main bundle unchanged; the simulator is a separate 100 KB chunk (30 KB gzipped).
- **Security:** all server text rendered as text; no secrets in the client.
- **Risk carried forward:** browser audio APIs vary. Phase 3's manual matrix is the mitigation, and it is outstanding.

## Phase 3 — Audio

**Built:**

- `SpeechProvider` protocol, an OpenAI implementation (`gpt-4o-mini-transcribe`, `gpt-4o-mini-tts`, both configurable), and a factory
- `/audio`, with MIME allowlist, size cap before and after parsing, and no audio persisted
- TTS on every reply (and the greeting), degrading to text on failure
- mic capture with device picker and mute; push-to-talk (pointer and Space); continuous listening with energy VAD, and the recorder paused while the agent speaks; optional phone-style 6 s silence timeout
- waveform, mic and speaker meters (`role="meter"`, reduced-motion aware)

Details:

- **New files:** `backend/app/speech/{__init__,base,openai_speech,factory}.py`, plus the hooks and components listed in Phase 2.
- **Tests:** stub-provider tests for success, provider failure (returns `empty`, then re-prompt), size/type/empty/unconfigured limits, the pre-parse 413, and the corrected-transcript override. The live run confirmed that provider failures (connection refused) degrade to text with a recorded error.
- **Known limitations:**
  - no barge-in
  - OpenAI transcription reports no confidence (the field is null)
  - STT/TTS latency is not representative of Twilio and Polly (flagged in the UI)
  - VAD is energy-based
  - no recording-length cap in push-to-talk beyond the byte cap
- **Rollback:** unset the speech settings or the OpenAI key; the page falls back to typed input.
- **Performance:** STT runs without a transaction and TTS after commit, so neither holds DB resources.
- **Security:** audio is held in memory for the provider call only.
- **Risk carried forward:** untested in real browsers.

## Phase 4 — Latency and observability

**Built:**

- server timings: STT, LLM (whole agent turn), TTS, queue wait, ticket creation, server total
- browser timings: capture, playback start and duration, total turn (caller stops talking → first agent audio), reported via `client-metrics`
- latency panel: current, average, p95 and max per stage, budget colouring against the phone constraint, a trend chart, and a per-turn waterfall
- `GET /stats` and a health strip: session count, active sessions, tickets, turns, failure rate, and STT/LLM/TTS/end-to-end latency
- the `hfmg.simulator` per-turn log line, with no caller text

Details:

- **Tests:** backend metrics and stats roll-up; frontend stats, percentile and budget functions.
- **Known limitations:** browser timings are self-reported and can't be verified server-side; there is no metrics export to an external system (Prometheus etc.), consistent with the rest of the app today.
- **Performance and security:** read-only aggregates.

## Phase 5 — Testing tools

**Built:**

- transcript `.txt`, metrics `.csv`, session `.json` and ticket `.json` exports (from server data)
- mock callers (seeded, state-keyed answers, expectations) with autopilot
- random issue bank (23 hand-written utterances across the six voice categories, including prompt-injection and "person at the front desk" cases)
- replay with divergence detection and outcome diff
- load test at 1/5/10/25 callers: average, p95 and max latency, queue wait, error rate, ticket-creation latency and throughput, with CSV/JSON export
- `purge_simulator_data.py`

Details:

- **Tests:** mock-caller determinism, category coverage, a full mock-driven call, runner concurrency, abort and refusal handling, replay divergence.
- **Not built:** audio loopback with word error rate (design §8.8).
- **Benchmark:** see the performance report. 25/25 passed at every level; the pool bottleneck appears at 25 callers and is fixed by `DB_POOL_SIZE`.

## Final security review

Complete. See the [security review](VOICE_SIMULATOR_SECURITY_REVIEW.md). Two issues were found and fixed:

- **S1:** email flooding via `send_notifications`, now behind a server-side opt-in
- **S2:** oversized uploads parsed before the cap was checked, now rejected before parsing

Six risks are accepted for internal test environments: no auth, readable-by-UUID sessions, PHI in transcripts pending the scheduled purge, chunked uploads, spoofable browser timings, and the shared pool.

---

## Production readiness assessment

The simulator is **not a production feature, by design.** It is refused in production by configuration, and that is the correct end state. The question is whether it is ready for **internal test and staging use**, and whether it is safe to **merge** alongside production code.

### Safe to merge? **Yes, after review.**

| Criterion | Status |
|---|---|
| Production cannot run it | ✅ Startup refusal, per-request 404, fake LLM refused. Tested |
| No behaviour change on the phone path | ✅ Same orchestrator code; `source=PHONE` and `HFMG-` numbering unchanged; all voice agent and webhook tests pass unchanged |
| No change to operational data or views | ✅ Filters verified by tests and a live run; production has no simulator rows to filter |
| Migration reversible | ✅ Round trip verified; the downgrade touches simulator data only |
| Default config unchanged | ✅ Flag off; pool 5/10 unchanged; `ENVIRONMENT` default `development` as before |

### Ready for internal test and staging use? **Yes, with four conditions.**

1. **Open it in a browser and run the manual checklist** (VOICE_SIMULATOR.md §4) on at least Chrome and Safari before telling QA or stakeholders it works by voice. Typed input is verified end to end; voice is not.
2. **Schedule `purge_simulator_data.py` daily** in that environment.
3. **Use a separate OpenAI key with a spend limit** for the test environment.
4. **Behind Nginx,** add the `client_max_body_size 3m` location for `/audio`, keeping it internal-only (VOICE_SIMULATOR.md §3).

### Findings that matter beyond the simulator

- **F1, pool ceiling on real phone calls** (performance report §3). At default settings a worker queues voice turns beyond 15 concurrent. That is well above expected HFMG call volume today, but it is the first capacity limit the phone agent will hit. Recorded in TECHNICAL_DEBT.md, and the pool is now tunable without code changes.
- **`twiml.spoken_text` now unescapes XML entities.** Previously any `&`, `<` or `>` in the agent's spoken text (for example an email or name read back) was stored in phone transcripts as `&amp;`, `&lt;` or `&gt;`. This is a small correctness fix to the production transcript.

### Suggested next steps, in order

1. Human code review of this branch, then commit (nothing has been committed).
2. Browser verification (condition 1).
3. One real-model latency run on the page, to replace the 800 ms stand-in with a measured number.
4. Decide on F1 before Twilio go-live: raise the pool or refactor the transaction.

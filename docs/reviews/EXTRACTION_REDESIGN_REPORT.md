# Extraction redesign: final implementation report

Strict extraction of the three impact facts that decide ticket priority (`work_blocked`, `patient_care_affected`, `affected_scope`). Implemented on branch `feature/sip-only`, phases 1 to 9, behind the flag `VOICE_STRICT_EXTRACTION` (default **off**).

Design background: [VOICE_AGENT_DESIGN.md](../../VOICE_AGENT_DESIGN.md) section 5.1 and [CALL_FLOW.md](../../CALL_FLOW.md) section 2.1. Measurements: [baseline](EXTRACTION_BASELINE.md) and [validation](EXTRACTION_STRICT_VALIDATION.md).

## 1. What was wrong and what changed

The model inferred the facts instead of reading them. "Outlook won't open" became "can't work" (High); "I use this for patient charts" became "patient care affected" (Critical); the same sentence could get different priorities. The question that would have settled it was skipped, because a guessed value counted as "already known".

Now a fact is recorded only if the caller said it, and the code decides whether to believe the model:

| Layer | What it does |
|---|---|
| Three-state facts (`facts.py`) | true / false / **unknown**, each with evidence, source, confidence and an unknown reason; precedence rules; legacy `True/False/None` mirror, no migration |
| Strict prompts (`strict_extraction.py`) | `{value, basis, evidence}`; descriptions name what is *not* enough; stated-impact priority guidance |
| Grounding | the quote must really appear in what the caller said |
| Phrase gate (`phrase_gate.py`) | strong explicit patterns; counter-evidence makes a conflict; symptoms, clinical context, a single-app login failure and system faults are not enough |
| Verifier (`nlu.verify_fact`) | a second tiny model call sees only the quote and must clearly confirm every accepted `true` safety fact; failure means unknown |
| Clarification (`orchestrator.py`) | asks only what is unknown and can change the priority; at most twice per fact and twice per call; no loops |
| Priority (`priority.assess_facts`) | true boosts, false caps, unknown does neither and is reported as unverified; `NEEDS TRIAGE REVIEW` for unresolved clinical safety facts; the caller's words quoted on the ticket |

## 2. Modified files

New:
- `backend/app/voice/facts.py`, `phrase_gate.py`, `strict_extraction.py`
- `backend/eval/extraction_corpus.py`, `extraction_eval.py`, `conversation_eval.py`
- tests: `test_facts.py`, `test_strict_extraction.py`, `test_clarification.py`, `test_phrase_gate.py`, `test_verifier.py`, `test_priority_integration.py`, `test_extraction_eval.py`
- docs: `EXTRACTION_BASELINE.md`, `EXTRACTION_STRICT_VALIDATION.md`, this report; result files under `backend/eval/results/` (`extraction-*.json`, `conversation-*.json`)

Modified:
- `backend/app/voice/nlu.py` (strict schemas and prompts for the three interpret functions, `interpret_clarification`, `verify_fact`, per-call timeout/hedge/retry)
- `backend/app/voice/orchestrator.py` (facts recorded with evidence, clarification flow, priority and ticket text)
- `backend/app/voice/priority.py` (`assess_facts`, `triage_note`, extra `Assessment` fields; `assess` unchanged)
- `backend/app/voice/scripts.py` (question wordings, read-back lines)
- `backend/app/speech/context.py` (speech-recognition hints for the clarification questions, also sent to the SIP gateway as `stt_prompt`)
- `backend/app/llm/fake_provider.py` (follows the same rules, so the simulator and tests behave like strict mode)
- `backend/app/core/config.py`, `backend/.env.example` (four settings)
- docs: `CALL_FLOW.md`, `VOICE_AGENT_DESIGN.md`, `DEPLOYMENT_GUIDE.md`, `docs/DOCUMENTATION_INDEX.md`

No database migration, no API contract change, no gateway change.

## 3. Commits (branch `feature/sip-only`, on top of `d530a2b`)

```
8000924 phase-1-baseline
5d5f817 phase-2-facts-engine
c2bfac1 phase-3-strict-extraction
29a512b phase-4-clarification
bcbc104 phase-5-phrase-gate
1dd1ede phase-6-verifier
5dd1e9f phase-7-priority-integration
77edfe2 phase-8-validation
(phase-9-cleanup: this commit)
```

## 4. Test results

- Backend: **702 passed** (414 before this work; 288 new). New tests per file: facts 42, strict extraction 36, clarification 39, phrase gate 113, verifier 26, priority integration 24, evaluation 8.
- The original suite passes unchanged with the flag off (no test was loosened; two phase-3 tests were updated when the phrase gate changed what is accepted, and one test file was made sync to fix an event-loop clash).
- Tests run against a scratch database (`hfmg_sip_test`) because the local dev database holds unrelated simulator data.

## 5. Accuracy comparison

Same corpus (68 cases, 340 scored runs), same model (`gpt-4.1-mini`). Truth = what the caller explicitly stated.

| Metric | Original | Strict |
|---|---|---|
| work_blocked invented (value where none stated) | 61.2% | **0.0%** |
| work_blocked false True | 28.2% | **0.0%** |
| work_blocked missed True | 0.0% | 0.0% |
| patient_care false True | 5.6% | **0.0%** |
| scope invented | 62.9% | **0.0%** |
| false HIGH | 15.6% | **4.7%** |
| false CRITICAL | 5.6% | **0.0%** |
| priority accuracy | 75.6% | **94.7%** |
| stable across runs | 83.8% | 95.6% |

Full conversations (11 scenarios x 3 runs): ticket priority correct 100% in both modes; strict asks **0.73 extra turns per call**.

What remains: 4.7% of runs still end in a false High. These are the model's own High rating for statements where the caller said nothing about their work ("The person at the front desk can't print anything today"). By design, unknown work impact is neither boosted nor capped. If you want those capped as well, that is a one-line rule change in `priority.assess_facts` and a trade-off with under-triage.

## 6. Deployment

The code is off by default, so pulling it changes nothing until you turn it on.

1. Pull and restart the backend (no migration; `alembic current` is unchanged).
2. Turn it on in the simulator first: set `VOICE_STRICT_EXTRACTION=true` in `backend/.env`, restart, and try the audit sentences ("Outlook won't open", "I use this for patient charts", "I can't work"). Check the simulator's ticket preview and the ticket text.
3. Place a few real calls. Watch the backend log lines `fact work_blocked: ...` (conflicts) and the new ticket lines (`Priority basis`, `Work impact: not confirmed`, `NEEDS TRIAGE REVIEW`).
4. Optional tuning: `VOICE_MAX_CLARIFICATION_TURNS` (default 2), `VOICE_VERIFY_SAFETY_FACTS` (default true), `VOICE_VERIFIER_TIMEOUT_SECONDS` (default 3.0).

The gateway needs no change. Its transcription prompt (`stt_prompt`) now follows the clarification questions automatically.

## 7. Rollback

Set `VOICE_STRICT_EXTRACTION=false` (or remove the line) and restart the backend. Nothing else is needed: the original code path is untouched, calls in progress simply continue under it, and tickets already created keep their text. To go back further, `git revert` the phase commits or check out `d530a2b`; no database change has to be undone.

## 8. Remaining risks

- **Speech recognition.** If the transcript drops the "n't", "I can't work" becomes "I can work" and every layer here sees the wrong words. The read-back lets the caller correct it. Passing the transcription confidence from the gateway would help (the gateway currently sends none).
- **Measured on text, not on live calls.** The corpus and the conversation scenarios are written utterances and a cooperative simulated caller. Real callers answer clarification questions vaguely; expect some "unknown" tickets, which is the intended safe outcome.
- **More questions for callers who do not state impact.** About 0.7 turns per call on average here; it will be higher if your callers describe symptoms more than impact. Watch call length.
- **Pattern lists miss new phrasings.** A missed phrase costs one clarification question, never a wrong priority. Review the "unknown" tickets periodically and extend `phrase_gate.py`; the corpus test fails if a change makes the gate contradict the labelled truth.
- **Verifier cost and latency.** One short call on turns that claim a safety fact; its first prompt over-rejected genuine statements and was aligned with the gate (see the phase 6 commit). Re-run `eval.extraction_eval` after any model change.
- **Model rating still passes through when impact is unknown** (the 4.7% above).
- **Eval environment.** The evaluation harnesses call the live model with your configured key and cost a few cents per run.

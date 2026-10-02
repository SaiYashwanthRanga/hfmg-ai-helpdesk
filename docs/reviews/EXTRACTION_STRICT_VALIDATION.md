# Extraction redesign: validation report

Compares the original extraction (commit `d530a2b`) with strict extraction after phases 1-7. Same corpus, same model (`gpt-4.1-mini`), same scoring.
- Corpus: `backend/eval/extraction_corpus.py`, 68 cases. Truth = what the caller **explicitly stated**.
- Reproduce: `cd backend && python -m eval.extraction_eval run --label strict-final --runs 5 --strict`, `python -m eval.conversation_eval run --label strict --runs 3 --strict`.

## 1. Headline: baseline vs final (340 runs each)

baseline: commit `d530a2b`, strict=False, 340 runs.  
strict-final: commit `5dd1e9f`, strict=True, 340 runs.

| Metric | baseline | strict-final | Change |
|---|---|---|---|
| work_blocked accuracy | 37.1% | 100.0% | +62.9 better |
| work_blocked invented (value where none stated) | 61.2% | 0.0% | -61.2 better |
| work_blocked false True | 28.2% | 0.0% | -28.2 better |
| work_blocked missed True | 0.0% | 0.0% | +0.0 |
| patient_care accuracy | 92.9% | 100.0% | +7.1 better |
| patient_care false True | 5.6% | 0.0% | -5.6 better |
| patient_care missed True | 1.5% | 0.0% | -1.5 better |
| scope accuracy | 35.6% | 98.5% | +62.9 better |
| scope invented | 62.9% | 0.0% | -62.9 better |
| priority accuracy | 75.6% | 94.7% | +19.1 better |
| false HIGH | 15.6% | 4.7% | -10.9 better |
| false CRITICAL | 5.6% | 0.0% | -5.6 better |
| under-triage | 1.5% | 0.0% | -1.5 better |
| stable across runs (cases) | 83.8% | 95.6% | +11.8 better |

## 2. Progress by phase

| Metric | Baseline | 3 Strict prompts | 5 + Phrase gate | 6 + Verifier | 7 + Priority guidance | Final (5 runs) |
|---|---|---|---|---|---|---|
| work_blocked accuracy | 37.1% | 88.2% | 100.0% | 100.0% | 100.0% | 100.0% |
| work_blocked invented (value where none stated) | 61.2% | 9.3% | 0.0% | 0.0% | 0.0% | 0.0% |
| work_blocked false True | 28.2% | 6.4% | 0.0% | 0.0% | 0.0% | 0.0% |
| work_blocked missed True | 0.0% | 0.5% | 0.0% | 0.0% | 0.0% | 0.0% |
| patient_care accuracy | 92.9% | 97.1% | 98.5% | 98.5% | 99.5% | 100.0% |
| patient_care false True | 5.6% | 1.5% | 0.0% | 0.0% | 0.0% | 0.0% |
| patient_care missed True | 1.5% | 1.5% | 1.5% | 1.5% | 0.5% | 0.0% |
| scope accuracy | 35.6% | 92.6% | 98.5% | 98.5% | 98.5% | 98.5% |
| scope invented | 62.9% | 5.9% | 0.0% | 0.0% | 0.0% | 0.0% |
| priority accuracy | 75.6% | 88.2% | 90.7% | 91.7% | 94.1% | 94.7% |
| false HIGH | 15.6% | 5.9% | 4.9% | 4.4% | 3.4% | 4.7% |
| false CRITICAL | 5.6% | 1.5% | 0.0% | 0.0% | 0.0% | 0.0% |
| under-triage | 1.5% | 1.5% | 1.5% | 1.5% | 0.5% | 0.0% |
| stable across runs (cases) | 83.8% | 82.4% | 98.5% | 97.1% | 94.1% | 95.6% |

(Phases 3-7 used 3 runs per case, the baseline and final 5.)

## 3. What is left

False HIGH remaining (model's own rating where the caller stated no impact; Phase 7 deliberately does not cap unknown work impact):

| Case | Runs HIGH | Text |
|---|---|---|
| `probe-locked-out-account` | 1/5 | I locked myself out of my account this morning. |
| `probe-teams-meeting` | 5/5 | Teams isn't working for me, I'm in a meeting and can't join. |
| `minor-person-cant-print` | 5/5 | The person at the front desk can't print anything today. |
| `scope-second-floor` | 5/5 | Several of us on the second floor can't print. |

## 4. Full conversations (11 scenarios x 3 runs, text, real model)

| Metric | Original | Strict |
|---|---|---|
| conversations | 33 | 33 |
| ticket_created_pct | 100.0 | 100.0 |
| priority_correct_pct | 100.0 | 100.0 |
| work_blocked_correct_pct | 100.0 | 100.0 |
| false_high_pct | 0.0 | 0.0 |
| false_critical_pct | 0.0 | 0.0 |
| avg_turns | 7.36 | 8.09 |
| avg_clarification_turns | 0 | 0.73 |
| needs_triage_pct | 0.0 | 0.0 |

Strict mode asks about impact only when it was not stated, at most twice per fact and twice per call: on these scenarios that is **0.73 extra turns per call**. The scenarios' callers state their impact clearly, so the original mode already scores 100% here; the gain is on callers who do not (section 1).

## 5. Notes

- `false CRITICAL` and invented/false-True facts are 0% in this corpus; the safety layers (phrase gate, verifier, three-state unknown) are what removed them. The 5.6% false Critical in the baseline came from `patient_care_affected` being inferred.
- Temperature is already 0 for the voice model, so the baseline instability (16% of cases) was the model's own variance on inferred fields; strict mode removes most of it by returning `null` instead of guessing.
- The verifier adds one short model call only on turns that claim `work_blocked` or `patient_care_affected` as true. Its first prompt over-rejected genuine statements; it was aligned with the phrase gate's examples (see the phase 6 commit).
- Not measured here: speech-recognition errors (a dropped "n't"), real callers who answer clarification questions vaguely, and live latency on the production server. See the final report.

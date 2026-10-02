# Extraction baseline (before strict extraction)

Measured on the current code (commit `d530a2b`, `gpt-4.1-mini`) before any of the remediation work. This is the reference every later phase is compared with.

- Corpus: `backend/eval/extraction_corpus.py`, 68 cases (audit statements and probes, the simulator's issue list, the voice-eval scenarios, new adversarial cases).
- Truth is what the caller **explicitly stated**; `None` means not stated. A defensible priority set is recorded per case.
- Reproduce: `cd backend && python -m eval.extraction_eval run --label baseline --runs 5`. Raw results: `backend/eval/results/extraction-baseline.json`.

## Results (68 cases x 5 runs = 340 scored runs, 0 errors)

| Metric | Baseline |
|---|---|
| work_blocked accuracy | 37.1% |
| work_blocked invented (value where none stated) | 61.2% |
| work_blocked false True | 28.2% |
| work_blocked missed True | 0.0% |
| patient_care accuracy | 92.9% |
| patient_care false True | 5.6% |
| patient_care missed True | 1.5% |
| scope accuracy | 35.6% |
| scope invented | 62.9% |
| priority accuracy | 75.6% |
| false HIGH | 15.6% |
| false CRITICAL | 5.6% |
| under-triage | 1.5% |
| stable across runs (cases) | 83.8% |

## What this shows

- **`work_blocked` is invented in about three of five runs** where the caller said nothing about their work, and 28% of all runs return a `true` that was not stated.
- That feeds the rules: **15.6% of runs end in a false High and 5.6% in a false Critical.**
- Patient care is mostly right (92.9%) but 5.6% of runs assert it without being told; each of those becomes Critical.
- Scope is invented in about 63% of runs where it was not stated (usually `one_person`).
- 16% of cases give different answers on different runs of the same sentence.

## Cases that produced a false High

| Case | Runs | Text |
|---|---|---|
| `audit-outlook-wont-open` | 5/5 | Outlook won't open. |
| `probe-locked-out-account` | 5/5 | I locked myself out of my account this morning. |
| `probe-teams-meeting` | 5/5 | Teams isn't working for me, I'm in a meeting and can't join. |
| `blocked-locked-ecw` | 5/5 | I got locked out of eClinicalWorks after too many tries. |
| `minor-person-cant-print` | 5/5 | The person at the front desk can't print anything today. |
| `scope-second-floor` | 5/5 | Several of us on the second floor can't print. |
| `details-bare-no` | 5/5 | No. |
| `details-eval-cant-see-schedule` | 5/5 | About twenty minutes ago. No, I can't see my schedule or chart anything. |
| `audit-forgot-password` | 4/5 | I forgot my password. |
| `working-outlook-phone` | 4/5 | My Outlook won't open, it just spins, but I can get my email on my phone. |
| `minor-excel-spreadsheet` | 2/5 | Excel freezes whenever I open the monthly scheduling spreadsheet. |
| `details-using-phone` | 2/5 | Since yesterday. I'm using my phone instead. |
| `minor-mfa` | 1/5 | My MFA codes stopped coming to my new phone. |

## Cases that produced a false Critical

| Case | Runs | Text |
|---|---|---|
| `probe-nurse-slow-laptop` | 5/5 | My laptop is slow, and I'm a nurse so I need it for patient charts. |
| `minor-label-printer` | 5/5 | The label printer in the lab stopped working and we can't label specimens. |
| `ctx-patients-waiting-printer` | 5/5 | Patients are waiting but the printer in the lobby is out of toner. |
| `ctx-nurse-badge` | 4/5 | I'm a nurse and my badge reader isn't working. |

## Unstable cases (runs disagreed)

| Case | Final priorities across 5 runs |
|---|---|
| `audit-forgot-password` | {'HIGH': 4, 'MEDIUM': 1} |
| `blocked-cant-checkin-patients` | {'URGENT': 5} |
| `blocked-internet-site` | {'URGENT': 5} |
| `working-outlook-phone` | {'MEDIUM': 1, 'HIGH': 4} |
| `working-wired-fine` | {'MEDIUM': 5} |
| `minor-ecw-question` | {'LOW': 4, 'MEDIUM': 1} |
| `minor-password-expires` | {'MEDIUM': 4, 'LOW': 1} |
| `minor-mfa` | {'MEDIUM': 4, 'HIGH': 1} |
| `minor-excel-spreadsheet` | {'HIGH': 2, 'MEDIUM': 3} |
| `ctx-nurse-badge` | {'URGENT': 4, 'MEDIUM': 1} |
| `details-using-phone` | {'MEDIUM': 3, 'HIGH': 2} |

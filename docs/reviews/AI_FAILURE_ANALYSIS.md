# AI Summary Failure Analysis

Date: 2026-09-21. Scope: why AI summaries "sometimes fail", and the fix.

## Root cause

**`gpt-5-nano` is a reasoning model, and the app let it reason at the default ("medium") effort.** Hidden reasoning tokens count against `max_output_tokens` (2000). Observed live against the real OpenAI API with the real prompt:

```
POST https://api.openai.com/v1/responses "HTTP/1.1 200 OK"
OpenAI returned no output text: status=incomplete
  incomplete_details=IncompleteDetails(reason='max_output_tokens')
  usage=... output_tokens=1984, reasoning_tokens=1984
Model returned unparseable JSON for schema ticket_summary
```

- 1984 of 2000 output tokens went to reasoning, leaving no room for the answer. `output_text` was empty, so `json.loads("")` failed and the ticket became `FAILED`. That call took 16.5s.
- A second, very short ticket ("help") reasoned past the 20s client timeout and failed the same way (`OpenAI call exceeded 20.0s budget`).
- Requests do reach OpenAI (HTTP 200); the failure is in what comes back, not delivery.

Why it was "sometimes": how long the model reasons varies with the input, so some tickets finished inside the budget and others did not.

### Contributing defects (also fixed)

| Defect | Evidence | Effect |
|---|---|---|
| Real error hidden | Retryable errors logged only `type(exc).__name__`; `generate_summary_for_ticket` collapsed every failure into one generic warning | Could not diagnose from logs |
| Empty-output cause never logged | No log of `response.status` / `incomplete_details` | The actual cause above was invisible |
| No try/except around the background task | `summarizer.py` (pre-fix) had none; already flagged in PRODUCTION_HARDENING_REPORT.md finding 3 | DB error, ticket deleted mid-flight, or `.format()` error left the ticket at `PENDING` forever with no log line |
| Missing API key left ticket `PENDING` | Pre-fix no-op branch returned without writing status; the old test `test_regenerate_summary_sets_pending_when_enabled` asserted `PENDING` | Permanent "generating..." |

### Not explained

AI_QUALITY_REVIEW.md recorded one failure ~27 ms after ticket creation, too fast for a network round trip. I could not reproduce that with the live API; every failure I saw took 16-20s. It may have been an earlier configuration or transient state. The new logging will identify it if it recurs.

### Known, not fixed

Two rapid regenerate calls can race (last write wins; no row locking). Low likelihood and low impact; left alone to avoid new machinery. Repeated regeneration on an unchanged ticket always issues a new OpenAI call (no dedupe).

## Reproduction steps

1. Set `ENABLE_AI_SUMMARY=true`, `OPENAI_MODEL=gpt-5-nano`, valid `OPENAI_API_KEY`, no `OPENAI_REASONING_EFFORT`, on the pre-fix code.
2. Call the provider with the summary prompt (from `backend/`):
   `.venv/Scripts/python.exe <probe>` using `get_provider().structured(system=_SYSTEM, user=<prompt>, schema_name="ticket_summary", schema=_SCHEMA)`.
3. Observe `status=incomplete ... reasoning_tokens=1984` or the 20s timeout, and `None` returned.

## Fix implemented

`backend/app/llm/openai_provider.py`
- `_default_reasoning_effort()`: for the original gpt-5 family (`gpt-5`, `-mini`, `-nano`) default to `"minimal"` when `OPENAI_REASONING_EFFORT` is unset. gpt-5.1+ and non-reasoning models get no default (different/invalid values). An explicit env var still wins.
- `_describe()`: logs class, HTTP status, request id and message (truncated to 500 chars) for retryable and non-retryable errors.
- Logs `status`, `incomplete_details` and `usage` whenever OpenAI returns empty text.

`backend/app/ai/summarizer.py`
- Body wrapped so any exception is logged with traceback and the ticket is moved to `FAILED` (`_mark_failed` never raises).
- Missing API key now resolves to `FAILED` instead of leaving `PENDING`.
- Logs start (model, description length), completion and failure with elapsed seconds.

Behavior change to note: an unconfigured provider now yields `FAILED` (was: stuck `PENDING`). The frontend already renders `FAILED` with a Regenerate action.

Tests: updated `test_regenerate_summary_sets_pending_when_enabled` and `test_unconfigured_provider_makes_no_call` to expect `FAILED`; added `test_unexpected_exception_marks_failed_and_is_logged` and a parametrized reasoning-default test. Full suite: **116 passed**.

## Verification evidence

Live, real OpenAI + real Postgres, throwaway ticket (deleted afterwards):

| Effort | "No internet in back office" | "help" |
|---|---|---|
| default (before) | failed, 16.5s, empty output | failed, 20.0s timeout |
| `minimal` (now default) | valid summary, 2.8s | valid summary, 2.9s |
| `low` | valid, 5.9s | valid, 3.3s |

Status transitions, live:

```
created:                  PENDING
after generate:           COMPLETED  (3.29s)
after regenerate request: PENDING
after regenerate run:     COMPLETED  (2.42s)
```

- `FAILED` verified by tests (provider returns `None`, empty summary, exception, no API key).
- `DISABLED` verified by test (`ENABLE_AI_SUMMARY=false` makes no call and leaves `DISABLED`).

## Collateral finding: voice would have failed too

The voice NLU shares this provider and has a 4s budget. Measured with the real `interpret_description`:

| Effort | Result |
|---|---|
| `medium` (old behavior) | 4.0-4.3s, `exceeded 4.0s budget`, returned `unable_to_determine=True` on both utterances |
| default now (`minimal`) | 2.7s and 1.8s, correct extraction |

Under the old behavior every voice turn would have hit the timeout and escalated to a human once Twilio goes live.

## Deployment note

The fix is a code default, so redeploying the backend is enough. If the deployed environment explicitly sets `OPENAI_REASONING_EFFORT`, that value overrides the default. I did not inspect the deployed environment's variables.

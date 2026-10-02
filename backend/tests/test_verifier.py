"""The verifier pass: a second reader confirms every accepted `true` safety fact."""

import asyncio

import pytest

from app.core.config import get_settings
from app.llm import fake_provider
from app.voice import facts as facts_mod
from app.voice import nlu, strict_extraction
from app.voice.facts import Source, UnknownReason

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
WB, PC, SC = facts_mod.WORK_BLOCKED, facts_mod.PATIENT_CARE, facts_mod.SCOPE


def claim(value=None, basis="none", evidence=None):
    return {"value": value, "basis": basis, "evidence": evidence}


class Recorder:
    """A verifier stand-in that records every call."""

    def __init__(self, answers=None, default=True, delay=0.0):
        self.calls, self.answers, self.default, self.delay = [], answers or {}, default, delay

    async def __call__(self, field, quote):
        self.calls.append((field, quote))
        if self.delay:
            await asyncio.sleep(self.delay)
        answer = self.answers.get(field, self.default)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def establish(data, text, verifier, fields=facts_mod.FIELDS, source=Source.DESCRIPTION):
    return await strict_extraction.establish_facts(data, text, source, fields=fields, verifier=verifier)


# --- when it runs ----------------------------------------------------------------------------------


async def test_it_does_not_run_when_nothing_claims_a_safety_fact():
    v = Recorder()
    out = await establish({"work_blocked": claim(), "patient_care_affected": claim(), "affected_scope": claim()},
                          "Outlook won't open.", v)
    assert v.calls == []
    assert all(not f.is_known for f in out.values())


async def test_it_does_not_run_for_false_or_scope_facts():
    v = Recorder()
    out = await establish(
        {"work_blocked": claim(False, "explicit", "I can still work"), "patient_care_affected": claim(),
         "affected_scope": claim("whole_site", "explicit", "the whole office")},
        "The whole office is slow but I can still work.", v,
    )
    assert v.calls == []                       # a false work_blocked and a scope are not verified
    assert out[WB].value is False and out[SC].value == "whole_site"


async def test_it_runs_once_per_true_safety_fact_and_in_parallel():
    v = Recorder(delay=0.2)
    text = "We can't check in any patients and I can't work."
    started = asyncio.get_running_loop().time()
    out = await establish({"work_blocked": claim(True, "explicit", "I can't work"),
                           "patient_care_affected": claim(True, "explicit", "We can't check in any patients"),
                           "affected_scope": claim()}, text, v)
    elapsed = asyncio.get_running_loop().time() - started
    assert sorted(f for f, _ in v.calls) == [PC, WB]
    assert elapsed < 0.35                      # two 0.2 s checks ran together, not one after the other
    assert out[WB].value is True and out[PC].value is True


async def test_it_also_checks_a_true_the_words_established_when_the_model_found_nothing():
    v = Recorder()
    out = await establish({"work_blocked": claim(), "patient_care_affected": claim(), "affected_scope": claim()},
                          "I cannot do my job right now.", v)
    assert v.calls == [(WB, "i cannot do my job")]        # it reads the matched words, not the whole call
    assert out[WB].value is True


async def test_no_verifier_means_no_extra_checks():
    out = await establish({"work_blocked": claim(True, "explicit", "I can't work"), "patient_care_affected": claim(),
                           "affected_scope": claim()}, "I can't work.", None)
    assert out[WB].value is True and out[WB].confidence == strict_extraction.GATE_CONFIDENCE


# --- what it decides -------------------------------------------------------------------------------


async def test_confirmed_fact_is_kept_with_higher_confidence_and_its_evidence():
    out = await establish({"work_blocked": claim(True, "explicit", "can't work"), "patient_care_affected": claim(),
                           "affected_scope": claim()}, "I can't work.", Recorder(default=True))
    f = out[WB]
    assert f.value is True and f.evidence == "can't work" and f.confidence >= strict_extraction.VERIFIED_CONFIDENCE
    assert f.is_trusted()


@pytest.mark.parametrize("answer", [False, None, RuntimeError("boom"), asyncio.TimeoutError()])
async def test_anything_but_a_clear_yes_leaves_the_fact_unknown(answer):
    out = await establish({"work_blocked": claim(True, "explicit", "I can't work"), "patient_care_affected": claim(),
                           "affected_scope": claim()}, "I can't work.", Recorder(answers={WB: answer}))
    f = out[WB]
    assert f.value is None and f.reason is UnknownReason.VERIFIER_REJECTED
    assert f.evidence                                   # the rejected quote is kept for the audit trail
    assert not f.is_trusted()


async def test_each_safety_fact_is_judged_on_its_own():
    out = await establish(
        {"work_blocked": claim(True, "explicit", "I can't work"),
         "patient_care_affected": claim(True, "explicit", "We can't check in any patients"), "affected_scope": claim()},
        "We can't check in any patients and I can't work.", Recorder(answers={WB: True, PC: False}),
    )
    assert out[WB].value is True and out[PC].value is None


# --- nlu.verify_fact ---------------------------------------------------------------------------------


async def test_verify_fact_sends_only_the_quote_and_a_minimal_schema(monkeypatch):
    seen = {}

    async def capture(system, user, name, properties, **kwargs):
        seen.update(system=system, user=user, name=name, properties=properties, kwargs=kwargs)
        return {"answer": "yes"}

    monkeypatch.setattr(nlu, "_call_structured", capture)
    assert await nlu.verify_fact(WB, "I can't work") is True
    assert seen["user"] == 'Statement: "I can\'t work"'              # no transcript, no priority, no history
    assert seen["name"] == "verify_work_blocked"
    assert seen["properties"] == {"answer": {"type": "string", "enum": ["yes", "no", "unclear"]}}
    assert "does not count unless" in seen["system"]
    # latency guard: short timeout, no hedged duplicate, no retry
    assert seen["kwargs"] == {"timeout": settings.voice_verifier_timeout_seconds, "hedge": False, "max_retries": 0}


async def test_verify_fact_asks_the_patient_question_for_patient_care(monkeypatch):
    seen = {}

    async def capture(system, user, name, properties, **kwargs):
        seen["system"], seen["name"] = system, name
        return {"answer": "no"}

    monkeypatch.setattr(nlu, "_call_structured", capture)
    assert await nlu.verify_fact(PC, "I use this for patient charts") is False
    assert seen["name"] == "verify_patient_care_affected"
    assert "cannot currently check in, see or treat" in seen["system"]
    assert "does not count" in seen["system"]


@pytest.mark.parametrize("data,expected", [({"answer": "yes"}, True), ({"answer": "no"}, False),
                                           ({"answer": "unclear"}, None), ({"answer": "maybe"}, None), ({}, None), (None, None)])
async def test_verify_fact_maps_answers(monkeypatch, data, expected):
    async def stub(*args, **kwargs):
        return data

    monkeypatch.setattr(nlu, "_call_structured", stub)
    assert await nlu.verify_fact(WB, "I can't work") is expected


async def test_call_structured_honours_timeout_hedge_and_retry_overrides(monkeypatch):
    calls = []

    class Provider:
        async def structured(self, **kwargs):
            calls.append(kwargs)
            return {"answer": "yes"}

    monkeypatch.setattr(nlu, "get_provider", lambda: Provider())
    monkeypatch.setattr(settings, "voice_nlu_hedge_after_seconds", 0.01)
    await nlu._call_structured("s", "u", "n", {"answer": {"type": "string"}}, timeout=1.5, hedge=False, max_retries=0)
    assert calls[0]["timeout"] == 1.5 and calls[0]["max_retries"] == 0 and len(calls) == 1
    calls.clear()
    await nlu._call_structured("s", "u", "n", {"answer": {"type": "string"}})
    assert calls[0]["timeout"] == settings.voice_nlu_timeout_seconds and calls[0]["max_retries"] == settings.voice_nlu_max_retries


# --- end to end through the extraction functions ----------------------------------------------------


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "voice_strict_extraction", True)
    monkeypatch.setattr(settings, "voice_verify_safety_facts", True)


def model_says(blocked, text):
    return {
        "escalation_requested": False, "is_problem_description": True, "description": "A real problem here.",
        "short_issue": "your Outlook", "category": "Microsoft 365", "priority": "Medium", "category_confidence": "high",
        "caller_name": None, "caller_name_confidence": None, "department": None, "started": None,
        "work_blocked": blocked, "patient_care_affected": claim(), "affected_scope": claim(),
        "patient_context_mentioned": False, "unable_to_determine": False,
    }


async def test_a_rejecting_verifier_blocks_the_fact_through_interpret_description(strict, monkeypatch):
    async def stub(system, user, name, properties, **kwargs):
        if name.startswith("verify_"):
            return {"answer": "no"}
        return model_says(claim(True, "explicit", "I can't work"), "")

    monkeypatch.setattr(nlu, "_call_structured", stub)
    result = await nlu.interpret_description("My Outlook is broken and I can't work.")
    assert result.extras["work_blocked"] is None
    assert result.extras["facts"][WB].reason is UnknownReason.VERIFIER_REJECTED


async def test_a_confirming_verifier_lets_the_fact_through(strict, monkeypatch):
    async def stub(system, user, name, properties, **kwargs):
        return {"answer": "yes"} if name.startswith("verify_") else model_says(claim(True, "explicit", "I can't work"), "")

    monkeypatch.setattr(nlu, "_call_structured", stub)
    result = await nlu.interpret_description("My Outlook is broken and I can't work.")
    assert result.extras["work_blocked"] is True
    assert result.extras["facts"][WB].confidence >= strict_extraction.VERIFIED_CONFIDENCE


async def test_the_setting_turns_the_verifier_off(strict, monkeypatch):
    monkeypatch.setattr(settings, "voice_verify_safety_facts", False)
    names = []

    async def stub(system, user, name, properties, **kwargs):
        names.append(name)
        return model_says(claim(True, "explicit", "I can't work"), "")

    monkeypatch.setattr(nlu, "_call_structured", stub)
    result = await nlu.interpret_description("My Outlook is broken and I can't work.")
    assert names == ["record_issue"] and result.extras["work_blocked"] is True


async def test_no_extra_model_call_when_no_safety_fact_is_claimed(strict, monkeypatch):
    names = []

    async def stub(system, user, name, properties, **kwargs):
        names.append(name)
        return model_says(claim(), "")

    monkeypatch.setattr(nlu, "_call_structured", stub)
    await nlu.interpret_description("Outlook won't open.")
    assert names == ["record_issue"]                                   # the common case costs nothing extra


async def test_details_and_corrections_are_verified_too(strict, monkeypatch):
    names = []

    async def stub(system, user, name, properties, **kwargs):
        names.append(name)
        if name.startswith("verify_"):
            return {"answer": "no"}
        base = {"escalation_requested": False, "started": None, "affected_scope": claim(), "unable_to_determine": False,
                "work_blocked": claim(True, "explicit", "I can't work"), "nothing_to_change": False, "caller_name": None,
                "department": None, "issue": None, "issue_details": None, "phone_number": None, "category": None}
        return base

    monkeypatch.setattr(nlu, "_call_structured", stub)
    details = await nlu.interpret_details("I can't work at all.", asked="q")
    assert details.extras["work_blocked"] is None
    correction = await nlu.interpret_summary_correction("Actually I can't work at all.", "summary")
    assert "work_blocked" not in correction.extras["changes"]
    assert names.count("verify_work_blocked") == 2


# --- the fake provider (simulator and tests) follow the same rule ---------------------------------------


async def test_the_fake_verifier_confirms_only_what_the_words_state(strict, monkeypatch):
    monkeypatch.setattr(fake_provider.settings, "fake_llm_latency_ms", 0)
    monkeypatch.setattr(nlu, "get_provider", lambda: fake_provider.FakeProvider())
    monkeypatch.setattr(settings, "voice_nlu_hedge_after_seconds", 0)
    assert await nlu.verify_fact(WB, "I can't work") is True
    assert await nlu.verify_fact(WB, "Outlook won't open") is False
    assert await nlu.verify_fact(PC, "we can't check in any patients") is True
    assert await nlu.verify_fact(PC, "I use this for patient charts") is False
    result = await nlu.interpret_description("I can't work, my Outlook won't open.")
    assert result.extras["work_blocked"] is True

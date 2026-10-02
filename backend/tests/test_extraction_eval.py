"""The extraction evaluation corpus and metrics (no network, no database)."""

import pytest

from eval import extraction_eval as ev
from eval.extraction_corpus import CASES, PRIORITIES, SCOPES


def test_corpus_is_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    assert len(CASES) >= 60
    for c in CASES:
        assert c["wb"] in (True, False, None) and c["pc"] in (True, False, None), c["id"]
        assert c["scope"] in SCOPES, c["id"]
        assert c["priority_ok"] and set(c["priority_ok"]) <= PRIORITIES, c["id"]
        assert c["stage"] in ("description", "details"), c["id"]
        assert (c["stage"] == "details") == bool(c["asked"]), c["id"]
        assert c["text"].strip(), c["id"]


def test_corpus_covers_the_audit_failure_modes():
    ids = {c["id"] for c in CASES}
    assert {"audit-outlook-wont-open", "audit-printer-jammed", "audit-forgot-password",
            "probe-nurse-slow-laptop", "probe-locked-out-account"} <= ids
    assert sum(c["wb"] is True for c in CASES) >= 8
    assert sum(c["wb"] is False for c in CASES) >= 5
    assert sum(c["pc"] is True for c in CASES) >= 4
    assert sum(c["wb"] is None for c in CASES) >= 30  # most real calls state nothing about impact
    sources = {c["source"] for c in CASES}
    assert {"audit", "sim", "eval", "edge"} <= sources


def test_truth_is_consistent_with_priority_expectations():
    for c in CASES:
        if c["pc"] is True:
            assert "URGENT" in c["priority_ok"] or "HIGH" in c["priority_ok"], c["id"]
        if c["wb"] is None and c["pc"] is not True and c["scope"] != "whole_site":
            assert "URGENT" not in c["priority_ok"], c["id"]  # nothing stated, so never Critical


def _case(**kw):
    base = dict(id="x", text="t", wb=None, pc=None, scope=None, priority_ok=["MEDIUM"], stage="description")
    return {**base, **kw}


def _run(wb=None, pc=False, scope=None, priority="MEDIUM"):
    return {"wb": wb, "pc": pc, "scope": scope, "priority": priority, "model_priority": "MEDIUM", "rule": "r"}


def test_metrics_count_invented_and_false_priorities():
    cases = [
        _case(id="a", wb=None, priority_ok=["MEDIUM"]),
        _case(id="b", wb=True, pc=True, priority_ok=["URGENT"]),
        _case(id="c", wb=False, priority_ok=["MEDIUM", "LOW"]),
    ]
    results = [
        {"id": "a", "runs": [_run(wb=True, priority="HIGH"), _run(wb=None, priority="MEDIUM")]},   # invented True, false HIGH
        {"id": "b", "runs": [_run(wb=True, pc=False, priority="HIGH"), _run(wb=True, pc=True, priority="URGENT")]},
        {"id": "c", "runs": [_run(wb=False, priority="MEDIUM"), _run(wb=False, priority="URGENT", pc=True)]},  # false Critical + false pc
    ]
    m = ev.compute_metrics(cases, results)
    assert m["runs"] == 6 and m["errors"] == 0
    assert m["wb_invented"] == 1 and m["wb_false_true"] == 1
    assert m["pc_false_true"] == 1 and m["pc_missed_true"] == 1
    assert m["false_high"] == 1 and m["false_critical"] == 1
    assert m["under_triage"] == 1  # HIGH where URGENT was expected
    assert m["prio_correct"] == 3
    assert m["stable_cases"] == 0


def test_errors_are_counted_not_scored():
    cases = [_case(id="a")]
    m = ev.compute_metrics(cases, [{"id": "a", "runs": [{"error": "timeout"}, _run()]}])
    assert m["errors"] == 1 and m["runs"] == 1
    assert m["stable_cases"] == 0  # a failed run means the case was not stable


def test_stability_requires_identical_runs():
    cases = [_case(id="a"), _case(id="b")]
    results = [
        {"id": "a", "runs": [_run(), _run(), _run()]},
        {"id": "b", "runs": [_run(), _run(wb=True, priority="HIGH"), _run()]},
    ]
    assert ev.compute_metrics(cases, results)["stable_cases"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_run_cases_uses_the_injected_extractor_and_survives_failures():
    calls = []

    async def fake_extract(case):
        calls.append(case["id"])
        if case["id"] == "bad":
            raise RuntimeError("boom")
        return _run()

    cases = [_case(id="ok"), _case(id="bad")]
    results = await ev.run_cases(cases, runs=2, extract=fake_extract)
    assert calls.count("ok") == 2 and calls.count("bad") == 2
    assert all("error" in r for r in results[1]["runs"])
    assert all("error" not in r for r in results[0]["runs"])


def test_report_and_comparison_render():
    meta = {"commit": "abc", "when": "now", "model": "m", "strict": False, "cases": 1, "runs": 1}
    cases = [_case(id="a")]
    results = [{"id": "a", "runs": [_run()]}]
    counts = ev.compute_metrics(cases, results)
    data = {"meta": meta, "summary": ev.summarize(counts), "counts": counts, "results": results}
    assert "work_blocked accuracy" in ev.render_report("x", data)
    better = {**data, "meta": {**meta, "strict": True}}
    text = ev.render_comparison("old", data, "new", better)
    assert "| Metric | old | new | Change |" in text

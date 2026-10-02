"""Extraction evaluation: how accurately the model's facts match what callers stated.

Runs each case in extraction_corpus.py through the real extraction functions
(`nlu.interpret_description` / `nlu.interpret_details`) several times, turns
the extracted facts into a final priority with the production rules
(`priority.assess`), and scores:

- work_blocked  three-state accuracy; how often it was *invented* (a value
                where the caller stated none); false True
- patient_care  accuracy of "True or not"; false True (the false Critical risk)
- scope         accuracy; how often it was invented
- priority      share of runs inside `priority_ok`; false High; false Critical
- stability     share of cases whose runs all agreed

Uses the OPENAI_API_KEY in backend/.env and costs a few cents per run
(cases x runs model calls). Nothing touches the database.

    cd backend
    python -m eval.extraction_eval run --label baseline --runs 3
    python -m eval.extraction_eval run --label strict --runs 3 --strict
    python -m eval.extraction_eval compare baseline strict
"""

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from eval.extraction_corpus import CASES

RESULTS_DIR = Path(__file__).parent / "results"


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


async def extract_case(case: dict) -> dict:
    """Extract facts for one case, exactly as the orchestrator would receive them."""
    from app.db.models import Priority
    from app.voice import nlu, priority as priority_rules

    if case["stage"] == "details":
        result = await nlu.interpret_details(case["text"], asked=case["asked"])
        model_priority = Priority(case.get("model_priority") or "MEDIUM")
    else:
        result = await nlu.interpret_description(case["text"])
        model_priority = result.priority or Priority.MEDIUM

    extras = result.extras or {}
    wb = extras.get("work_blocked")
    pc = extras.get("patient_care_affected")
    scope = extras.get("affected_scope")
    assessment = priority_rules.assess(
        model_priority=model_priority, work_blocked=wb, scope=scope, patient_care=pc is True
    )
    return {
        "wb": wb, "pc": pc is True, "scope": scope,
        "model_priority": model_priority.value,
        "priority": assessment.priority.value, "rule": assessment.rule,
    }


async def run_cases(cases: list[dict], runs: int, concurrency: int = 6, extract=extract_case) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)

    async def one(case: dict) -> dict:
        async with sem:
            try:
                return await extract(case)
            except Exception as exc:  # a failed model call is data, not a crash
                return {"error": f"{type(exc).__name__}: {exc}"}

    out = []
    for case in cases:
        results = await asyncio.gather(*[one(case) for _ in range(runs)])
        out.append({"id": case["id"], "runs": results})
    return out


def compute_metrics(cases: list[dict], results: list[dict]) -> dict:
    by_id = {r["id"]: r["runs"] for r in results}
    m = {k: 0 for k in (
        "runs", "errors",
        "wb_total", "wb_correct", "wb_invented", "wb_false_true", "wb_missed_true",
        "pc_total", "pc_correct", "pc_false_true", "pc_missed_true",
        "scope_total", "scope_correct", "scope_invented",
        "prio_total", "prio_correct", "false_high", "false_critical", "under_triage",
        "stable_cases", "cases",
    )}
    for case in cases:
        runs = by_id.get(case["id"])
        if runs is None:
            continue
        m["cases"] += 1
        valid = [r for r in runs if "error" not in r]
        m["errors"] += len(runs) - len(valid)
        for r in valid:
            m["runs"] += 1
            m["wb_total"] += 1
            m["wb_correct"] += r["wb"] == case["wb"]
            m["wb_invented"] += case["wb"] is None and r["wb"] is not None
            m["wb_false_true"] += r["wb"] is True and case["wb"] is not True
            m["wb_missed_true"] += case["wb"] is True and r["wb"] is not True

            m["pc_total"] += 1
            m["pc_correct"] += r["pc"] == (case["pc"] is True)
            m["pc_false_true"] += r["pc"] is True and case["pc"] is not True
            m["pc_missed_true"] += case["pc"] is True and r["pc"] is not True

            m["scope_total"] += 1
            m["scope_correct"] += r["scope"] == case["scope"]
            m["scope_invented"] += case["scope"] is None and r["scope"] is not None

            ok = case["priority_ok"]
            m["prio_total"] += 1
            m["prio_correct"] += r["priority"] in ok
            order = ["LOW", "MEDIUM", "HIGH", "URGENT"]
            rank = order.index(r["priority"])
            over = rank > max(order.index(p) for p in ok)  # above everything defensible
            m["false_high"] += r["priority"] == "HIGH" and over
            m["false_critical"] += r["priority"] == "URGENT" and over
            m["under_triage"] += rank < min(order.index(p) for p in ok)
        if valid and len({(r["wb"], r["pc"], r["scope"], r["priority"]) for r in valid}) == 1 and len(valid) == len(runs):
            m["stable_cases"] += 1
    return m


def _pct(num: int, den: int) -> str:
    return f"{100 * num / den:.1f}%" if den else "n/a"


def summarize(m: dict) -> dict:
    """The headline rates, as numbers (percent)."""
    def rate(a, b):
        return round(100 * m[a] / m[b], 1) if m[b] else None
    return {
        "work_blocked_accuracy": rate("wb_correct", "wb_total"),
        "work_blocked_invented": rate("wb_invented", "wb_total"),
        "work_blocked_false_true": rate("wb_false_true", "wb_total"),
        "work_blocked_missed_true": rate("wb_missed_true", "wb_total"),
        "patient_care_accuracy": rate("pc_correct", "pc_total"),
        "patient_care_false_true": rate("pc_false_true", "pc_total"),
        "patient_care_missed_true": rate("pc_missed_true", "pc_total"),
        "scope_accuracy": rate("scope_correct", "scope_total"),
        "scope_invented": rate("scope_invented", "scope_total"),
        "priority_accuracy": rate("prio_correct", "prio_total"),
        "false_high": rate("false_high", "prio_total"),
        "false_critical": rate("false_critical", "prio_total"),
        "under_triage": rate("under_triage", "prio_total"),
        "stable_cases": rate("stable_cases", "cases"),
    }


def save(label: str, meta: dict, cases: list[dict], results: list[dict]) -> Path:
    metrics = compute_metrics(cases, results)
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"extraction-{label}.json"
    path.write_text(
        json.dumps({"meta": meta, "summary": summarize(metrics), "counts": metrics, "results": results}, indent=2),
        encoding="utf-8",
    )
    return path


ROWS = [
    ("work_blocked accuracy", "work_blocked_accuracy", True),
    ("work_blocked invented (value where none stated)", "work_blocked_invented", False),
    ("work_blocked false True", "work_blocked_false_true", False),
    ("work_blocked missed True", "work_blocked_missed_true", False),
    ("patient_care accuracy", "patient_care_accuracy", True),
    ("patient_care false True", "patient_care_false_true", False),
    ("patient_care missed True", "patient_care_missed_true", False),
    ("scope accuracy", "scope_accuracy", True),
    ("scope invented", "scope_invented", False),
    ("priority accuracy", "priority_accuracy", True),
    ("false HIGH", "false_high", False),
    ("false CRITICAL", "false_critical", False),
    ("under-triage", "under_triage", False),
    ("stable across runs (cases)", "stable_cases", True),
]


def render_report(label: str, data: dict) -> str:
    meta, s = data["meta"], data["summary"]
    lines = [f"# Extraction evaluation: {label}", "",
             f"- commit `{meta['commit']}`, {meta['when']}", f"- NLU model `{meta['model']}`, strict extraction: {meta['strict']}",
             f"- {meta['cases']} cases x {meta['runs']} runs = {data['counts']['runs']} scored runs "
             f"({data['counts']['errors']} errors)", "", "| Metric | Result |", "|---|---|"]
    lines += [f"| {name} | {s[key]}% |" for name, key, _ in ROWS]
    return "\n".join(lines) + "\n"


def render_comparison(a_label: str, a: dict, b_label: str, b: dict) -> str:
    lines = [f"# Extraction evaluation: {a_label} vs {b_label}", "",
             f"{a_label}: commit `{a['meta']['commit']}`, strict={a['meta']['strict']}, {a['counts']['runs']} runs.  ",
             f"{b_label}: commit `{b['meta']['commit']}`, strict={b['meta']['strict']}, {b['counts']['runs']} runs.", "",
             f"| Metric | {a_label} | {b_label} | Change |", "|---|---|---|---|"]
    for name, key, higher_better in ROWS:
        x, y = a["summary"][key], b["summary"][key]
        if x is None or y is None:
            lines.append(f"| {name} | {x} | {y} | |")
            continue
        delta = round(y - x, 1)
        good = (delta > 0) == higher_better
        mark = "" if delta == 0 else (" better" if good else " WORSE")
        lines.append(f"| {name} | {x}% | {y}% | {delta:+}{mark} |")
    return "\n".join(lines) + "\n"


def case_diffs(cases: list[dict], a: dict, b: dict) -> list[str]:
    """Cases whose final priority changed between two runs sets (first run of each)."""
    ra = {r["id"]: r["runs"] for r in a["results"]}
    rb = {r["id"]: r["runs"] for r in b["results"]}
    out = []
    for case in cases:
        xa = next((r for r in ra.get(case["id"], []) if "error" not in r), None)
        xb = next((r for r in rb.get(case["id"], []) if "error" not in r), None)
        if xa and xb and xa["priority"] != xb["priority"]:
            out.append(f"- `{case['id']}`: {xa['priority']} -> {xb['priority']} (ok: {case['priority_ok']})")
    return out


async def _main_run(args) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    if args.strict:
        settings.voice_strict_extraction = True
    cases = [c for c in CASES if not args.only or args.only in c["id"]]
    meta = {
        "label": args.label, "commit": _git_commit(), "when": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "model": settings.voice_nlu_model or settings.openai_model, "strict": bool(getattr(settings, "voice_strict_extraction", False)),
        "cases": len(cases), "runs": args.runs,
    }
    print(f"Running {len(cases)} cases x {args.runs} runs ({meta['model']}, strict={meta['strict']}) ...")
    results = await run_cases(cases, args.runs, args.concurrency)
    path = save(args.label, meta, cases, results)
    data = json.loads(path.read_text(encoding="utf-8"))
    print(render_report(args.label, data))
    print(f"Saved {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--label", required=True)
    run.add_argument("--runs", type=int, default=3)
    run.add_argument("--concurrency", type=int, default=6)
    run.add_argument("--strict", action="store_true", help="enable VOICE_STRICT_EXTRACTION for this run")
    run.add_argument("--only", default="", help="substring filter on case ids")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("a")
    cmp_.add_argument("b")
    args = parser.parse_args()

    if args.cmd == "run":
        asyncio.run(_main_run(args))
    else:
        a = json.loads((RESULTS_DIR / f"extraction-{args.a}.json").read_text(encoding="utf-8"))
        b = json.loads((RESULTS_DIR / f"extraction-{args.b}.json").read_text(encoding="utf-8"))
        print(render_comparison(args.a, a, args.b, b))
        diffs = case_diffs(CASES, a, b)
        if diffs:
            print("Cases whose final priority changed:\n" + "\n".join(diffs))


if __name__ == "__main__":
    sys.exit(main())

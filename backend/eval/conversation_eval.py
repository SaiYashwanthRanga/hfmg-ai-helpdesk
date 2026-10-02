"""Full-conversation evaluation (text, no audio): does strict extraction cost turns, and is the ticket right?

Plays the voice evaluation scenarios (eval/corpus.py) through the real orchestrator and the
real model, once with the original behaviour and once with VOICE_STRICT_EXTRACTION, and
compares what matters end to end:

- the final ticket priority against the scenario's defensible set (false High / Critical)
- work_blocked recorded correctly
- how many turns the call took, and how many were clarification questions
- how often a ticket is flagged NEEDS TRIAGE REVIEW

The caller answers the clarification questions the way the scenario's truth says a cooperative
caller would ("Yes, it's stopping me from working" / "No, I can still work").

Uses its own throwaway database (hfmg_voice_eval, never your dev database) and the OPENAI_API_KEY
in backend/.env.

    cd backend
    python -m eval.conversation_eval run --label baseline --runs 3
    python -m eval.conversation_eval run --label strict --runs 3 --strict
    python -m eval.conversation_eval compare baseline strict
"""

import argparse
import asyncio
import json
import statistics
import sys
import uuid
from datetime import datetime, timezone

from eval import voice_eval  # configures the eval database before the app is imported
from eval.corpus import SCENARIOS
from eval.extraction_eval import RESULTS_DIR, _git_commit

TERMINAL = {"COMPLETED", "ESCALATED", "ABANDONED"}


def clarification_answer(kind: str, truth: dict) -> str:
    """What a cooperative caller says to a clarification question, from the scenario's truth."""
    if kind == "blocked":
        return "Yes, it's stopping me from working." if truth.get("work_blocked") else "No, I can still work."
    if kind == "patient_care":
        return "No, patients are not affected."
    return "No, just me."


async def play(scenario: dict, strict: bool) -> dict:
    from app.core.config import get_settings
    from app.db.base import async_session_factory
    from app.db.models import Category, Ticket
    from app.voice import orchestrator
    from app.voice.session import get_or_create_session

    get_settings().voice_strict_extraction = strict
    truth = scenario["truth"]
    turns: list[dict] = []
    async with async_session_factory() as db:
        session = await get_or_create_session(
            db, call_sid=f"CONV-{uuid.uuid4().hex[:10]}", from_number="simulator", to_number="simulator"
        )
        session.is_simulated = True
        await orchestrator.start_call(session)
        await db.commit()

        for _ in range(40):
            state = session.state.value
            if state in TERMINAL:
                break
            pending = session.collected.get("pending_question")
            if state == "COLLECT_DETAILS" and pending:
                key = f"CLARIFY_{pending.upper()}"
                answer = scenario["answers"].get(key) or clarification_answer(pending, truth)
            else:
                key = voice_eval._answer_key(scenario, state, turns, realistic=False)
                answer = scenario["answers"].get(key)
            if answer is None:
                break
            outcome = await orchestrator.handle_turn(db, session, utterance=answer)
            await db.commit()
            turns.append({
                "state": state, "pending": pending, "caller": answer, "reply": outcome.text,
                "collected_after": dict(session.collected),
            })
            if outcome.hangup:
                break

        ticket = await db.get(Ticket, session.ticket_id) if session.ticket_id else None
        category = await db.get(Category, ticket.category_id) if ticket else None
        ticket_dict = (
            {"priority": ticket.priority.value, "category": category.name if category else None} if ticket else None
        )
        collected = dict(session.collected)
        score = voice_eval.score_call(truth, collected, ticket_dict, bool(session.escalated))
        return {
            "id": scenario["id"],
            "turns": len(turns),
            "clarifications": sum(1 for t in turns if t["pending"]),
            "priority": (ticket_dict or {}).get("priority") or collected.get("priority"),
            "priority_ok": score.get("priority"),
            "work_blocked_ok": score.get("work_blocked"),
            "needs_triage": "NEEDS TRIAGE REVIEW" in (ticket.description if ticket else ""),
            "unverified": collected.get("priority_unverified") or [],
            "ticket_created": ticket is not None,
            "expected": truth.get("priority"),
        }


def summarize(results: list[dict]) -> dict:
    scored = [r for r in results if r["priority_ok"] is not None]
    order = ["LOW", "MEDIUM", "HIGH", "URGENT"]

    def over(r):
        exp = r["expected"] if isinstance(r["expected"], list) else [r["expected"]]
        return r["priority"] in order and order.index(r["priority"]) > max(order.index(p) for p in exp)

    n = len(scored) or 1
    return {
        "conversations": len(results),
        "ticket_created_pct": round(100 * sum(r["ticket_created"] for r in results) / (len(results) or 1), 1),
        "priority_correct_pct": round(100 * sum(bool(r["priority_ok"]) for r in scored) / n, 1),
        "work_blocked_correct_pct": round(100 * sum(bool(r["work_blocked_ok"]) for r in scored) / n, 1),
        "false_high_pct": round(100 * sum(1 for r in scored if over(r) and r["priority"] == "HIGH") / n, 1),
        "false_critical_pct": round(100 * sum(1 for r in scored if over(r) and r["priority"] == "URGENT") / n, 1),
        "avg_turns": round(statistics.mean(r["turns"] for r in results), 2) if results else None,
        "avg_clarification_turns": round(statistics.mean(r["clarifications"] for r in results), 2) if results else None,
        "needs_triage_pct": round(100 * sum(r["needs_triage"] for r in results) / (len(results) or 1), 1),
    }


async def _run(args) -> None:
    await voice_eval._prepare_database()
    scenarios = [s for s in SCENARIOS if not args.only or args.only in s["id"]]
    sem = asyncio.Semaphore(args.concurrency)

    async def one(scenario):
        async with sem:
            try:
                return await play(scenario, args.strict)
            except Exception as exc:  # a failed call is data, not a crash
                return {"id": scenario["id"], "error": f"{type(exc).__name__}: {exc}", "turns": 0, "clarifications": 0,
                        "priority_ok": None, "work_blocked_ok": None, "needs_triage": False, "ticket_created": False,
                        "priority": None, "expected": scenario["truth"].get("priority"), "unverified": []}

    results = []
    for scenario in scenarios:
        results += await asyncio.gather(*[one(scenario) for _ in range(args.runs)])
    out = {
        "meta": {"label": args.label, "strict": args.strict, "commit": _git_commit(), "runs": args.runs,
                 "when": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "scenarios": len(scenarios)},
        "summary": summarize(results),
        "results": results,
    }
    path = RESULTS_DIR / f"conversation-{args.label}.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out["summary"], indent=2))
    errors = [r for r in results if r.get("error")]
    if errors:
        print(f"{len(errors)} conversations errored, e.g. {errors[0]['error']}")
    print(f"Saved {path}")


def compare(a: str, b: str) -> str:
    da = json.loads((RESULTS_DIR / f"conversation-{a}.json").read_text(encoding="utf-8"))
    db = json.loads((RESULTS_DIR / f"conversation-{b}.json").read_text(encoding="utf-8"))
    lines = [f"# Conversation evaluation: {a} vs {b}", "", f"| Metric | {a} | {b} |", "|---|---|---|"]
    for key in da["summary"]:
        lines.append(f"| {key} | {da['summary'][key]} | {db['summary'][key]} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--label", required=True)
    run.add_argument("--runs", type=int, default=3)
    run.add_argument("--concurrency", type=int, default=3)
    run.add_argument("--strict", action="store_true")
    run.add_argument("--only", default="")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("a")
    cmp_.add_argument("b")
    args = parser.parse_args()
    if args.cmd == "run":
        asyncio.run(_run(args))
    else:
        print(compare(args.a, args.b))


if __name__ == "__main__":
    sys.exit(main())

"""Replay real test calls through the current agent, for before/after transcripts.

`eval/results/real_calls_before.json` holds four real calls made against the
previous build (exported from the dev database). This replays the *same
caller lines* -- exactly what speech recognition produced, warts included --
through the current orchestrator with the real language model, answering
whichever question the agent asks next. Text in, text out: it isolates the
agent's conversation logic from speech recognition, which is unchanged.

    cd backend && python -m eval.transcripts            # writes eval/results/real_calls_after.json + .md
"""

import asyncio
import json
import uuid
from collections import deque

from eval.voice_eval import RESULTS, _prepare_database  # sets the eval database before app imports

# What each caller said, in order, to the question the agent was in when they said it.
PERSONAS = {
    "SIM-2026-79A66954": {  # Bluetooth call: uncommon name, spelled correction, "can't work"
        "COLLECT_DESCRIPTION": ["The Bluetooth in my laptop is not working."],
        "COLLECT_DETAILS": ["It started today morning and I can't able to connect my AirPods to my laptop and I can't able to work."],
        "COLLECT_NAME": ["My name is Yashvant and I'm from Development department."],
        "CONFIRM_NAME": ["No, it's not correct. My name is Y-A-S-H-W-A-N-T-H.", "Yes, that's right."],
        "COLLECT_PHONE": ["It's 214-885-9089."],
        "COLLECT_EMAIL": ["I don't want to add any email address right now."],
        "CONFIRM_SUMMARY": ["Yes, that's right."],
        "ANYTHING_ELSE": ["Nothing, thank you."],
    },
    "SIM-2026-185A0C0D": {  # Hotspot call: garbled name, multi-round fix, phone trouble -> escalated
        "COLLECT_DESCRIPTION": ["Yeah, hi, my, like, my hotspot is not turning on in my laptop."],
        "COLLECT_DETAILS": ["Yesterday it was working fine, but today I can't turn it on. Today, from today."],
        "COLLECT_NAME": ["My name is Chindun Natakarani. I am from development department."],
        "CONFIRM_NAME": ["No, I after CH.", "C H A N D U", "N A T E K E R E N I", "Yes, that's right."],
        "COLLECT_PHONE": ["Number is like 11006.", "1-0-0-6", "866-882-085-444"],
        "COLLECT_EMAIL": ["Skip."],
        "CONFIRM_SUMMARY": ["Yes."],
        "ANYTHING_ELSE": ["No, thank you."],
    },
    "SIM-2026-48EC118F": {  # "Can you add H in the last?" -- an edit instruction, and a spelled email
        "COLLECT_DESCRIPTION": ["I'm having a problem with the uh my laptop."],
        "COLLECT_DETAILS": ["Yes, it started this morning and yeah, I can't able to work from this morning."],
        "COLLECT_NAME": ["My name is Yashwant, and my department is AI Developer."],
        "CONFIRM_NAME": ["Can you add H in the last?", "Yes."],
        "COLLECT_PHONE": ["214-885-9089"],
        "COLLECT_EMAIL": ["R A N G A dot S A I Y A S H W A N T H at hfmg.net."],
        "CONFIRM_EMAIL": ["Yes."],
        "CONFIRM_SUMMARY": ["Yes."],
        "ANYTHING_ELSE": ["No, thank you."],
    },
    "SIM-2026-68A67058": {  # headset call: phone noise, then the caller says goodbye at the email question
        "COLLECT_DESCRIPTION": ["My headset is not working."],
        "COLLECT_DETAILS": ["It started this morning."],
        "COLLECT_NAME": ["My name is Yashwant and my department is AI Director."],
        "CONFIRM_NAME": ["Yes."],
        "COLLECT_PHONE": ["It's still on forward, edit file 985.", "214-885-9089"],
        "COLLECT_EMAIL": ["Thank you. Bye bye."],
    },
}


async def replay(persona: dict) -> dict:
    from app.db.base import async_session_factory
    from app.voice import orchestrator, twiml
    from app.voice.session import get_or_create_session

    answers = {state: deque(lines) for state, lines in persona.items()}
    turns = []
    async with async_session_factory() as db:
        # Same conditions as the real calls: a browser session, no caller ID.
        session = await get_or_create_session(
            db, call_sid=f"REPLAY-{uuid.uuid4().hex[:10]}", from_number="simulator", to_number="simulator"
        )
        session.is_simulated = True
        outcome = await orchestrator.start_call(session)
        await db.commit()
        turns.append({"state": "GREETING", "caller": None, "agent": twiml.spoken_text(outcome.twiml), "next": session.state.value})
        for _ in range(30):
            queue = answers.get(session.state.value)
            if not queue:
                break
            state = session.state.value
            line = queue.popleft()
            outcome = await orchestrator.handle_turn(db, session, utterance=line)
            await db.commit()
            turns.append({"state": state, "caller": line, "agent": twiml.spoken_text(outcome.twiml), "next": session.state.value})
            if twiml.ends_call(outcome.twiml):
                break
        ticket = None
        if session.ticket_id:
            from app.db.models import Ticket

            ticket = await db.get(Ticket, session.ticket_id)
        description = ticket.description if ticket else ""
        intake = (
            description.split("--- Intake details ---")[-1].split("--- Call transcript ---")[0].strip()
            if "--- Intake details ---" in description
            else ""
        )
        return {
            "final_state": session.state.value,
            "priority": ticket.priority.value if ticket else None,
            "collected": dict(session.collected),
            "intake_details": intake,
            "turns": turns,
            "escalated": session.escalated,
        }


def render(label: str, call: dict) -> str:
    lines = [f"#### {label}", ""]
    for t in call["turns"]:
        if t.get("caller"):
            lines.append(f"> **Caller:** {t['caller']}")
        lines.append(f"> **Agent** *({t.get('state') or 'GREETING'} → {t.get('next') or t.get('state')})*: {t['agent']}")
        lines.append(">")
    lines.pop()
    lines += ["", f"**Result:** {call['final_state']}, priority **{call['priority']}**"]
    if call.get("intake_details"):
        lines += ["", "```", call["intake_details"], "```"]
    return "\n".join(lines)


async def main() -> None:
    await _prepare_database()
    before = json.loads((RESULTS / "real_calls_before.json").read_text())
    after, sections = {}, []
    for call in before:
        number = call["ticket"]
        result = await replay(PERSONAS[number])
        after[number] = result
        sections.append(
            f"### {number}\n\n{render('Before (your real call)', {**call, 'turns': [{'state': t['state'], 'next': t['next'], 'caller': t['caller'], 'agent': t['agent']} for t in call['turns']]})}"
            f"\n\n{render('After (same caller lines, current agent)', result)}\n"
        )
        print(f"replayed {number}: {len(call['turns'])} turns before -> {len(result['turns'])} after; {result['final_state']} {result['priority']}")
    (RESULTS / "real_calls_after.json").write_text(json.dumps(after, indent=2, default=str), encoding="utf-8")
    (RESULTS / "real_calls_transcripts.md").write_text("\n".join(sections), encoding="utf-8")
    print(f"\nwrote {RESULTS / 'real_calls_transcripts.md'}")


if __name__ == "__main__":
    asyncio.run(main())

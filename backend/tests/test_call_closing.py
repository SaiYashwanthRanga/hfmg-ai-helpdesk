"""A real provider call: at "Is there anything else?" the caller said "That's it. Thank you." It was taken for a new
problem ("Is that about the same problem, or a new one?"); "No, that's all. Thank you." then started a new issue, and
the same printer was filed twice. A closing line is a no, at the question and at the same-or-new question.
"""

import pytest
from sqlalchemy import func, select

from app.db.models import Ticket, VoiceCallState
from app.voice import nlu, scripts
from tests.test_intelligent_conversation import _describe, _file, _production_flow, _text, make_call  # noqa: F401
from tests.test_multi_issue_calls import _session
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _tickets(db_session) -> int:
    return (await db_session.execute(select(func.count()).select_from(Ticket))).scalar_one()


# --- 1. "That's it" at "Is there anything else?" -----------------------------------------------------------------


@pytest.mark.parametrize("said", [
    "That's it. Thank you.", "That's all, thanks.", "Nothing else.", "I'm good, thank you.", "That's everything.",
    "No, that's all. Thank you.", "I'm all set.",
])
async def test_a_closing_line_ends_the_call(said, make_call, db_session, sent_emails):  # noqa: F811
    call_id = "close-" + "".join(ch for ch in said.lower() if ch.isalpha())[:20]
    call = make_call(call_id)
    await _describe(call, "My keyboard stopped working after I spilled water on it, I have a spare.", short="your keyboard")
    await _file(call)
    before = await _tickets(db_session)

    out = await call.turn(said)

    assert _text(out) == scripts.GOODBYE and out["expect_reply"] is False
    assert (await _session(db_session, call_id)).state == VoiceCallState.COMPLETED
    assert await _tickets(db_session) == before


async def test_no_at_the_same_or_new_question_ends_the_call_without_a_second_ticket(make_call, db_session, sent_emails):  # noqa: F811
    call = make_call("close-same-or-new")
    await _describe(call, "My keyboard stopped working after I spilled water on it, I have a spare.", short="your keyboard")
    await _file(call)
    asked = await call.turn("Hmm, I was going to ask about something.")
    assert _text(asked) == scripts.SAME_OR_NEW_ASK
    before = await _tickets(db_session)

    out = await call.turn("No, that's all. Thank you.")

    assert _text(out) == scripts.GOODBYE and out["expect_reply"] is False
    assert await _tickets(db_session) == before


async def test_a_new_problem_after_a_closing_word_is_still_heard(make_call, db_session, sent_emails):  # noqa: F811
    call = make_call("close-but")
    await _describe(call, "My keyboard stopped working after I spilled water on it, I have a spare.", short="your keyboard")
    await _file(call)

    out = await call.turn("That's it, but my monitor also flickers.")

    assert _text(out) == scripts.SAME_OR_NEW_ASK


@pytest.mark.parametrize("said, done", [
    ("That's it. Thank you.", True), ("No, that's all.", True), ("Nothing else.", True), ("I'm good.", True),
    ("That's it, but my monitor flickers.", False), ("That's all, can I talk to a person?", False),
    ("My printer is also jammed.", False),
])
async def test_nothing_more(said, done):
    assert nlu.nothing_more(said) is done

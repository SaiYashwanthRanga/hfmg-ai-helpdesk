"""Voice agent state machine tests.

The NLU layer is mocked throughout, so these run without a Twilio account or
an LLM API key and assert conversation logic rather than model quality.
"""

import pytest
from sqlalchemy import select

from app.db.models import (
    Category,
    EscalationReason,
    Priority,
    Ticket,
    TicketSource,
    VoiceCallState,
)
from app.voice import nlu, orchestrator
from app.voice.session import get_or_create_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

CALLER_ID = "+18455550142"


def described(**overrides):
    result = nlu.TurnResult(
        value="Cannot log into eClinicalWorks, whole office affected.",
        confidence="high",
        unable_to_determine=False,
        category="eClinicalWorks",
        priority=Priority.HIGH,
        impact="multiple people",
        extras={"short_issue": "eClinicalWorks"},
    )
    for key, value in overrides.items():
        setattr(result, key, value)
    return result


def extracted(value: str, **overrides):
    result = nlu.TurnResult(value=value, confidence="high", unable_to_determine=False)
    for key, value_ in overrides.items():
        setattr(result, key, value_)
    return result


def yes_no(answer: bool):
    return nlu.TurnResult(yes_no=answer, unable_to_determine=False)


def failure():
    return nlu.TurnResult()


async def _session(db, *, from_number=CALLER_ID, call_sid="CA-test-1"):
    session = await get_or_create_session(
        db, call_sid=call_sid, from_number=from_number, to_number="+18455559999"
    )
    await orchestrator.start_call(session)
    await db.commit()
    return session


async def _seed_categories(db):
    for name in ("eClinicalWorks", "Other"):
        exists = (await db.execute(select(Category).where(Category.name == name))).scalar_one_or_none()
        if exists is None:
            db.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db.commit()


async def test_happy_path_skips_phone_question_when_caller_id_present(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await _session(db_session)

    monkeypatch.setattr(nlu, "interpret_description", lambda u: _async(described()))
    out = await orchestrator.handle_turn(db_session, session, utterance="eCW won't load")
    assert session.state == VoiceCallState.COLLECT_NAME
    assert "May I have your name" in out.twiml

    # Caller ID was usable, so phone is never asked -- straight to email.
    monkeypatch.setattr(nlu, "interpret_name", lambda u: _async(extracted("Maria Lopez")))
    out = await orchestrator.handle_turn(db_session, session, utterance="Maria Lopez")
    assert session.state == VoiceCallState.COLLECT_EMAIL
    assert "email address" in out.twiml
    assert session.collected["phone_number"] == CALLER_ID

    monkeypatch.setattr(nlu, "interpret_email", lambda u: _async(extracted("mlopez@hfmg.net")))
    await orchestrator.handle_turn(db_session, session, utterance="m lopez at hfmg dot net")
    assert session.state == VoiceCallState.CONFIRM_EMAIL

    monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(yes_no(True)))
    out = await orchestrator.handle_turn(db_session, session, utterance="yes")
    await db_session.commit()

    assert session.state == VoiceCallState.ANYTHING_ELSE
    assert session.ticket_id is not None

    ticket = await db_session.get(Ticket, session.ticket_id)
    assert ticket.source == TicketSource.PHONE
    assert ticket.caller_name == "Maria Lopez"
    assert ticket.phone_number == CALLER_ID
    assert ticket.email == "mlopez@hfmg.net"
    assert ticket.priority == Priority.HIGH
    assert "Call transcript" in ticket.description
    # Ticket number is read back spelled out, not as a cardinal number.
    assert "H F M G" in out.twiml


async def test_phone_is_asked_when_caller_id_unavailable(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await _session(db_session, from_number="anonymous", call_sid="CA-test-anon")

    monkeypatch.setattr(nlu, "interpret_description", lambda u: _async(described()))
    await orchestrator.handle_turn(db_session, session, utterance="eCW won't load")

    monkeypatch.setattr(nlu, "interpret_name", lambda u: _async(extracted("Maria Lopez")))
    out = await orchestrator.handle_turn(db_session, session, utterance="Maria Lopez")

    assert session.state == VoiceCallState.COLLECT_PHONE
    assert "phone number" in out.twiml

    monkeypatch.setattr(nlu, "interpret_phone", lambda u: _async(extracted("+18455550143")))
    await orchestrator.handle_turn(db_session, session, utterance="845 555 0143")
    assert session.state == VoiceCallState.COLLECT_EMAIL
    assert session.collected["phone_number"] == "+18455550143"


async def test_caller_requesting_human_escalates_immediately(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await _session(db_session, call_sid="CA-test-human")

    monkeypatch.setattr(
        nlu, "interpret_description", lambda u: _async(described(escalation_requested=True))
    )
    out = await orchestrator.handle_turn(db_session, session, utterance="can I talk to a real person")
    await db_session.commit()

    assert session.state == VoiceCallState.ESCALATED
    assert session.escalation_reason == EscalationReason.CALLER_REQUESTED
    assert session.ticket_id is not None

    ticket = await db_session.get(Ticket, session.ticket_id)
    assert "CALLBACK REQUESTED" in ticket.description
    # Escalated callers never sit in a normal-priority queue.
    assert ticket.priority in (Priority.HIGH, Priority.URGENT)
    assert "call you back" in out.twiml
    assert "<Hangup" in out.twiml


async def test_three_failures_escalate(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await _session(db_session, call_sid="CA-test-fail")

    monkeypatch.setattr(nlu, "interpret_description", lambda u: _async(failure()))

    out1 = await orchestrator.handle_turn(db_session, session, utterance="mumble")
    assert session.misunderstanding_count == 1
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION

    out2 = await orchestrator.handle_turn(db_session, session, utterance="mumble")
    assert session.misunderstanding_count == 2
    # Re-prompts must be reworded, never repeated verbatim.
    assert out1.twiml != out2.twiml

    await orchestrator.handle_turn(db_session, session, utterance="mumble")
    await db_session.commit()

    assert session.state == VoiceCallState.ESCALATED
    assert session.escalation_reason == EscalationReason.REPEATED_MISUNDERSTANDING
    assert session.ticket_id is not None


async def test_silence_counts_as_failure(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await _session(db_session, call_sid="CA-test-silence")

    await orchestrator.handle_turn(db_session, session, utterance="")
    assert session.misunderstanding_count == 1


async def test_email_failures_do_not_escalate_and_skip_after_two_tries(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await _session(db_session, call_sid="CA-test-email")

    monkeypatch.setattr(nlu, "interpret_description", lambda u: _async(described()))
    await orchestrator.handle_turn(db_session, session, utterance="eCW down")
    monkeypatch.setattr(nlu, "interpret_name", lambda u: _async(extracted("Maria Lopez")))
    await orchestrator.handle_turn(db_session, session, utterance="Maria Lopez")

    monkeypatch.setattr(nlu, "interpret_email", lambda u: _async(failure()))
    await orchestrator.handle_turn(db_session, session, utterance="gibberish")
    assert session.state == VoiceCallState.COLLECT_EMAIL
    assert session.misunderstanding_count == 0  # optional field, never counts

    await orchestrator.handle_turn(db_session, session, utterance="gibberish again")
    await db_session.commit()

    # Gave up on email and completed the ticket anyway.
    assert session.misunderstanding_count == 0
    assert session.ticket_id is not None
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert ticket.email is None


async def test_low_confidence_category_triggers_confirmation(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await _session(db_session, call_sid="CA-test-cat")

    monkeypatch.setattr(
        nlu, "interpret_description", lambda u: _async(described(confidence="low"))
    )
    await orchestrator.handle_turn(db_session, session, utterance="something is broken")
    monkeypatch.setattr(nlu, "interpret_name", lambda u: _async(extracted("Maria Lopez")))
    await orchestrator.handle_turn(db_session, session, utterance="Maria Lopez")
    monkeypatch.setattr(nlu, "interpret_email", lambda u: _async(extracted("mlopez@hfmg.net")))
    await orchestrator.handle_turn(db_session, session, utterance="email")
    monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(yes_no(True)))
    out = await orchestrator.handle_turn(db_session, session, utterance="yes")

    assert session.state == VoiceCallState.CONFIRM_CATEGORY
    assert "is this about" in out.twiml.lower()

    # Saying no falls back to Other rather than starting a guessing game.
    monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(yes_no(False)))
    await orchestrator.handle_turn(db_session, session, utterance="no")
    await db_session.commit()

    ticket = await db_session.get(Ticket, session.ticket_id)
    category = await db_session.get(Category, ticket.category_id)
    assert category.name == "Other"


async def test_ticket_creation_is_idempotent_on_replay(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await _session(db_session, call_sid="CA-test-replay")

    monkeypatch.setattr(nlu, "interpret_description", lambda u: _async(described()))
    await orchestrator.handle_turn(db_session, session, utterance="eCW down")
    monkeypatch.setattr(nlu, "interpret_name", lambda u: _async(extracted("Maria Lopez")))
    await orchestrator.handle_turn(db_session, session, utterance="Maria Lopez")
    monkeypatch.setattr(nlu, "interpret_email", lambda u: _async(nlu.TurnResult(
        unable_to_determine=False, extras={"declined": True}
    )))
    await orchestrator.handle_turn(db_session, session, utterance="skip")
    await db_session.commit()

    first_ticket_id = session.ticket_id
    assert first_ticket_id is not None

    before = (await db_session.execute(select(Ticket))).scalars().all()

    # Replay the same completed turn, as a Twilio retry would.
    await orchestrator._create_ticket_and_read_back(db_session, session)
    await db_session.commit()

    after = (await db_session.execute(select(Ticket))).scalars().all()
    assert session.ticket_id == first_ticket_id
    assert len(after) == len(before)


async def test_anything_else_starts_second_ticket_keeping_contact_details(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await _session(db_session, call_sid="CA-test-second")
    session.state = VoiceCallState.ANYTHING_ELSE
    session.collected = {
        "caller_name": "Maria Lopez",
        "phone_number": CALLER_ID,
        "email": "mlopez@hfmg.net",
        "description": "first issue",
    }

    monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(yes_no(True)))
    await orchestrator.handle_turn(db_session, session, utterance="yes actually")

    assert session.state == VoiceCallState.COLLECT_DESCRIPTION
    assert session.collected["caller_name"] == "Maria Lopez"
    assert session.collected.get("description") is None


async def test_keyword_escalation_works_when_nlu_is_down(db_session, monkeypatch):
    """If the LLM is unreachable, an explicit request for a human still works."""
    await _seed_categories(db_session)
    session = await _session(db_session, call_sid="CA-test-nlu-down")

    monkeypatch.setattr(nlu, "interpret_description", lambda u: _async(failure()))
    await orchestrator.handle_turn(db_session, session, utterance="just get me a real person")
    await db_session.commit()

    assert session.state == VoiceCallState.ESCALATED
    assert session.escalation_reason == EscalationReason.CALLER_REQUESTED


def _async(value):
    async def _coro():
        return value

    return _coro()

"""Voice-first understanding: deterministic fast paths, email repair,
volunteered details, no re-asking, priority floor, recognition hints.

Cases come from real failures: the evaluation corpus in backend/eval and a
tester's call ("Yes, please." read as unclear; "RAGGA.SAIYASHWANACH.HFMT.NET").
"""

import pytest
from sqlalchemy import select

from app.core import trace
from app.db.models import Category, Priority, Ticket, VoiceCallState
from app.speech import context
from app.voice import nlu, orchestrator, scripts
from app.voice.session import get_or_create_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

CALLER_ID = "+18455550142"


# --- fast paths ---------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("Yes, please.", True),
        ("Yeah, that's right.", True),
        ("Yes, correct.", True),
        ("Yep.", True),
        ("Sure, yes.", True),
        ("Yes, I think so.", True),
        ("Yes, is correct.", True),
        ("That's right.", True),
        ("No.", False),
        ("Nope, thanks.", False),
        ("No, that's all, thank you.", False),
        ("No, that's everything.", False),
        ("That's not right.", False),
        ("Hmm, I'm not sure.", None),
        ("Yes, but can I talk to a real person?", None),
        ("", None),
        ("Actually yes, my printer is also broken and the scanner too since this morning.", None),
    ],
)
async def test_quick_yes_no(utterance, expected):
    assert nlu.quick_yes_no(utterance) is expected


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("Eight four five, five five five, zero one four two.", "+18455550142"),
        ("Eight four five five five five zero three three three.", "+18455550333"),  # fast talker, no pauses
        ("It's 845-555-0199.", "+18455550199"),
        ("Um, eight four five... five five five... zero two three one.", "+18455550231"),
        ("eight four five, double five five, oh one four two", "+18455550142"),
        ("eight forty-five, five fifty-five, oh one four two", None),  # the model assembles these
        ("I don't know", None),
    ],
)
async def test_quick_phone(utterance, expected):
    assert nlu.quick_phone(utterance) == expected


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("It's m lopez at h f m g dot net.", "mlopez@hfmg.net"),
        ("Uh, p shah, at, h f m g, dot net.", "pshah@hfmg.net"),
        ("Suresh dot kumar at h f m g dot net.", "suresh.kumar@hfmg.net"),
        ("ranga.saieshwar@hfmg.net", "ranga.saieshwar@hfmg.net"),
        # A real tester's call: '@' dropped and one letter of the domain misheard.
        ("RAGGA.SAIYASHWANACH.HFMT.NET", "ragga.saiyashwanach@hfmg.net"),
        ("jsmith@hfmt.net", "jsmith@hfmg.net"),
        # Other domains are never "corrected".
        ("jsmith@gmail.com", "jsmith@gmail.com"),
        ("jsmith at hfm dot com", "jsmith@hfm.com"),
        # Spelled corrections go to the model.
        ("d nguyen, that's n g u y e n, at h f m g dot net.", None),
        # Exact recognizer outputs from the evaluation corpus:
        ("D Nguyen, that's N-G-U-Y-E-N, at HFMG.net.", None),  # was "dnguyenthat'sn-g-u-y-e-n@hfmg.net"
        ("is mlopez@hfmg.net.", "mlopez@hfmg.net"),  # "It'" dropped; was "ismlopez@hfmg.net"
        ("N-G-U-Y-E-N at HFMG.net.", "nguyen@hfmg.net"),
        ("ahcshopathfmg.net", None),  # garbage stays garbage
    ],
)
async def test_quick_email(utterance, expected):
    assert nlu.quick_email(utterance) == expected


async def test_normalize_email_rejects_leftover_words():
    assert nlu.normalize_email("dnguyenthat'snguyen@hfmg.net") is None
    assert nlu.normalize_email("m lopez at hfmg dot net") == "mlopez@hfmg.net"


@pytest.mark.parametrize("utterance", ["Skip.", "I'd rather not, skip that.", "No email please"])
async def test_email_declined(utterance):
    assert nlu.email_declined(utterance)
    assert not nlu.email_declined("m lopez at hfmg dot net")


async def test_fast_paths_make_no_model_call_and_are_traced(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(nlu, "_call_structured", boom)
    with trace.collect() as tr:
        assert (await nlu.interpret_yes_no("Is that correct?", "Yes, please.")).yes_no is True
        assert (await nlu.interpret_phone("eight four five five five five zero one four two")).value == "+18455550142"
        assert (await nlu.interpret_email("It's m lopez at h f m g dot net.")).value == "mlopez@hfmg.net"
        assert (await nlu.interpret_email("Skip.")).extras["declined"] is True
    assert [c.kind for c in tr.llm_calls] == ["rule"] * 4


async def test_ambiguous_answers_still_reach_the_model(monkeypatch):
    calls = []

    async def fake(system, user, name, properties):
        calls.append(name)
        return {"escalation_requested": False, "answer": True, "unable_to_determine": False}

    monkeypatch.setattr(nlu, "_call_structured", fake)
    result = await nlu.interpret_yes_no("Is that correct?", "Hmm, I think that's mostly it, but not sure about the dot")
    assert result.yes_no is True
    assert calls == ["record_answer"]


async def test_escalation_is_never_short_circuited(monkeypatch):
    async def fake(system, user, name, properties):
        return {"escalation_requested": True, "answer": None, "unable_to_determine": True}

    monkeypatch.setattr(nlu, "_call_structured", fake)
    result = await nlu.interpret_yes_no("Is that correct?", "No, get me a real person")
    assert result.escalation_requested


# --- regressions from real test calls ------------------------------------------------


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("Hi.", True),
        ("Hello, is this the help desk?", True),
        ("Good morning.", True),
        ("Hi, my printer is broken.", False),
        ("My laptop is having some problem.", False),
        ("", False),
    ],
)
async def test_is_greeting(utterance, expected):
    assert nlu.is_greeting(utterance) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("X, Y, A, S, H, W, A and T, H.", "Xyashwath"),
        ("Y-A-S-H-W-A-N-D-H", "Yashwandh"),
        ("Maria Lopez", "Maria Lopez"),
        ("J. R. Smith", "J. R. Smith"),  # initials + surname are a name, not a spelling
        (None, None),
    ],
)
async def test_join_spelled_name(raw, expected):
    assert nlu.join_spelled_name(raw) == expected


@pytest.mark.parametrize(
    "utterance", ["I don't want any email updates.", "I don't want any email address updates.", "Let us skip the email address altogether."]
)
async def test_real_email_declines(utterance):
    assert nlu.email_declined(utterance)


async def test_real_call_answers():
    assert nlu.quick_yes_no("No, it's not correct.") is False
    assert nlu.quick_yes_no("Yes.") is True
    assert nlu.quick_email("My email address is raga.saiyaashwandh at hfmc.net.") == "raga.saiyaashwandh@hfmg.net"


async def test_greeting_invites_the_problem_without_counting_a_failure(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-hi")

    async def boom(*args, **kwargs):
        raise AssertionError("a greeting needs no model call")

    monkeypatch.setattr(nlu, "interpret_description", boom)
    out = await orchestrator.handle_turn(db_session, session, utterance="Hi.")
    assert session.state == VoiceCallState.COLLECT_DESCRIPTION
    assert session.misunderstanding_count == 0
    assert scripts.DESCRIPTION_ASK in out.text
    assert "Greeting" not in out.text


async def test_callback_request_keeps_the_problem_and_asks_for_a_number(db_session, monkeypatch):
    """Real call: 'My Outlook password has been reset... I need IT experts to call back me.'"""
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-callback", from_number="anonymous")
    described = _described()
    described.escalation_requested = True
    described.value = "Outlook password was reset and the caller can't reset it."
    monkeypatch.setattr(nlu, "interpret_description", _async(described))
    out = await orchestrator.handle_turn(db_session, session, utterance="...I need IT experts to call back me.")

    # No caller ID: ask for a number before promising a callback.
    assert session.state == VoiceCallState.COLLECT_PHONE
    assert scripts.ESCALATION_PHONE_ASK in out.text
    assert session.collected["description"].startswith("Outlook password")

    out = await orchestrator.handle_turn(db_session, session, utterance="two one four, eight eight five, nine zero eight nine")
    await db_session.commit()
    assert session.state == VoiceCallState.ESCALATED
    assert "call you back at 2 1 4" in out.text
    assert "trouble understanding" not in out.text
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert ticket.phone_number == "+12148859089"
    assert "Outlook password was reset" in ticket.description


async def test_callback_request_without_a_usable_number_still_escalates_politely(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-callback-2", from_number="anonymous")
    monkeypatch.setattr(nlu, "interpret_description", _async(nlu.TurnResult(escalation_requested=True)))
    await orchestrator.handle_turn(db_session, session, utterance="get me a real person")
    monkeypatch.setattr(nlu, "interpret_phone", _async(nlu.TurnResult()))
    out = await orchestrator.handle_turn(db_session, session, utterance="mumble")
    assert session.state == VoiceCallState.ESCALATED
    assert session.misunderstanding_count == 0  # asked once, never retried
    assert scripts.ESCALATION_CALLER_REQUESTED_NO_NUMBER in out.text
    assert "trouble understanding" not in out.text


async def test_caller_id_means_no_number_question_on_escalation(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-callback-3")  # caller ID present
    monkeypatch.setattr(nlu, "interpret_description", _async(nlu.TurnResult(escalation_requested=True)))
    out = await orchestrator.handle_turn(db_session, session, utterance="real person please")
    assert session.state == VoiceCallState.ESCALATED
    assert "call you back at 8 4 5" in out.text


async def test_other_category_is_never_confirmed(db_session, monkeypatch):
    """Real call: 'Just to make sure I route this correctly, is this about Other?'"""
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-other")
    vague = _described(started="today", work_blocked=False)
    vague.category, vague.confidence = "Other", "low"
    monkeypatch.setattr(nlu, "interpret_description", _async(vague))
    await orchestrator.handle_turn(db_session, session, utterance="my laptop is having some problem")
    monkeypatch.setattr(nlu, "interpret_name", _async(nlu.TurnResult(value="Yash", unable_to_determine=False, extras={"department": "IT"})))
    await orchestrator.handle_turn(db_session, session, utterance="Yash, IT")
    out = await orchestrator.handle_turn(db_session, session, utterance="I don't want any email updates.")
    assert session.state == VoiceCallState.ANYTHING_ELSE
    assert "is this about" not in out.text.lower()
    assert "Your ticket number is" in out.text


# --- hedged model calls ------------------------------------------------------------


class _ScriptedProvider:
    """Each call takes the next (delay, result) from the script."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def structured(self, **kwargs):
        import asyncio

        delay, result = self.script[self.calls]
        self.calls += 1
        await asyncio.sleep(delay)
        return result


async def test_fast_answer_sends_no_duplicate(monkeypatch):
    provider = _ScriptedProvider([(0.0, {"name": "Maria", "department": None, "escalation_requested": False, "unable_to_determine": False})])
    monkeypatch.setattr(nlu, "get_provider", lambda: provider)
    monkeypatch.setattr(nlu.settings, "voice_nlu_hedge_after_seconds", 0.2)
    result = await nlu.interpret_name("Maria")
    assert result.value == "Maria"
    assert provider.calls == 1


async def test_stalled_call_is_beaten_by_the_hedged_duplicate(monkeypatch):
    import time as _time

    provider = _ScriptedProvider([
        (5.0, {"name": "Slow", "department": None, "escalation_requested": False, "unable_to_determine": False}),
        (0.0, {"name": "Maria", "department": None, "escalation_requested": False, "unable_to_determine": False}),
    ])
    monkeypatch.setattr(nlu, "get_provider", lambda: provider)
    monkeypatch.setattr(nlu.settings, "voice_nlu_hedge_after_seconds", 0.05)
    started = _time.perf_counter()
    result = await nlu.interpret_name("Maria")
    assert result.value == "Maria"
    assert provider.calls == 2
    assert _time.perf_counter() - started < 1.0  # did not wait for the stalled call


async def test_hedge_falls_through_to_the_other_attempt_when_one_fails(monkeypatch):
    provider = _ScriptedProvider([
        (0.1, None),  # first attempt eventually fails (timeout / error)
        (0.3, {"name": "Maria", "department": None, "escalation_requested": False, "unable_to_determine": False}),
    ])
    monkeypatch.setattr(nlu, "get_provider", lambda: provider)
    monkeypatch.setattr(nlu.settings, "voice_nlu_hedge_after_seconds", 0.05)
    assert (await nlu.interpret_name("Maria")).value == "Maria"


# --- recognition context ---------------------------------------------------------


async def test_transcription_prompt_is_state_aware():
    prompt = context.transcription_prompt("COLLECT_EMAIL")
    assert "hfmg.net" in prompt and "email" in prompt.lower()
    assert "eClinicalWorks" in context.transcription_prompt(None)
    assert "ten-digit" in context.transcription_prompt("COLLECT_PHONE")


# --- conversation flow ------------------------------------------------------------


def _async(value):
    async def _coro(*args, **kwargs):
        return value

    return _coro


async def _seed(db):
    for name in ("eClinicalWorks", "Microsoft 365", "Password", "Other"):
        exists = (await db.execute(select(Category).where(Category.name == name))).scalar_one_or_none()
        if exists is None:
            db.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db.commit()


async def _session(db, *, call_sid, from_number=CALLER_ID):
    session = await get_or_create_session(db, call_sid=call_sid, from_number=from_number, to_number="+18455559999")
    await orchestrator.start_call(session)
    await db.commit()
    return session


def _described(**extras):
    return nlu.TurnResult(
        value="Outlook won't open.",
        confidence="high",
        unable_to_determine=False,
        category="Microsoft 365",
        priority=Priority.MEDIUM,
        extras={"short_issue": "Outlook", **extras},
    )


async def test_details_are_asked_once_when_not_volunteered(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-details")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described()))
    out = await orchestrator.handle_turn(db_session, session, utterance="outlook won't open")

    assert session.state == VoiceCallState.COLLECT_DETAILS
    assert any(o in out.text for o in scripts.DETAILS_ASK_OPTIONS)
    assert "Outlook" in out.text and "having an issue" not in out.text

    monkeypatch.setattr(
        nlu, "interpret_details",
        _async(nlu.TurnResult(value="this morning", unable_to_determine=False, extras={"started": "this morning", "work_blocked": True})),
    )
    out = await orchestrator.handle_turn(db_session, session, utterance="this morning, and I can't work at all")
    assert session.collected["started"] == "this morning"
    assert session.collected["work_blocked"] is True
    # Can't work at all -> at least High, even though the model said Medium.
    assert session.collected["priority"] == Priority.HIGH.value
    assert session.state == VoiceCallState.COLLECT_NAME
    assert any(o in out.text for o in scripts.NAME_ASK_OPTIONS)


async def test_single_user_who_can_work_is_capped_at_medium(db_session, monkeypatch):
    """Eval case: 'Outlook won't open' rated High, then 'I can still work'."""
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-ceiling")
    high = _described(affected_scope="one_person")
    high.priority = Priority.HIGH
    monkeypatch.setattr(nlu, "interpret_description", _async(high))
    await orchestrator.handle_turn(db_session, session, utterance="outlook won't open")
    assert session.collected["priority"] == Priority.HIGH.value  # nothing known about work yet

    monkeypatch.setattr(
        nlu, "interpret_details",
        _async(nlu.TurnResult(value="this morning", unable_to_determine=False, extras={"started": "this morning", "work_blocked": False})),
    )
    await orchestrator.handle_turn(db_session, session, utterance="this morning, I can still work on my phone")
    assert session.collected["priority"] == Priority.MEDIUM.value


async def test_site_wide_problem_is_never_capped(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-site")
    urgent = _described(affected_scope="whole_site", started="8:30", work_blocked=False)
    urgent.priority = Priority.URGENT
    monkeypatch.setattr(nlu, "interpret_description", _async(urgent))
    await orchestrator.handle_turn(db_session, session, utterance="internet is down for the whole office, I'm on my phone")
    assert session.collected["priority"] == Priority.URGENT.value


async def test_unanswered_details_never_block_the_call(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-details-silent")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described()))
    await orchestrator.handle_turn(db_session, session, utterance="outlook won't open")
    assert session.state == VoiceCallState.COLLECT_DETAILS

    await orchestrator.handle_turn(db_session, session, utterance="")
    assert session.state == VoiceCallState.COLLECT_NAME
    assert session.misunderstanding_count == 0


async def test_volunteered_name_and_department_are_not_asked_again(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-volunteer")
    monkeypatch.setattr(
        nlu, "interpret_description",
        _async(_described(caller_name="James Carter", department="Front desk", started="8:30", work_blocked=True)),
    )
    out = await orchestrator.handle_turn(db_session, session, utterance="this is James from the front desk ...")
    # Everything volunteered and caller ID present: straight to email.
    assert session.state == VoiceCallState.COLLECT_EMAIL
    assert "Thanks, James" in out.text
    assert session.collected["department"] == "Front Desk"


async def test_department_is_asked_once_when_only_the_name_was_given(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-dept")
    monkeypatch.setattr(
        nlu, "interpret_description", _async(_described(caller_name="Maria Lopez", started="today", work_blocked=False))
    )
    out = await orchestrator.handle_turn(db_session, session, utterance="Maria here, outlook is broken")
    assert session.state == VoiceCallState.COLLECT_NAME
    assert "which department" in out.text

    monkeypatch.setattr(nlu, "interpret_name", _async(nlu.TurnResult(unable_to_determine=True)))
    await orchestrator.handle_turn(db_session, session, utterance="mumble")
    # Department is optional: an unclear answer moves on and is not retried.
    assert session.state == VoiceCallState.COLLECT_EMAIL
    assert session.misunderstanding_count == 0


async def test_name_and_department_in_one_answer(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-name-dept")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(started="today", work_blocked=False)))
    await orchestrator.handle_turn(db_session, session, utterance="outlook")
    monkeypatch.setattr(
        nlu, "interpret_name",
        _async(nlu.TurnResult(value="Maria Lopez", unable_to_determine=False, extras={"department": "Billing"})),
    )
    await orchestrator.handle_turn(db_session, session, utterance="Maria Lopez, billing")
    assert session.collected["caller_name"] == "Maria Lopez"
    assert session.collected["department"] == "Billing"
    assert session.state == VoiceCallState.COLLECT_EMAIL


async def test_intake_details_are_written_onto_the_ticket(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-intake")
    monkeypatch.setattr(
        nlu, "interpret_description",
        _async(_described(caller_name="James Carter", department="Front desk", started="8:30 today", work_blocked=True)),
    )
    await orchestrator.handle_turn(db_session, session, utterance="...")
    monkeypatch.setattr(nlu, "interpret_email", _async(nlu.TurnResult(unable_to_determine=False, extras={"declined": True})))
    await orchestrator.handle_turn(db_session, session, utterance="skip")
    await db_session.commit()

    ticket = await db_session.get(Ticket, session.ticket_id)
    assert "--- Intake details ---" in ticket.description
    assert "Department: Front Desk" in ticket.description
    assert "Started: 8:30 today" in ticket.description
    assert "Work blocked: yes" in ticket.description
    assert ticket.priority == Priority.HIGH
    # The problem stays first, where agents and the AI summary read it.
    assert ticket.description.startswith("Outlook won't open.")


async def test_second_issue_keeps_identity_and_asks_details_again(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, call_sid="CA-second-issue")
    session.state = VoiceCallState.ANYTHING_ELSE
    session.collected = {
        "caller_name": "Maria Lopez", "department": "Billing", "phone_number": CALLER_ID,
        "email": "mlopez@hfmg.net", "description": "first", "started": "today", "work_blocked": False,
        "asked": ["details", "department", "email"],
    }
    monkeypatch.setattr(nlu, "interpret_yes_no", _async(nlu.TurnResult(yes_no=True, unable_to_determine=False)))
    await orchestrator.handle_turn(db_session, session, utterance="yes, one more")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described()))
    await orchestrator.handle_turn(db_session, session, utterance="printer is broken")
    # New problem, new timing question -- but name, department, email are kept.
    assert session.state == VoiceCallState.COLLECT_DETAILS
    assert session.collected["department"] == "Billing"

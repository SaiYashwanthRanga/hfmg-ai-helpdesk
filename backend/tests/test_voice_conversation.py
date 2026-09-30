"""Conversation quality: priority reasoning, entity confidence, department
validation, the read-back before a ticket, corrections, phone/goodbye handling.

Fixtures are taken from real test calls (see docs/reviews/VOICE_CONVERSATION_REVIEW.md):
the Bluetooth call that announced "one user, minor issue ... high priority",
the name that was corrected but never read back, and the hotspot call that
escalated over a phone number.
"""

import itertools
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket, VoiceCallState
from app.voice import departments, names, nlu, orchestrator, scripts
from app.voice import priority as rules
from app.voice.session import get_or_create_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()


# --- priority: the reason can never contradict the level ---------------------------


async def test_the_real_contradiction_is_gone():
    """'Since this is affecting one user, minor issue, I'm marking it high priority.'"""
    a = rules.assess(model_priority=Priority.LOW, work_blocked=True, scope="one_person")
    assert a.priority == Priority.HIGH
    assert rules.notice(a) == "Since you can't work, I'll mark this high priority."
    assert a.impact == "One person, unable to work"
    assert "minor" not in (a.reason or "") + a.impact


@pytest.mark.parametrize(
    "kwargs,priority,rule",
    [
        (dict(model_priority=Priority.MEDIUM, work_blocked=True, scope="one_person"), Priority.HIGH, "caller_blocked"),
        (dict(model_priority=Priority.URGENT, work_blocked=True, scope="one_person"), Priority.HIGH, "caller_blocked"),
        (dict(model_priority=Priority.HIGH, work_blocked=False, scope="one_person"), Priority.MEDIUM, "caller_working"),
        (dict(model_priority=Priority.LOW, work_blocked=False, scope="one_person"), Priority.LOW, "caller_working"),
        (dict(model_priority=Priority.MEDIUM, work_blocked=True, scope="several_people"), Priority.HIGH, "team_blocked"),
        (dict(model_priority=Priority.URGENT, work_blocked=True, scope="several_people"), Priority.URGENT, "team_blocked"),
        (dict(model_priority=Priority.MEDIUM, work_blocked=None, scope="whole_site"), Priority.URGENT, "site_wide"),
        (dict(model_priority=Priority.LOW, work_blocked=True, scope="one_person", patient_care=True), Priority.URGENT, "patient_care"),
        (dict(model_priority=Priority.HIGH, work_blocked=None, scope=None), Priority.HIGH, "model_only"),
        (dict(model_priority=None, work_blocked=None, scope=None), Priority.MEDIUM, "model_only"),
    ],
)
async def test_priority_rules(kwargs, priority, rule):
    a = rules.assess(**kwargs)
    assert (a.priority, a.rule) == (priority, rule)


async def test_priority_and_reason_agree_for_every_combination():
    """Exhaustive: whatever the inputs, what is said follows from what was decided."""
    for model, blocked, scope, patient in itertools.product(
        [*Priority, None], [True, False, None], ["one_person", "several_people", "whole_site", None], [True, False, None]
    ):
        a = rules.assess(model_priority=model, work_blocked=blocked, scope=scope, patient_care=patient)
        said = rules.notice(a) or ""
        context = (model, blocked, scope, patient, a)
        # Never "minor"/"low" wording next to a high level, never invented facts.
        assert "minor" not in said and "minor" not in a.impact, context
        # "you can't work" only when the caller said so.
        assert ("can't work" not in (a.reason or "")) or blocked is True, context
        # A caller who can work, alone, is never above Medium.
        if blocked is False and scope in ("one_person", None) and not patient:
            assert a.priority in (Priority.LOW, Priority.MEDIUM), context
        # A caller who can't work, with no wider scope, is at least High and says why.
        if blocked is True and scope in ("one_person", None) and not patient:
            assert a.priority == Priority.HIGH and a.reason == "you can't work", context
        # Nothing announced below High; every announcement of a rule-driven level has a reason.
        if a.priority in (Priority.LOW, Priority.MEDIUM):
            assert said == "", context
        elif a.rule != "model_only":
            assert a.reason, context


# --- entity confidence ------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,kwargs,expected",
    [
        ("Maria Lopez", {}, "high"),
        ("Tom Reilly", {}, "low"),  # Reilly/Riley sound the same: worth a spelled read-back
        ("Yashvant", {}, "low"),  # real call
        ("Chindun Natakarani", {}, "low"),  # real call
        ("Yashwanth Ranga", {"spelled": True}, "high"),  # spelled letters are decoded exactly
        ("Maria Lopez", {"model_confidence": "low"}, "low"),
        ("Maria Lopez", {"stt_confidence": 0.6}, "low"),
        ("Maria Lopez", {"stt_confidence": 0.95}, "high"),
        ("", {}, "low"),
    ],
)
async def test_name_confidence(name, kwargs, expected):
    assert names.name_confidence(name, **kwargs) == expected


@pytest.mark.parametrize(
    "said,canonical,verified",
    [
        ("radiology", "Radiology", True),
        ("the front desk", "Front Desk", True),
        ("HR", "Human Resources", True),
        ("billin", "Billing", True),  # near miss
        ("IT department", "IT", True),
        ("lab", "Laboratory", True),
        ("AI Director", "AI Director", False),  # real call: not a department
        ("Development", "Development", False),  # real call
    ],
)
async def test_department_validation(said, canonical, verified):
    match = departments.canonicalize(said)
    assert (match.name, match.verified) == (canonical, verified)


async def test_department_list_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "voice_departments", "Development, Cardiology")
    assert departments.canonicalize("development").verified is True
    assert departments.canonicalize("radiology").verified is False


# --- helpers for conversations --------------------------------------------------------


def _async(value):
    async def _coro(*args, **kwargs):
        return value

    return _coro


@pytest.fixture
def conversation(monkeypatch):
    """The improved flow: doubtful names and the whole ticket are read back."""
    monkeypatch.setattr(settings, "voice_confirm_name", True)
    monkeypatch.setattr(settings, "voice_confirm_summary", True)


async def _seed(db):
    for name in ("Microsoft 365", "Network", "Other", "Password"):
        if (await db.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db.commit()


async def _call(db, *, caller_id="+12148859089"):
    await _seed(db)
    session = await get_or_create_session(
        db, call_sid=f"CA-{uuid.uuid4().hex[:8]}", from_number=caller_id, to_number="+18455559999"
    )
    await orchestrator.start_call(session)
    return session


def _described(monkeypatch, **extras):
    fields = {
        "short_issue": "your laptop's Bluetooth",
        "started": "this morning",
        "work_blocked": True,
        "affected_scope": "one_person",
        **extras,
    }
    result = nlu.TurnResult(
        value="The Bluetooth on the caller's laptop is not working.",
        confidence="high",
        unable_to_determine=False,
        category="Other",
        priority=Priority.LOW,  # the model called it minor; the caller said they can't work
        extras=fields,
    )
    monkeypatch.setattr(nlu, "interpret_description", _async(result))


def _named(monkeypatch, name, department, confidence=None):
    monkeypatch.setattr(
        nlu,
        "interpret_name",
        _async(nlu.TurnResult(value=name, unable_to_determine=False, extras={"department": department, "name_confidence": confidence})),
    )


async def _say(db, session, text, **kwargs):
    return await orchestrator.handle_turn(db, session, utterance=text, **kwargs)


def _heard(outcome) -> str:
    """What the caller hears, as text (the TwiML XML-escapes apostrophes)."""
    return outcome.text


async def _up_to_summary(db, monkeypatch, *, name="Yashwanth", department="Development", caller_id="+12148859089"):
    session = await _call(db, caller_id=caller_id)
    _described(monkeypatch)
    await _say(db, session, "The Bluetooth in my laptop is not working and I can't work.")
    _named(monkeypatch, name, department)
    await _say(db, session, f"My name is {name}, {department} department.")
    if session.state == VoiceCallState.CONFIRM_NAME:  # an unfamiliar name is read back spelled
        await _say(db, session, "Yes, that's right.")
    return session


# --- the read-back before creating a ticket ---------------------------------------------


async def test_ticket_is_read_back_before_it_is_created(db_session, monkeypatch, conversation):
    """The Bluetooth call: no summary, contradictory priority, name never re-checked."""
    session = await _up_to_summary(db_session, monkeypatch)
    out = await _say(db_session, session, "I don't want to add any email address right now.")

    assert session.state == VoiceCallState.CONFIRM_SUMMARY
    assert session.ticket_id is None  # nothing is filed until the caller agrees
    heard = _heard(out)
    for expected in (
        "Yashwanth",
        "You're in Development",
        "your laptop's Bluetooth",
        "It started this morning",
        "Since you can't work, I'll mark this high priority.",  # the reason is the caller's own
    ):
        assert expected in heard, expected
    assert "minor" not in heard.lower()

    out = await _say(db_session, session, "Yes.")
    await db_session.commit()
    assert session.state == VoiceCallState.ANYTHING_ELSE
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert ticket.priority == Priority.HIGH
    assert "Priority: High: you can't work" in ticket.description
    assert "Impact: One person, unable to work" in ticket.description
    assert "minor" not in ticket.description.lower().split("--- call transcript")[0]
    assert scripts.SUMMARY_FILING in _heard(out)


async def test_unlisted_department_is_flagged_on_the_ticket(db_session, monkeypatch, conversation):
    session = await _up_to_summary(db_session, monkeypatch, department="AI Director")
    await _say(db_session, session, "Skip.")
    await _say(db_session, session, "Yes.")
    await db_session.commit()
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert "Department: AI Director (unverified" in ticket.description


async def test_listed_department_is_normalized(db_session, monkeypatch, conversation):
    session = await _up_to_summary(db_session, monkeypatch, department="radiology")
    assert session.collected["department"] == "Radiology"
    assert session.collected["department_verified"] is True


async def test_bare_no_asks_what_to_change_then_applies_it(db_session, monkeypatch, conversation):
    session = await _up_to_summary(db_session, monkeypatch)
    await _say(db_session, session, "Skip.")
    out = await _say(db_session, session, "No.")
    assert scripts.SUMMARY_WHAT_TO_CHANGE in out.text
    assert session.ticket_id is None

    monkeypatch.setattr(
        nlu, "interpret_summary_correction",
        _async(nlu.TurnResult(unable_to_determine=False, extras={"changes": {"department": "IT"}, "nothing_to_change": False})),
    )
    out = await _say(db_session, session, "My department is IT.")
    assert session.collected["department"] == "IT"
    assert session.state == VoiceCallState.CONFIRM_SUMMARY  # read again, not filed
    assert "Okay, updated." in _heard(out) and "You're in IT" in _heard(out)

    await _say(db_session, session, "Yes.")
    await db_session.commit()
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert "Department: IT\n" in ticket.description


async def test_yes_but_is_a_correction_not_a_yes(db_session, monkeypatch, conversation):
    session = await _up_to_summary(db_session, monkeypatch)
    await _say(db_session, session, "Skip.")
    monkeypatch.setattr(
        nlu, "interpret_summary_correction",
        _async(nlu.TurnResult(unable_to_determine=False, extras={"changes": {"started": "yesterday"}, "nothing_to_change": False})),
    )
    await _say(db_session, session, "Yes, but it actually started yesterday.")
    assert session.ticket_id is None
    assert session.collected["started"] == "yesterday"
    assert session.state == VoiceCallState.CONFIRM_SUMMARY


async def test_corrected_name_is_read_back_before_the_ticket(db_session, monkeypatch, conversation):
    session = await _up_to_summary(db_session, monkeypatch)
    await _say(db_session, session, "Skip.")
    await _say(db_session, session, "No.")
    monkeypatch.setattr(
        nlu, "interpret_summary_correction",
        _async(nlu.TurnResult(unable_to_determine=False, extras={"changes": {"caller_name": "Yashwant"}, "nothing_to_change": False})),
    )
    out = await _say(db_session, session, "It's Yashwant, not Yashwanth.")
    assert session.state == VoiceCallState.CONFIRM_NAME  # unfamiliar name: spelled back first
    assert "Y A S H W A N T" in out.text


async def test_unresolved_disagreement_files_the_ticket_and_says_so(db_session, monkeypatch, conversation):
    """Bounded: two corrections, then it stops asking and notes it for the team."""
    session = await _up_to_summary(db_session, monkeypatch)
    await _say(db_session, session, "Skip.")
    monkeypatch.setattr(
        nlu, "interpret_summary_correction",
        _async(nlu.TurnResult(unable_to_determine=False, extras={"changes": {"started": "yesterday"}, "nothing_to_change": False})),
    )
    await _say(db_session, session, "It started yesterday.")
    await _say(db_session, session, "No, it started last week actually.")
    out = await _say(db_session, session, "No, that's still not right.")
    await db_session.commit()
    assert session.ticket_id is not None
    assert scripts.SUMMARY_LEAVE_AS_IS in out.text
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert "Read-back: caller said the summary was not fully correct" in ticket.description


async def test_silence_after_the_read_back_files_it_noted_unconfirmed(db_session, monkeypatch, conversation):
    session = await _up_to_summary(db_session, monkeypatch)
    await _say(db_session, session, "Skip.")
    await _say(db_session, session, "")
    await db_session.commit()
    assert session.ticket_id is not None
    assert session.misunderstanding_count == 0
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert "details unverified" in ticket.description


async def test_uncertain_category_is_mentioned_in_the_read_back_not_asked(db_session, monkeypatch, conversation):
    session = await _call(db_session)
    result = nlu.TurnResult(
        value="Wi-Fi keeps dropping.", confidence="low", unable_to_determine=False, category="Network",
        priority=Priority.MEDIUM, extras={"short_issue": "the Wi-Fi", "started": "today", "work_blocked": False, "affected_scope": "one_person"},
    )
    monkeypatch.setattr(nlu, "interpret_description", _async(result))
    await _say(db_session, session, "the wifi keeps dropping")
    _named(monkeypatch, "Maria Lopez", "Billing")
    await _say(db_session, session, "Maria Lopez, billing")
    out = await _say(db_session, session, "skip")
    assert session.state == VoiceCallState.CONFIRM_SUMMARY  # no separate "is this about Network?"
    assert "filing it under Network" in out.text
    assert "Since you can still work" in out.text


async def test_summary_wording_for_a_long_running_problem(db_session, monkeypatch, conversation):
    session = await _call(db_session)
    _described(monkeypatch, started="since Monday", work_blocked=False)
    await _say(db_session, session, "my outlook calendar hasn't synced since monday")
    _named(monkeypatch, "Maria Lopez", "HR")
    await _say(db_session, session, "Maria Lopez, HR")
    out = await _say(db_session, session, "skip")
    assert "It's been going on since Monday" in _heard(out)


# --- phone: say what was wrong, then carry on --------------------------------------------


async def test_a_spoken_number_is_read_back_in_the_summary_but_caller_id_is_not(db_session, monkeypatch, conversation):
    with_caller_id = await _up_to_summary(db_session, monkeypatch)
    out = await _say(db_session, with_caller_id, "skip")
    assert "reach you at" not in out.text

    session = await _up_to_summary(db_session, monkeypatch, caller_id="anonymous")
    assert session.state == VoiceCallState.COLLECT_PHONE
    await _say(db_session, session, "two one four, eight eight five, nine zero eight nine")
    out = await _say(db_session, session, "skip")
    assert "reach you at 2 1 4, 8 8 5, 9 0 8 9" in out.text


async def test_phone_retry_says_how_many_digits_were_heard(db_session, monkeypatch, conversation):
    """Real call: 'Number is like 11006' -> 'one digit at a time' -> '1-0-0-6' -> ..."""
    session = await _up_to_summary(db_session, monkeypatch, caller_id="anonymous")
    out = await _say(db_session, session, "Number is like 11006")
    assert "only caught 5 digits" in out.text
    assert session.misunderstanding_count == 0


async def test_phone_failure_never_escalates_and_never_blocks_the_ticket(db_session, monkeypatch, conversation):
    """Real call 2: escalated to a human over a phone number after everything else was understood."""
    session = await _up_to_summary(db_session, monkeypatch, caller_id="anonymous")
    await _say(db_session, session, "Number is like 11006")
    out = await _say(db_session, session, "866-882-085-444")  # twelve digits
    assert scripts.PHONE_GIVE_UP in out.text
    assert not session.escalated
    assert session.state == VoiceCallState.COLLECT_EMAIL  # carried on

    await _say(db_session, session, "skip")
    await _say(db_session, session, "Yes.")
    await db_session.commit()
    assert session.ticket_id is not None and not session.escalated
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert "Callback number: not captured" in ticket.description
    assert ticket.phone_number == "unknown"


async def test_silence_at_the_phone_question_is_not_a_misunderstanding(db_session, monkeypatch, conversation):
    session = await _up_to_summary(db_session, monkeypatch, caller_id="anonymous")
    await _say(db_session, session, "")
    assert session.misunderstanding_count == 0
    assert session.collected["phone_attempts"] == 1


# --- a caller who is leaving -------------------------------------------------------------------


async def test_goodbye_at_an_optional_question_files_an_ordinary_ticket(db_session, monkeypatch, conversation):
    """Real call: 'Thank you. Bye bye.' at the email question was treated as an email answer.

    Everything required had been collected, so this is a normal ticket at the
    priority that was assessed -- not an 'incomplete' one bumped to High (the
    replay of the real headset call filed a Low-priority problem as High).
    """
    session = await _up_to_summary(db_session, monkeypatch)
    assert session.state == VoiceCallState.COLLECT_EMAIL
    out = await _say(db_session, session, "Thank you. Bye bye.")
    await db_session.commit()
    assert session.state == VoiceCallState.COMPLETED
    assert out.hangup and "Your ticket number is" in _heard(out)
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert "INCOMPLETE" not in ticket.description
    assert "Read-back: not done: the caller ended the call first" in ticket.description
    assert ticket.priority == Priority.HIGH  # assessed: the caller said they can't work


async def test_a_leaving_caller_with_a_minor_problem_is_not_bumped_to_high(db_session, monkeypatch, conversation):
    session = await _call(db_session)
    _described(monkeypatch, work_blocked=False, short_issue="your headset")
    await _say(db_session, session, "My headset is not working.")
    _named(monkeypatch, "Maria Lopez", "Billing")
    await _say(db_session, session, "Maria Lopez, billing")
    assert session.state == VoiceCallState.COLLECT_EMAIL
    await _say(db_session, session, "Thank you. Bye bye.")
    await db_session.commit()
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert ticket.priority == Priority.LOW  # the model said Low; the caller can still work


async def test_leaving_before_giving_a_name_is_flagged_incomplete_but_not_bumped(db_session, monkeypatch, conversation):
    session = await _call(db_session)
    _described(monkeypatch, work_blocked=False, short_issue="your headset")
    await _say(db_session, session, "My headset is not working.")
    assert session.state == VoiceCallState.COLLECT_NAME
    session.collected = {**session.collected, "phone_number": "+12148859089"}
    await _say(db_session, session, "Actually never mind, goodbye.")
    await db_session.commit()
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert "INCOMPLETE VOICE INTAKE" in ticket.description
    assert ticket.priority == Priority.LOW


async def test_goodbye_before_a_problem_is_a_polite_goodbye(db_session, conversation):
    session = await _call(db_session)
    out = await _say(db_session, session, "Oh sorry, wrong number. Bye.")
    assert session.state == VoiceCallState.ABANDONED
    assert session.ticket_id is None
    assert scripts.LEAVING_NO_TICKET in out.text


async def test_goodbye_words_inside_a_real_description_are_not_goodbyes():
    assert not nlu.is_goodbye("My phone rings and then says goodbye and hangs up on every call I make from the front desk")
    assert nlu.is_goodbye("Thank you. Bye bye.")
    assert nlu.is_goodbye("ok goodbye")


# --- natural wording ----------------------------------------------------------------------------


async def test_acknowledgement_uses_the_callers_own_thing(db_session, monkeypatch, conversation):
    session = await _call(db_session)
    _described(monkeypatch, started=None, work_blocked=None)
    out = await _say(db_session, session, "The Bluetooth in my laptop is not working.")
    assert "your laptop's Bluetooth" in _heard(out)
    assert "having an issue" not in _heard(out) and "I understand" not in _heard(out)


async def test_no_acknowledgement_is_better_than_an_odd_one(db_session, monkeypatch, conversation):
    session = await _call(db_session)
    _described(monkeypatch, short_issue="", started=None, work_blocked=None)
    out = await _say(db_session, session, "something is wrong with my laptop")
    assert "Sorry to hear" not in out.text and "Got it" not in out.text


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("your laptop's Bluetooth", "your laptop's Bluetooth"),
        ("the front desk printer.", "the front desk printer"),
        ("Greeting", ""),  # real call: "an issue with Greeting"
        ("a problem", "a problem"),
        ("problem", ""),
        ("The caller's laptop Bluetooth is not working properly today", ""),  # a sentence, not a phrase
        ("call 8455550142 now", ""),
        (None, ""),
    ],
)
async def test_issue_phrase_is_validated(raw, expected):
    assert nlu.clean_issue_phrase(raw) == expected


async def test_wording_rotates_but_is_reproducible():
    assert scripts.pick(("a", "b", "c"), 4) == "b"
    assert scripts.pick(("a", "b", "c"), 4) == scripts.pick(("a", "b", "c"), 4)


@pytest.mark.parametrize(
    "utterance,expected",
    [
        ("Yes.", True), ("Yes, that's right.", True), ("No.", False), ("No, it's not correct.", False),
        ("Yes, but the department is IT.", None), ("Yes, actually it started yesterday", None),
        ("Yes that's right but change my name please", None),
    ],
)
async def test_strict_yes_no_treats_qualified_yes_as_a_correction(utterance, expected):
    assert nlu.quick_yes_no(utterance, strict=True) is expected


@pytest.mark.parametrize(
    "utterance,count",
    [("Number is like 11006", 5), ("1-0-0-6", 4), ("866-882-085-444", 12), ("double five", 2), ("no idea", 0),
     ("eight four five, five five five, zero one four two", 10)],
)
async def test_count_digits(utterance, count):
    assert nlu.count_digits(utterance) == count

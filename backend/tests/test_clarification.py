"""Clarification flow (strict extraction): ask about impact facts that were not stated.

Can you still work / is anyone else affected / is patient care blocked. At most two
asks per fact and two clarification turns per call, so the flow can never loop.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, VoiceCallState
from app.llm import fake_provider
from app.speech import context as speech_context
from app.voice import facts as facts_mod
from app.voice import nlu, orchestrator, scripts
from app.voice.facts import Fact, Source, UnknownReason
from app.voice.session import get_or_create_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
WB, PC, SC = facts_mod.WORK_BLOCKED, facts_mod.PATIENT_CARE, facts_mod.SCOPE


@pytest.fixture(autouse=True)
def strict_mode(monkeypatch):
    monkeypatch.setattr(settings, "voice_strict_extraction", True)
    monkeypatch.setattr(settings, "voice_max_clarification_turns", 2)


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setattr(fake_provider.settings, "fake_llm_latency_ms", 0)
    monkeypatch.setattr(nlu, "get_provider", lambda: fake_provider.FakeProvider())
    monkeypatch.setattr(settings, "voice_nlu_hedge_after_seconds", 0)


# --- the interpreter ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [("Yes", True), ("yes.", True), ("Yeah", True), ("Yes it is", True), ("No", False), ("Nope.", False),
     ("No, it isn't", False), ("Not really", False),
     # not bare: these answer a different half of the question, so the model decides
     ("Yes, I can still work", None), ("No, I can't work", None), ("Yes but only partly", None), ("", None),
     ("I think so", None)],
)
async def test_bare_yes_no_is_strict(text, expected):
    assert nlu.bare_yes_no(text) is expected


async def test_bare_answers_skip_the_model(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("the model must not be called for a bare yes/no")

    monkeypatch.setattr(nlu, "_call_structured", boom)
    assert (await nlu.interpret_clarification("blocked", "q", "Yes")).extras["answer"] == "yes"
    assert (await nlu.interpret_clarification("blocked", "q", "No")).extras["answer"] == "no"
    assert (await nlu.interpret_clarification("patient_care", "q", "yes")).extras["answer"] == "yes"
    assert (await nlu.interpret_clarification("scope", "q", "yes")).extras["answer"] == "others"
    assert (await nlu.interpret_clarification("scope", "q", "no")).extras["answer"] == "just_me"


@pytest.mark.parametrize(
    "kind,text,answer",
    [
        ("blocked", "Yes, I can still work, it's just slow.", "no"),     # long answers go to the model
        ("blocked", "It's stopping me completely.", "yes"),
        ("blocked", "I'm not sure.", "unsure"),
        ("blocked", "Sort of, sometimes.", "partly"),
        ("patient_care", "Yes, we can't check anyone in.", "yes"),
        ("patient_care", "No, we're using paper charts, it's not held up.", "no"),
        ("scope", "Just me, nobody else.", "just_me"),
        ("scope", "Yes, my whole team is affected.", "others"),
        ("scope", "The whole office is down.", "whole_site"),
        ("scope", "Hmm.", "unclear"),
    ],
)
async def test_interpreter_maps_replies_through_the_model(fake_model, kind, text, answer):
    result = await nlu.interpret_clarification(kind, scripts.CLARIFY_OPTIONS[kind][0], text)
    assert result.extras["answer"] == answer


async def test_clarification_prompt_carries_the_fixed_meaning_of_yes(monkeypatch):
    seen = {}

    async def capture(system, user, name, properties):
        seen.update(system=system, user=user, name=name, properties=properties)
        return {"escalation_requested": False, "answer": "yes", "unable_to_determine": False}

    monkeypatch.setattr(nlu, "_call_structured", capture)
    await nlu.interpret_clarification("blocked", scripts.CLARIFY_BLOCKED_OPTIONS[0], "It is stopping me from doing anything.")
    assert "yes means it IS stopping them" in seen["system"]
    assert "no means it is NOT stopping them" in seen["system"]
    assert "ignore anything from earlier in the call" in seen["system"]
    assert seen["properties"]["answer"]["enum"] == ["yes", "no", "partly", "unsure", "unclear"]
    assert seen["name"] == "record_clarification"


# --- which question is asked next -----------------------------------------------------------------


async def _seed(db):
    for name in ("Microsoft 365", "eClinicalWorks", "Network", "Other"):
        if (await db.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db.commit()


async def _session(db, call_sid, **collected):
    session = await get_or_create_session(db, call_sid=call_sid, from_number="+18455550142", to_number="+18455559999")
    await orchestrator.start_call(session)
    session.collected = {**session.collected, **collected}
    await db.commit()
    return session


def _async(value):
    async def _coro(*args, **kwargs):
        return value

    return _coro


def _described(category="Microsoft 365", facts=None, started="this morning", context=False):
    full = {f: (facts or {}).get(f, Fact.unknown(f)) for f in facts_mod.FIELDS}
    return nlu.TurnResult(
        value="Outlook won't open.", confidence="high", unable_to_determine=False, category=category,
        priority=Priority.MEDIUM,
        extras={"short_issue": "Outlook", "started": started, "facts": full, "patient_context_mentioned": context,
                **{f: full[f].value for f in facts_mod.FIELDS}},
    )


def _answer_extras(**values):
    facts = {f: Fact.unknown(f, source=Source.DETAILS) for f in (WB, SC)}
    facts.update(values)
    return nlu.TurnResult(
        value="x", unable_to_determine=False,
        extras={"started": "this morning", **{f: v.value for f, v in facts.items()}, "facts": facts},
    )


async def test_unknown_work_impact_asks_the_blocked_question_after_the_details_turn(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-1")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(started=None)))
    out = await orchestrator.handle_turn(db_session, session, utterance="outlook won't open")
    assert any(o in out.text for o in scripts.DETAILS_ASK_STRICT_OPTIONS)        # compound question first
    assert "pending_question" not in session.collected or not session.collected["pending_question"]
    assert session.collected["clarify_asks"] == {"blocked": 1}
    assert session.collected.get("clarify_turns", 0) == 0                         # it does not count toward the cap

    monkeypatch.setattr(nlu, "interpret_details", _async(_answer_extras()))      # still nothing explicit
    out = await orchestrator.handle_turn(db_session, session, utterance="since this morning")
    assert out.text == scripts.CLARIFY_BLOCKED_OPTIONS[1]                         # second ask uses different wording
    assert session.state == VoiceCallState.COLLECT_DETAILS
    assert session.collected["pending_question"] == "blocked"


async def test_yes_answer_records_a_trusted_fact_and_raises_priority(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-2")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(started="today")))
    out = await orchestrator.handle_turn(db_session, session, utterance="outlook won't open")
    assert scripts.CLARIFY_BLOCKED_OPTIONS[0] in out.text      # started is known: the clean question is asked directly
    assert session.collected["pending_question"] == "blocked"
    assert session.collected["priority"] == "MEDIUM"

    await orchestrator.handle_turn(db_session, session, utterance="Yes")
    record = session.collected["facts"][WB]
    assert record["state"] == "true" and record["source"] == "answer" and record["confidence"] >= 0.9
    assert record["evidence"] == "Yes"
    assert session.collected["priority"] == "HIGH" and session.collected["priority_rule"] == "caller_blocked"
    assert session.collected["pending_question"] == "scope"       # blocked: who else is affected can change the priority


async def test_no_answer_records_can_still_work(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-3")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(started="today")))
    await orchestrator.handle_turn(db_session, session, utterance="outlook is slow")
    await orchestrator.handle_turn(db_session, session, utterance="No")
    assert session.collected["facts"][WB]["state"] == "false"
    assert session.collected["priority"] == "MEDIUM" and session.collected["priority_rule"] == "caller_working"


async def test_not_sure_is_final_and_is_not_asked_again(db_session, monkeypatch, fake_model):
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-4")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(started="today")))
    await orchestrator.handle_turn(db_session, session, utterance="outlook is slow")
    await orchestrator.handle_turn(db_session, session, utterance="I'm not sure honestly")

    record = session.collected["facts"][WB]
    assert record["state"] == "unknown" and record["reason"] == UnknownReason.CALLER_UNSURE.value
    assert session.collected["clarify_asks"]["blocked"] == orchestrator.MAX_ASKS_PER_FACT
    assert not session.collected.get("pending_question")
    assert session.state != VoiceCallState.COLLECT_DETAILS or not session.collected.get("pending_question")


async def test_unclear_answer_is_asked_once_more_then_left_unknown(db_session, monkeypatch, fake_model):
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-5")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(started="today")))
    await orchestrator.handle_turn(db_session, session, utterance="outlook is slow")            # asks (1)
    out = await orchestrator.handle_turn(db_session, session, utterance="Hmm, well, it depends.")  # unclear -> asks (2)
    assert out.text == scripts.CLARIFY_BLOCKED_OPTIONS[1]
    out = await orchestrator.handle_turn(db_session, session, utterance="I guess, whatever.")   # unclear again -> moves on
    assert session.collected["clarify_asks"]["blocked"] == 2
    assert session.collected["work_blocked"] is None
    assert session.state != VoiceCallState.COLLECT_DETAILS or "work" not in out.text.lower()
    assert session.misunderstanding_count == 0                                                   # optional info: never escalates


async def test_the_whole_flow_cannot_loop(db_session, monkeypatch):
    """A caller who never answers anything usefully is asked a bounded number of questions."""
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-6")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(category="eClinicalWorks", started=None, context=True)))
    monkeypatch.setattr(nlu, "interpret_details", _async(_answer_extras()))
    monkeypatch.setattr(nlu, "interpret_clarification", _async(nlu.TurnResult(extras={"answer": "unclear"})))

    await orchestrator.handle_turn(db_session, session, utterance="ecw is slow, I use it for patient charts")
    clarifications = 0
    for _ in range(12):
        if session.state != VoiceCallState.COLLECT_DETAILS:
            break
        pending = session.collected.get("pending_question")
        clarifications += bool(pending)
        await orchestrator.handle_turn(db_session, session, utterance="uh, maybe")
    else:
        pytest.fail("the call is still asking clarification questions after 12 turns")

    assert clarifications <= settings.voice_max_clarification_turns + 1     # the cap, plus the retry of the compound question
    assert session.collected["clarify_turns"] <= settings.voice_max_clarification_turns
    assert all(n <= orchestrator.MAX_ASKS_PER_FACT for n in session.collected["clarify_asks"].values())


async def test_patient_context_triggers_the_patient_care_question(db_session, monkeypatch):
    await _seed(db_session)
    known_blocked = {WB: Fact.known(WB, False, evidence="I can still work", confidence=0.9)}
    session = await _session(db_session, "CA-clar-7")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(facts=known_blocked, started="today", context=True)))
    out = await orchestrator.handle_turn(db_session, session, utterance="slow laptop, I use it for patient charts, I can still work")
    assert scripts.CLARIFY_PATIENT_CARE_OPTIONS[0] in out.text
    assert session.collected["pending_question"] == "patient_care"

    await orchestrator.handle_turn(db_session, session, utterance="No")
    assert session.collected["facts"][PC]["state"] == "false"
    assert session.collected["priority"] != "URGENT"


async def test_patient_yes_makes_it_critical(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-8")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(category="eClinicalWorks", started="today", context=True)))
    await orchestrator.handle_turn(db_session, session, utterance="ecw is frozen")
    await orchestrator.handle_turn(db_session, session, utterance="Yes")                         # blocked
    assert session.collected["pending_question"] == "patient_care"
    await orchestrator.handle_turn(db_session, session, utterance="Yes")                         # patients held up
    assert session.collected["priority"] == "URGENT" and session.collected["priority_rule"] == "patient_care"


async def test_scope_is_asked_only_when_it_can_change_the_priority(db_session, monkeypatch):
    await _seed(db_session)
    can_work = {WB: Fact.known(WB, False, evidence="I can still work", confidence=0.9)}

    quiet = await _session(db_session, "CA-clar-9a")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(category="Microsoft 365", facts=can_work, started="today")))
    await orchestrator.handle_turn(db_session, quiet, utterance="outlook slow, I can still work")
    assert not quiet.collected.get("pending_question")                    # one person, unshared system: nothing to ask

    shared = await _session(db_session, "CA-clar-9b")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(category="Network", facts=can_work, started="today")))
    out = await orchestrator.handle_turn(db_session, shared, utterance="wifi is slow, I can still work")
    assert scripts.CLARIFY_SCOPE_OPTIONS[0] in out.text
    await orchestrator.handle_turn(db_session, shared, utterance="Yes")
    assert shared.collected["facts"][SC]["state"] == "several_people"


async def test_decided_priority_skips_the_work_question(db_session, monkeypatch):
    await _seed(db_session)
    whole_site = {SC: Fact.known(SC, "whole_site", evidence="the whole office", confidence=0.9)}
    session = await _session(db_session, "CA-clar-10")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(category="Network", facts=whole_site, started="today")))
    await orchestrator.handle_turn(db_session, session, utterance="the whole office is offline")
    assert session.collected["priority"] == "URGENT"
    assert not session.collected.get("pending_question")


async def test_asking_for_a_person_during_a_clarification_escalates(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-11")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(started="today")))
    await orchestrator.handle_turn(db_session, session, utterance="outlook is slow")
    assert session.collected["pending_question"] == "blocked"
    monkeypatch.setattr(nlu, "interpret_clarification", _async(nlu.TurnResult(escalation_requested=True, extras={"answer": "unclear"})))
    await orchestrator.handle_turn(db_session, session, utterance="can I talk to a real person")
    assert session.state in (VoiceCallState.ESCALATED, VoiceCallState.COLLECT_PHONE)


async def test_silence_while_a_question_is_pending_moves_on(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-12")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(started="today")))
    await orchestrator.handle_turn(db_session, session, utterance="outlook is slow")
    await orchestrator.handle_turn(db_session, session, utterance="")
    record = session.collected["facts"][WB]
    assert record["reason"] == UnknownReason.ASKED_UNCLEAR.value
    assert session.misunderstanding_count == 0


async def test_original_flow_is_unchanged_when_strict_is_off(db_session, monkeypatch):
    monkeypatch.setattr(settings, "voice_strict_extraction", False)
    await _seed(db_session)
    session = await _session(db_session, "CA-clar-13")
    legacy = nlu.TurnResult(
        value="x", confidence="high", unable_to_determine=False, category="Network", priority=Priority.MEDIUM,
        extras={"short_issue": "Wi-Fi", "started": "today", "work_blocked": False, "affected_scope": None},
    )
    monkeypatch.setattr(nlu, "interpret_description", _async(legacy))
    await orchestrator.handle_turn(db_session, session, utterance="wifi slow")
    assert not session.collected.get("pending_question") and not session.collected.get("clarify_asks")


# --- speech hints --------------------------------------------------------------------------


async def test_recognition_hints_follow_the_pending_question():
    for kind, hint in (("blocked", "yes or no"), ("patient_care", "patients"), ("scope", "anyone else")):
        mode = speech_context.recognition_mode("COLLECT_DETAILS", {"pending_question": kind})
        assert mode == f"CLARIFY_{kind.upper()}"
        assert hint in speech_context.stt_prompt("COLLECT_DETAILS", {"pending_question": kind})
    assert speech_context.recognition_mode("COLLECT_DETAILS", {}) == "COLLECT_DETAILS"

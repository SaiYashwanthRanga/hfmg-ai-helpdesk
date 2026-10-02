"""Strict extraction (VOICE_STRICT_EXTRACTION): only what the caller actually said.

The model's claim about work_blocked / patient_care_affected / affected_scope is
believed only when it is explicit and its quote is really in the transcript.
Everything else is an unknown fact -- never a guess.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, VoiceCallState
from app.llm import fake_provider
from app.voice import facts as facts_mod
from app.voice import nlu, orchestrator, scripts, strict_extraction
from app.voice.facts import Fact, Source, UnknownReason
from app.voice.session import get_or_create_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
WB, PC, SC = facts_mod.WORK_BLOCKED, facts_mod.PATIENT_CARE, facts_mod.SCOPE


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "voice_strict_extraction", True)


@pytest.fixture
def fake_model(monkeypatch):
    """Route the real extraction code through the keyword-rule fake provider."""
    monkeypatch.setattr(settings, "fake_llm_latency_ms", 0)
    monkeypatch.setattr(fake_provider.settings, "fake_llm_latency_ms", 0)
    monkeypatch.setattr(nlu, "get_provider", lambda: fake_provider.FakeProvider())
    monkeypatch.setattr(settings, "voice_nlu_hedge_after_seconds", 0)


def _fact(value, basis="explicit", evidence=None):
    return {"value": value, "basis": basis, "evidence": evidence}


def _model_says(blocked=None, patient=None, scope=None, *, context_mentioned=False, priority="Medium"):
    """A raw model response for record_issue."""
    return {
        "escalation_requested": False,
        "is_problem_description": True,
        "description": "Outlook will not open.",
        "short_issue": "your Outlook",
        "category": "Microsoft 365",
        "priority": priority,
        "category_confidence": "high",
        "caller_name": None, "caller_name_confidence": None, "department": None, "started": None,
        "work_blocked": blocked or _fact(None, "none"),
        "patient_care_affected": patient or _fact(None, "none"),
        "affected_scope": scope or _fact(None, "none"),
        "patient_context_mentioned": context_mentioned,
        "unable_to_determine": False,
    }


def _stub_model(monkeypatch, data):
    async def fake_call(system, user, name, properties):
        fake_call.seen = (system, user, name, properties)
        return data

    monkeypatch.setattr(nlu, "_call_structured", fake_call)
    return fake_call


# --- judging one claimed fact ---------------------------------------------------------------


async def test_explicit_grounded_fact_is_accepted_with_evidence():
    f = strict_extraction.judge_fact(WB, _fact(True, "explicit", "I can't work"), "I can't work, Outlook won't open.", Source.DESCRIPTION)
    assert f.value is True and f.evidence == "I can't work" and f.source is Source.DESCRIPTION
    assert f.is_trusted()


@pytest.mark.parametrize(
    "raw,reason",
    [
        (_fact(True, "implied", "won't open"), UnknownReason.IMPLIED_UNCONFIRMED),   # inferred
        (_fact(True, "none", None), UnknownReason.IMPLIED_UNCONFIRMED),
        (_fact(True, "explicit", "I can't work"), UnknownReason.UNGROUNDED),         # quote not in the transcript
        (_fact(True, "explicit", None), UnknownReason.UNGROUNDED),                   # no evidence at all
        (_fact(True, "explicit", "   "), UnknownReason.UNGROUNDED),
        (_fact(None, "implied", None), UnknownReason.IMPLIED_UNCONFIRMED),           # honest null, hinted
        (_fact(None, "none", None), UnknownReason.NOT_STATED),
        (_fact("yes", "explicit", "won't open"), UnknownReason.NOT_STATED),          # not a boolean
        (None, UnknownReason.NOT_STATED),
        ("true", UnknownReason.NOT_STATED),                                          # old-style bare value
    ],
)
async def test_unsupported_claims_become_unknown_never_true(raw, reason):
    f = strict_extraction.judge_fact(WB, raw, "Outlook won't open.", Source.DESCRIPTION)
    assert f.value is None and f.reason is reason


async def test_scope_must_be_one_of_the_known_values():
    ok = strict_extraction.judge_fact(SC, _fact("whole_site", "explicit", "the whole office"), "the whole office is down", Source.DESCRIPTION)
    assert ok.value == "whole_site"
    # An invalid claimed value is no claim; the words themselves still say whole_site.
    ignored = strict_extraction.judge_fact(SC, _fact("everyone", "explicit", "the whole office"), "the whole office is down", Source.DESCRIPTION)
    assert ignored.value == "whole_site"
    silent = strict_extraction.judge_fact(SC, _fact("everyone", "explicit", "it is down"), "it is down", Source.DESCRIPTION)
    assert silent.value is None


async def test_a_false_needs_the_same_proof_as_a_true():
    f = strict_extraction.judge_fact(WB, _fact(False, "explicit", "I can still work"), "Slow, but I can still work.", Source.DESCRIPTION)
    assert f.value is False
    g = strict_extraction.judge_fact(WB, _fact(False, "implied", None), "It's slow.", Source.DESCRIPTION)
    assert g.value is None


# --- the schema and prompts ---------------------------------------------------------------


def _check_strict_object(schema, path="root"):
    """OpenAI strict mode: every object lists all its keys as required and forbids extras."""
    if schema.get("type") == "object" or (isinstance(schema.get("type"), list) and "object" in schema["type"]):
        props = schema.get("properties", {})
        assert schema.get("additionalProperties") is False, path
        assert sorted(schema.get("required", [])) == sorted(props), path
        for name, sub in props.items():
            _check_strict_object(sub, f"{path}.{name}")


async def test_strict_schema_is_valid_for_strict_structured_output(strict, monkeypatch):
    call = _stub_model(monkeypatch, _model_says())
    await nlu.interpret_description("Outlook won't open.")
    _, _, name, properties = call.seen
    assert name == "record_issue"
    _check_strict_object(nlu._strict_schema(properties))
    for field in (WB, PC, SC):
        assert properties[field]["type"] == "object"
        assert set(properties[field]["properties"]) == {"value", "basis", "evidence"}
    assert properties["patient_context_mentioned"]["type"] == "boolean"


async def test_strict_prompt_demands_explicit_statements_and_drops_the_inference_wording(strict, monkeypatch):
    call = _stub_model(monkeypatch, _model_says())
    await nlu.interpret_description("Outlook won't open.")
    system, user, _, properties = call.seen
    assert "Explicit-statement rule" in system
    assert "Do not infer impact" in system
    assert "Medium for a fault" in system                    # unstated impact is not High
    assert "multiple users blocked" not in system            # the old priority guidance is gone
    blocked_text = properties[WB]["description"]
    assert "or it is minor" not in blocked_text
    assert "won't open" in blocked_text                      # named as NOT enough
    assert "'I can't work'" in blocked_text
    assert "NOT enough" in properties[PC]["description"]
    assert "NOT one_person" in properties[SC]["description"]
    assert "Outlook won't open." in user


async def test_original_prompt_and_schema_are_unchanged_when_the_flag_is_off(monkeypatch):
    monkeypatch.setattr(settings, "voice_strict_extraction", False)
    call = _stub_model(monkeypatch, {
        "escalation_requested": False, "is_problem_description": True, "description": "x problem here",
        "short_issue": "x", "category": "Other", "priority": "Medium", "category_confidence": "high",
        "caller_name": None, "caller_name_confidence": None, "department": None, "started": None,
        "work_blocked": True, "patient_care_affected": False, "affected_scope": "one_person",
        "unable_to_determine": False,
    })
    result = await nlu.interpret_description("my laptop is broken")
    system, _, _, properties = call.seen
    assert "Explicit-statement rule" not in system and "multiple users blocked" in system
    assert properties["work_blocked"]["type"] == ["boolean", "null"]
    assert "patient_context_mentioned" not in properties
    assert result.extras["work_blocked"] is True and "facts" not in result.extras   # legacy behaviour


# --- interpret_description ----------------------------------------------------------------


async def test_description_inferred_blocked_is_unknown_and_priority_stays_medium(strict, monkeypatch):
    _stub_model(monkeypatch, _model_says(blocked=_fact(True, "implied", "won't open")))
    result = await nlu.interpret_description("Outlook won't open.")
    assert result.extras["work_blocked"] is None
    assert result.extras["facts"][WB].reason is UnknownReason.IMPLIED_UNCONFIRMED
    assert result.extras["patient_care_affected"] is None and result.extras["affected_scope"] is None


async def test_description_explicit_blocked_is_kept_with_its_quote(strict, monkeypatch):
    _stub_model(monkeypatch, _model_says(blocked=_fact(True, "explicit", "I can't work")))
    result = await nlu.interpret_description("My Outlook won't open and I can't work.")
    assert result.extras["work_blocked"] is True
    assert result.extras["facts"][WB].evidence == "I can't work"


async def test_patient_charts_mention_is_context_not_patient_care(strict, monkeypatch):
    _stub_model(monkeypatch, _model_says(
        patient=_fact(True, "implied", "I use this for patient charts"), context_mentioned=True))
    result = await nlu.interpret_description("My laptop is slow, I use it for patient charts.")
    assert result.extras["patient_care_affected"] is None
    assert result.extras["patient_context_mentioned"] is True


async def test_made_up_patient_care_quote_is_rejected(strict, monkeypatch):
    _stub_model(monkeypatch, _model_says(patient=_fact(True, "explicit", "we can't check in patients")))
    result = await nlu.interpret_description("My laptop is slow.")
    assert result.extras["patient_care_affected"] is None
    assert result.extras["facts"][PC].reason is UnknownReason.UNGROUNDED


# --- the real extraction path through the fake model ---------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Outlook won't open.",
        "The printer is jammed.",
        "I forgot my password.",
        "I locked myself out of my account this morning.",
        "My laptop is slow, and I'm a nurse so I need it for patient charts.",
        "VPN is slow.",
    ],
)
async def test_audit_statements_do_not_invent_impact(strict, fake_model, text):
    result = await nlu.interpret_description(text)
    assert result.extras["work_blocked"] is None
    assert result.extras["patient_care_affected"] is None
    assert result.extras["affected_scope"] is None


@pytest.mark.parametrize(
    "text,expected_blocked,expected_scope",
    [
        ("I can't work, my Outlook won't open.", True, None),
        ("My Outlook crashes sometimes but I can still work.", False, None),
        ("The scanner at the front desk isn't working, so the whole front desk is stuck.", True, "several_people"),
    ],
)
async def test_explicit_statements_are_captured(strict, fake_model, text, expected_blocked, expected_scope):
    result = await nlu.interpret_description(text)
    assert result.extras["work_blocked"] is expected_blocked
    assert result.extras["affected_scope"] == expected_scope
    assert result.extras["facts"][WB].evidence


# --- interpret_details and corrections -------------------------------------------------------


async def test_details_bare_answer_is_unknown(strict, fake_model):
    result = await nlu.interpret_details("Yes.", asked=scripts.DETAILS_ASK_OPTIONS[0])
    assert result.extras["work_blocked"] is None
    assert result.extras["facts"][WB].source is Source.DETAILS


async def test_details_explicit_answer_is_recorded_as_details(strict, fake_model):
    result = await nlu.interpret_details("This morning, and I can still work.", asked=scripts.DETAILS_ASK_OPTIONS[0])
    assert result.extras["work_blocked"] is False
    assert result.extras["facts"][WB].source is Source.DETAILS
    assert result.extras["started"] == "this morning"


async def test_correction_changes_work_blocked_only_on_an_explicit_statement(strict, fake_model):
    quiet = await nlu.interpret_summary_correction("It started yesterday.", "A summary.")
    assert "work_blocked" not in quiet.extras["changes"]
    explicit = await nlu.interpret_summary_correction("Actually I can't work at all.", "A summary.")
    assert explicit.extras["changes"]["work_blocked"] is True
    assert explicit.extras["changes"]["work_blocked_evidence"].lower() == "can't work"


# --- the orchestrator records the facts --------------------------------------------------------


async def _seed(db):
    for name in ("Microsoft 365", "Other"):
        if (await db.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db.commit()


async def _session(db, call_sid):
    session = await get_or_create_session(db, call_sid=call_sid, from_number="+18455550142", to_number="+18455559999")
    await orchestrator.start_call(session)
    await db.commit()
    return session


def _async(value):
    async def _coro(*args, **kwargs):
        return value

    return _coro


def _described(facts=None, **extras):
    facts = facts or {}
    full = {f: facts.get(f, Fact.unknown(f)) for f in facts_mod.FIELDS}
    return nlu.TurnResult(
        value="Outlook won't open.", confidence="high", unable_to_determine=False, category="Microsoft 365",
        priority=Priority.MEDIUM,
        extras={
            "short_issue": "Outlook", "facts": full, "patient_context_mentioned": False,
            **{f: full[f].value for f in facts_mod.FIELDS}, **extras,
        },
    )


async def test_unknown_impact_is_asked_about_and_not_boosted(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-strict-unknown")
    monkeypatch.setattr(nlu, "interpret_description", _async(_described()))
    out = await orchestrator.handle_turn(db_session, session, utterance="Outlook won't open")

    assert session.collected["work_blocked"] is None
    assert session.collected["facts"]["work_blocked"]["state"] == "unknown"
    assert session.collected["priority"] == "MEDIUM"
    assert session.collected["priority_rule"] == "model_only"
    assert session.state == VoiceCallState.COLLECT_DETAILS          # the question is asked, not skipped
    assert any(o in out.text for o in scripts.DETAILS_ASK_OPTIONS)


async def test_explicit_blocked_is_recorded_with_evidence_and_gives_high(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-strict-blocked")
    blocked = {WB: Fact.known(WB, True, evidence="I can't work", confidence=0.9)}
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(blocked, started="this morning")))
    await orchestrator.handle_turn(db_session, session, utterance="Outlook won't open and I can't work")

    assert session.collected["work_blocked"] is True
    assert session.collected["facts"]["work_blocked"]["evidence"] == "I can't work"
    assert session.collected["priority"] == "HIGH" and session.collected["priority_rule"] == "caller_blocked"


async def test_unknown_details_answer_does_not_erase_a_known_fact(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-strict-keep")
    blocked = {WB: Fact.known(WB, False, evidence="I can still work", confidence=0.9)}
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(blocked)))
    await orchestrator.handle_turn(db_session, session, utterance="slow but I can still work")
    assert session.collected["work_blocked"] is False

    unknown_details = nlu.TurnResult(
        value="yesterday", unable_to_determine=False,
        extras={"started": "yesterday", "work_blocked": None, "affected_scope": None,
                "facts": {WB: Fact.unknown(WB, source=Source.DETAILS), SC: Fact.unknown(SC, source=Source.DETAILS)}},
    )
    monkeypatch.setattr(nlu, "interpret_details", _async(unknown_details))
    session.state = VoiceCallState.COLLECT_DETAILS
    await orchestrator.handle_turn(db_session, session, utterance="since yesterday")
    assert session.collected["work_blocked"] is False
    assert session.collected["started"] == "yesterday"


async def test_correction_is_recorded_as_a_correction_fact(db_session, monkeypatch):
    await _seed(db_session)
    session = await _session(db_session, "CA-strict-correct")
    blocked = {WB: Fact.known(WB, False, evidence="I can still work", confidence=0.9)}
    monkeypatch.setattr(nlu, "interpret_description", _async(_described(blocked)))
    await orchestrator.handle_turn(db_session, session, utterance="slow but I can still work")

    orchestrator._apply_corrections(session, {"work_blocked": True, "work_blocked_evidence": "can't work at all"})
    record = session.collected["facts"]["work_blocked"]
    assert record["source"] == "correction" and record["evidence"] == "can't work at all"
    assert session.collected["work_blocked"] is True and session.collected["priority"] == "HIGH"

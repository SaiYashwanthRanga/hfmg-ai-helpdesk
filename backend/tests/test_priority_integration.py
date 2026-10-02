"""Priority over three-state facts: unknown neither boosts nor caps, and is reported as unverified."""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket
from app.voice import facts as facts_mod
from app.voice import nlu, orchestrator, scripts
from app.voice import priority as rules
from app.voice.facts import Fact, Source, UnknownReason
from app.voice.session import get_or_create_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
WB, PC, SC = facts_mod.WORK_BLOCKED, facts_mod.PATIENT_CARE, facts_mod.SCOPE
L, M, H, U = Priority.LOW, Priority.MEDIUM, Priority.HIGH, Priority.URGENT


def known(field, value, evidence=None):
    return Fact.known(field, value, source=Source.DESCRIPTION, confidence=0.9, evidence=evidence)


def unknown(field):
    return Fact.unknown(field, UnknownReason.NOT_STATED)


def assess(model=M, wb=None, pc=None, sc=None, category="Microsoft 365", context=False):
    facts = {
        WB: wb if isinstance(wb, Fact) else (known(WB, wb) if wb is not None else unknown(WB)),
        PC: pc if isinstance(pc, Fact) else (known(PC, pc) if pc is not None else unknown(PC)),
        SC: sc if isinstance(sc, Fact) else (known(SC, sc) if sc is not None else unknown(SC)),
    }
    return rules.assess_facts(model_priority=model, facts=facts, category=category, patient_context=context)


# --- unknown work impact: no boost, no cap -----------------------------------------------------------


@pytest.mark.parametrize("model", [L, M, H, U])
async def test_unknown_work_impact_passes_the_models_rating_through_unchanged(model):
    a = assess(model=model)
    assert a.priority == model and a.rule == "model_only"          # not boosted, not capped
    assert "work_impact" in a.unverified


async def test_unknown_is_not_treated_as_false_or_true():
    unknown_ = assess(model=H)
    assert unknown_.priority == H                                    # a false would cap it at Medium
    assert assess(model=M).priority == M                             # a true would raise it to High
    assert assess(model=H, wb=False).priority == M                   # known false does cap
    assert assess(model=M, wb=True).priority == H                    # known true does boost


async def test_known_work_impact_is_not_unverified():
    assert assess(wb=True).unverified == ()
    assert assess(wb=False).unverified == ()


# --- unknown patient impact: never Critical ------------------------------------------------------------


async def test_unknown_patient_care_never_escalates_to_critical():
    a = assess(model=H, wb=True, category="eClinicalWorks", context=True)
    assert a.priority == H and a.rule == "caller_blocked"
    assert "patient_impact" in a.unverified and a.needs_triage


async def test_stated_patient_care_is_critical_with_the_words_as_the_basis():
    a = assess(model=M, pc=known(PC, True, "we can't check anyone in"), category="eClinicalWorks")
    assert a.priority == U and a.rule == "patient_care"
    assert a.basis == 'caller said "we can\'t check anyone in"'
    assert a.unverified == ("work_impact",) or a.unverified == ()    # patient care decided it either way
    assert not a.needs_triage


async def test_patient_care_explicitly_false_is_resolved():
    a = assess(wb=False, pc=False, category="eClinicalWorks", context=True)
    assert "patient_impact" not in a.unverified and not a.needs_triage


# --- triage review ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category,context,expected",
    [
        ("eClinicalWorks", False, True),     # EHR issue, nothing settled
        ("Microsoft 365", True, True),       # patients were mentioned, nothing settled
        ("Microsoft 365", False, False),     # not clinical: unverified, but no review
        ("Network", False, False),
    ],
)
async def test_triage_review_is_for_clinical_issues_with_unresolved_safety_facts(category, context, expected):
    a = assess(category=category, context=context)
    assert a.needs_triage is expected
    assert "work_impact" in a.unverified


async def test_triage_note_lists_what_is_unresolved():
    note = rules.triage_note(assess(category="eClinicalWorks"))
    assert note.startswith("NEEDS TRIAGE REVIEW - ") and "patient impact not confirmed" in note and "work impact not confirmed" in note
    assert rules.triage_note(assess(category="Network")) is None
    assert "work impact" not in rules.triage_note(assess(wb=True, category="eClinicalWorks"))


async def test_decided_priority_leaves_nothing_unverified_about_work():
    site = assess(sc="whole_site", category="Network")
    assert site.priority == U and site.rule == "site_wide" and "work_impact" not in site.unverified
    patients = assess(pc=True, category="eClinicalWorks")
    assert "work_impact" not in patients.unverified


# --- basis -----------------------------------------------------------------------------------------------


async def test_basis_quotes_the_words_behind_the_rule_that_fired():
    assert assess(wb=known(WB, True, "I can't work")).basis == 'caller said "I can\'t work"'
    assert assess(wb=known(WB, False, "I can still work")).basis == 'caller said "I can still work"'
    assert assess(sc=known(SC, "whole_site", "the whole office")).basis == 'caller said "the whole office"'
    assert assess().basis is None                                     # nothing was said: nothing to quote
    assert assess(wb=True).basis is None                              # a bare True carries no evidence


# --- the original rules are untouched ----------------------------------------------------------------------


async def test_assess_itself_behaves_as_before_for_plain_values():
    a = rules.assess(model_priority=M, work_blocked=True, scope="one_person")
    assert (a.priority, a.rule, a.unverified, a.needs_triage, a.basis) == (H, "caller_blocked", (), False, None)


# --- the orchestrator and the ticket -------------------------------------------------------------------------


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "voice_strict_extraction", True)
    monkeypatch.setattr(settings, "voice_verify_safety_facts", False)


async def _seed(db):
    for name in ("Microsoft 365", "eClinicalWorks", "Other"):
        if (await db.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db.commit()


async def _session(db, call_sid, **collected):
    session = await get_or_create_session(db, call_sid=call_sid, from_number="+18455550142", to_number="+18455559999")
    await orchestrator.start_call(session)
    session.collected = {**session.collected, "description": "The EHR is slow.", "caller_name": "Maria Lopez",
                         "category": "eClinicalWorks", "model_priority": "MEDIUM", **collected}
    await db.commit()
    return session


async def test_strict_calls_record_unverified_flags_and_the_ticket_says_so(db_session, strict):
    await _seed(db_session)
    session = await _session(db_session, "CA-prio-1", patient_context_mentioned=True)
    orchestrator._apply_priority_rules(session)
    assert session.collected["priority_unverified"] == ["work_impact", "patient_impact"]
    assert session.collected["needs_triage"] is True and session.collected["priority"] == "MEDIUM"

    ticket = await orchestrator._create_ticket(db_session, session)
    lines = ticket.description.splitlines()
    assert lines[0] == "NEEDS TRIAGE REVIEW - patient impact not confirmed, work impact not confirmed."
    assert "Work impact: not confirmed" in ticket.description
    assert "Patient impact: not confirmed" in ticket.description
    assert "Work blocked:" not in ticket.description                  # an unknown is not printed as "no"


async def test_the_ticket_quotes_the_callers_words(db_session, strict):
    await _seed(db_session)
    wb = Fact.known(WB, True, source=Source.ANSWER, confidence=0.95, evidence="Yes, I can't do anything")
    session = await _session(db_session, "CA-prio-2", category="Microsoft 365")
    session.collected = facts_mod.apply_fact(session.collected, wb)
    orchestrator._apply_priority_rules(session)
    ticket = await orchestrator._create_ticket(db_session, session)
    assert ticket.priority == Priority.HIGH
    assert 'Priority basis: caller said "Yes, I can\'t do anything"' in ticket.description
    assert "NEEDS TRIAGE" not in ticket.description and "not confirmed" not in ticket.description


async def test_non_clinical_unknown_is_unverified_but_not_flagged_for_triage(db_session, strict):
    await _seed(db_session)
    session = await _session(db_session, "CA-prio-3", category="Microsoft 365")
    orchestrator._apply_priority_rules(session)
    assert session.collected["priority_unverified"] == ["work_impact"] and session.collected["needs_triage"] is False
    ticket = await orchestrator._create_ticket(db_session, session)
    assert "NEEDS TRIAGE" not in ticket.description and "Work impact: not confirmed" in ticket.description


async def test_the_read_back_is_honest_about_what_was_not_settled(db_session, strict):
    await _seed(db_session)
    flagged = await _session(db_session, "CA-prio-4", patient_context_mentioned=True)
    orchestrator._apply_priority_rules(flagged)
    assert scripts.SUMMARY_FLAGGED_FOR_REVIEW in orchestrator._summary_text(flagged)

    plain = await _session(db_session, "CA-prio-5", category="Microsoft 365")
    orchestrator._apply_priority_rules(plain)
    text = orchestrator._summary_text(plain)
    assert scripts.SUMMARY_WORK_IMPACT_UNKNOWN in text and scripts.SUMMARY_FLAGGED_FOR_REVIEW not in text

    known_ = await _session(db_session, "CA-prio-6", category="Microsoft 365")
    known_.collected = facts_mod.apply_fact(known_.collected, Fact.known(WB, False, source=Source.ANSWER, confidence=0.95))
    orchestrator._apply_priority_rules(known_)
    text = orchestrator._summary_text(known_)
    assert "couldn't confirm" not in text and "Since you can still work" in text


async def test_escalation_tickets_keep_their_raise_and_still_carry_the_triage_line(db_session, strict):
    await _seed(db_session)
    session = await _session(db_session, "CA-prio-7", patient_context_mentioned=True)
    orchestrator._apply_priority_rules(session)
    ticket = await orchestrator._create_ticket(db_session, session, escalation_note="CALLBACK REQUESTED - caller asked for a person.")
    assert ticket.priority == Priority.HIGH                            # escalation raise: unchanged behaviour
    assert ticket.description.startswith("NEEDS TRIAGE REVIEW")


async def test_original_behaviour_is_unchanged_when_strict_is_off(db_session, monkeypatch):
    monkeypatch.setattr(settings, "voice_strict_extraction", False)
    await _seed(db_session)
    session = await _session(db_session, "CA-prio-8", work_blocked=True, affected_scope="one_person")
    orchestrator._apply_priority_rules(session)
    assert session.collected["priority"] == "HIGH" and session.collected["priority_rule"] == "caller_blocked"
    assert "needs_triage" not in session.collected and "priority_unverified" not in session.collected
    ticket = await orchestrator._create_ticket(db_session, session)
    assert "NEEDS TRIAGE" not in ticket.description and "not confirmed" not in ticket.description
    assert (await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))).scalar_one().priority == Priority.HIGH

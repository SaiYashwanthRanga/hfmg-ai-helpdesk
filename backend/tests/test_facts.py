"""Three-state facts: construction, precedence, storage compatibility, grounding."""

import json

import pytest

from app.voice import facts
from app.voice.facts import Fact, Source, UnknownReason

WB, PC, SC = facts.WORK_BLOCKED, facts.PATIENT_CARE, facts.SCOPE


# --- construction and reading -----------------------------------------------------------


def test_known_boolean_fact_states():
    assert Fact.known(WB, True).state == "true"
    assert Fact.known(WB, False).state == "false"
    assert Fact.unknown(WB).state == "unknown"
    assert Fact.known(SC, "whole_site").state == "whole_site"


def test_unknown_defaults_to_not_stated_with_zero_confidence():
    f = Fact.unknown(PC)
    assert f.value is None and not f.is_known
    assert f.reason is UnknownReason.NOT_STATED and f.confidence == 0.0


def test_values_are_validated():
    with pytest.raises(ValueError):
        Fact.known(WB, "yes")            # not a boolean
    with pytest.raises(ValueError):
        Fact.known(SC, "everyone")       # not a scope
    with pytest.raises(ValueError):
        Fact.known(SC, True)
    with pytest.raises(ValueError):
        Fact.known("priority", True)     # not a fact field
    with pytest.raises(ValueError):
        Fact.known(WB, None)             # use Fact.unknown
    with pytest.raises(ValueError):
        Fact.known(WB, True, confidence=1.5)


def test_trusted_needs_a_known_value_and_enough_confidence():
    assert Fact.known(WB, True, confidence=0.9).is_trusted()
    assert not Fact.known(WB, True, confidence=0.79).is_trusted()
    assert not Fact.unknown(WB, confidence=0.95).is_trusted()
    assert Fact.known(WB, True, confidence=0.5).is_trusted(threshold=0.5)


def test_round_trips_through_json():
    original = Fact.known(WB, True, source=Source.ANSWER, confidence=0.95, evidence="I can't work")
    again = Fact.from_dict(WB, json.loads(json.dumps(original.to_dict())))
    assert again == original
    unknown = Fact.unknown(PC, UnknownReason.VERIFIER_REJECTED, source=Source.DETAILS)
    assert Fact.from_dict(PC, json.loads(json.dumps(unknown.to_dict()))) == unknown


# --- precedence -----------------------------------------------------------------------


def known(value, source, field=WB):
    return Fact.known(field, value, source=source, confidence=0.9)


def test_first_fact_is_taken():
    new = known(True, Source.DESCRIPTION)
    assert facts.merge(None, new) is new


def test_unknown_never_overwrites_known():
    current = known(False, Source.DESCRIPTION)
    for source in Source:
        assert facts.merge(current, Fact.unknown(WB, source=source)) == current


def test_known_replaces_unknown():
    new = known(True, Source.DESCRIPTION)
    assert facts.merge(Fact.unknown(WB, source=Source.ANSWER), new) == new


@pytest.mark.parametrize(
    "older,newer",
    [
        (Source.DESCRIPTION, Source.DETAILS),
        (Source.DETAILS, Source.ANSWER),
        (Source.ANSWER, Source.CORRECTION),
        (Source.DESCRIPTION, Source.CORRECTION),
    ],
)
def test_higher_source_overrides_lower(older, newer):
    assert facts.merge(known(True, older), known(False, newer)).value is False


@pytest.mark.parametrize("older,newer", [(Source.DETAILS, Source.DESCRIPTION), (Source.CORRECTION, Source.ANSWER)])
def test_lower_source_cannot_override_higher(older, newer):
    assert facts.merge(known(True, older), known(False, newer)).value is True


def test_same_source_later_statement_wins():
    assert facts.merge(known(True, Source.ANSWER), known(False, Source.ANSWER)).value is False


def test_between_two_unknowns_the_higher_source_is_kept():
    low = Fact.unknown(WB, UnknownReason.NOT_STATED, source=Source.DESCRIPTION)
    high = Fact.unknown(WB, UnknownReason.ASKED_UNCLEAR, source=Source.ANSWER)
    assert facts.merge(low, high) is high
    assert facts.merge(high, low) is high


def test_merge_rejects_different_fields():
    with pytest.raises(ValueError):
        facts.merge(Fact.unknown(WB), Fact.unknown(PC))


def test_conflicts_only_between_known_disagreeing_facts():
    assert facts.conflicts(known(True, Source.DESCRIPTION), known(False, Source.ANSWER))
    assert not facts.conflicts(known(True, Source.DESCRIPTION), known(True, Source.ANSWER))
    assert not facts.conflicts(Fact.unknown(WB), known(True, Source.ANSWER))
    assert not facts.conflicts(None, known(True, Source.ANSWER))


# --- the collected mapping --------------------------------------------------------------


def test_apply_fact_writes_the_record_and_the_legacy_mirror():
    out = facts.apply_fact({"other": 1}, Fact.known(WB, True, evidence="I cannot work", confidence=0.95))
    assert out["work_blocked"] is True
    assert out["facts"]["work_blocked"]["state"] == "true"
    assert out["facts"]["work_blocked"]["evidence"] == "I cannot work"
    assert out["other"] == 1


def test_apply_fact_does_not_mutate_its_input():
    collected = {"facts": {}}
    facts.apply_fact(collected, Fact.known(WB, True))
    assert collected == {"facts": {}}


def test_apply_unknown_mirrors_none():
    out = facts.apply_fact({}, Fact.unknown(WB, UnknownReason.IMPLIED_UNCONFIRMED))
    assert out["work_blocked"] is None
    assert out["facts"]["work_blocked"]["reason"] == "implied_unconfirmed"


def test_apply_fact_follows_precedence():
    c = facts.apply_fact({}, known(True, Source.DESCRIPTION))
    c = facts.apply_fact(c, known(False, Source.ANSWER))
    assert c["work_blocked"] is False
    c = facts.apply_fact(c, known(True, Source.DESCRIPTION))   # lower source: ignored
    assert c["work_blocked"] is False
    c = facts.apply_fact(c, Fact.unknown(WB, source=Source.CORRECTION))  # unknown: ignored
    assert c["work_blocked"] is False


def test_apply_facts_applies_several():
    out = facts.apply_facts({}, [Fact.known(WB, False), Fact.known(SC, "one_person"), Fact.unknown(PC)])
    assert (out["work_blocked"], out["affected_scope"], out["patient_care_affected"]) == (False, "one_person", None)


def test_legacy_values_are_rebuilt_without_a_facts_record():
    collected = {"work_blocked": False, "affected_scope": "several_people", "patient_care_affected": True}
    all_ = facts.all_facts(collected)
    assert all_[WB].value is False and all_[WB].source is Source.LEGACY
    assert all_[SC].value == "several_people"
    assert all_[PC].value is True


def test_legacy_patient_care_false_means_not_asserted():
    # The old extraction could not tell "no" from "not said".
    assert not facts.get_fact({"patient_care_affected": False}, PC).is_known
    assert not facts.get_fact({}, WB).is_known
    assert not facts.get_fact({"affected_scope": "nonsense"}, SC).is_known


def test_a_legacy_value_takes_part_in_precedence():
    collected = {"work_blocked": True}  # an old session: no facts record
    out = facts.apply_fact(collected, Fact.unknown(WB, source=Source.DETAILS))
    assert out["work_blocked"] is True                       # unknown does not erase it
    out = facts.apply_fact(collected, known(False, Source.DESCRIPTION))
    assert out["work_blocked"] is False                      # legacy is the lowest rank


# --- grounding --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance,quote",
    [
        ("I can't work, my Outlook won't open.", "I can't work"),
        ("I can't work, my Outlook won't open.", "i CAN'T work"),
        ("I can’t work at all.", "I can't work"),          # curly apostrophe
        ("Um, I... I cannot do my job, okay?", "I cannot do my job"),
        ("Honestly, it's stopping me from working today.", "stopping me from working"),
        ("I can't work. The VPN is down and I'm stuck.", "I can't work... I'm stuck"),  # ordered pieces
    ],
)
def test_real_quotes_are_grounded(utterance, quote):
    assert facts.is_grounded(utterance, quote)


@pytest.mark.parametrize(
    "utterance,quote",
    [
        ("Outlook won't open.", "I can't work"),                 # invented
        ("I can still work.", "I cannot work"),                  # paraphrase that flips the meaning
        ("I'm stuck. I can't work.", "I can't work... I'm stuck"),  # pieces out of order
        ("anything", ""),
        ("anything", None),
        ("anything", "   "),
        ("anything", "..."),
        ("it is ok", "ok"),                                      # too short to be evidence
    ],
)
def test_made_up_or_empty_quotes_are_not_grounded(utterance, quote):
    assert not facts.is_grounded(utterance, quote)


def test_normalize_text():
    assert facts.normalize_text("  I CAN’T   work!! ") == "i can't work"


def test_an_overridden_conflicting_fact_is_logged(caplog):
    first = facts.apply_fact({}, known(True, Source.DESCRIPTION))
    with caplog.at_level("INFO", logger="hfmg.voice.facts"):
        facts.apply_fact(first, known(False, Source.ANSWER))
    assert any("work_blocked" in r.message and "true (description) vs false (answer) -> kept false" in r.message for r in caplog.records)

    caplog.clear()
    with caplog.at_level("INFO", logger="hfmg.voice.facts"):
        facts.apply_fact(first, known(True, Source.ANSWER))      # agreement: nothing to log
    assert not caplog.records

"""The deterministic phrase gate: strong patterns, counter-evidence, safe fallback to unknown."""

import pytest

from app.voice import phrase_gate as gate
from app.voice import strict_extraction
from app.voice.facts import PATIENT_CARE, SCOPE, WORK_BLOCKED, Source, UnknownReason

WB, PC, SC = WORK_BLOCKED, PATIENT_CARE, SCOPE


def value(field, text):
    return gate.inspect(field, text).value


# --- work_blocked: stated as blocked --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I can't work, my Outlook won't open.",
        "I cannot work.",
        "I cannot do my job right now, eClinicalWorks is frozen.",
        "I can't do anything.",
        "I am not able to do any work, my computer won't start.",
        "We are unable to work until this is fixed.",
        "I really can't get any work done.",
        "I can't work, Outlook won't open.",                           # 'work Outlook' must not trip the 'work out' exclusion
        "I cannot work outlook is frozen.",
        "I can’t do my work without email.",                      # curly apostrophe from the transcript
        "It's stopping me from working.",
        "The printer error is stopping us from doing our jobs.",
        "I'm completely blocked.",
        "I am stuck.",
        "We're all stuck.",
        "The whole front desk is stuck.",
        "Nobody up front can do anything.",
        "We can't check in any patients.",
        "I can't see patients.",
        "We can't check anyone in.",
        "I can't log in to my computer.",
        "I can't log in at all.",
        "I'm locked out of my computer.",
        "I can't get past the login screen.",
    ],
)
def test_explicit_blocked_statements(text):
    assert value(WB, text) is True


@pytest.mark.parametrize(
    "text",
    [
        # a symptom or a system fault is not a statement about the caller's work
        "Outlook won't open.",
        "My printer is jammed.",
        "I forgot my password.",
        "I locked myself out of my account this morning.",
        "I got locked out of eClinicalWorks after too many tries.",
        "I can't log in to Outlook.",                                  # one application: asked, not assumed
        "I cannot log in.",
        "Teams isn't working for me, I'm in a meeting and can't join.",
        "The person at the front desk can't print anything today.",
        "I can't print my timesheet.",
        "I can't work out why the printer is jammed.",                 # 'work out'
        "I can't do my taxes on this computer.",
        "My printer can't work properly.",                             # the system, not the person
        "The label printer can't work with these labels.",
        "It's slow and annoying.",
        "This is urgent! I need it fixed right now.",
        "Excel freezes occasionally.",
        "The ticket is stuck in the queue.",                           # a system, not 'I'm stuck'
        "My computer is stuck on the login screen.",
        "I use this for patient charts.",
        "",
    ],
)
def test_symptoms_and_non_statements_are_not_blocked(text):
    assert value(WB, text) is None


# --- work_blocked: stated as able to work ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I can still work.",
        "It's slow, but I can still work.",
        "We can still use the old one.",
        "I can still type it in by hand.",
        "I can work, it's just painful.",
        "I can keep working.",
        "I am able to continue working.",
        "I'm still working.",
        "There's a workaround.",
        "I'm using my phone instead.",
        "I can get my email on my phone.",
        "It's not stopping me.",
        "I can use the paper charts.",
    ],
)
def test_explicit_can_still_work_statements(text):
    assert value(WB, text) is False


def test_blocked_and_can_work_together_is_a_conflict():
    verdict = gate.inspect(WB, "I can't work today, but I can still type my notes.")
    assert verdict.conflict and verdict.value is None
    assert gate.inspect(WB, "I can't print but I can still work.").value is False   # printing is not working
    assert gate.inspect(WB, "I can't work on the old system.").value is None          # a system, not a statement about them


# --- patient_care_affected --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "We can't check in any patients.",
        "I can't see patients right now.",
        "We can't check anyone in.",
        "Patients can't be checked in.",
        "Patients cannot be seen.",
        "I can't open patient charts.",
        "I can't pull up the chart for the patient in room 3.",
        "None of the providers at our site can open patient charts.",
        "Nobody here can see patients.",
    ],
)
def test_patient_care_explicitly_blocked(text):
    assert value(PC, text) is True


@pytest.mark.parametrize(
    "text",
    [
        "I use this for patient charts.",                              # the user's own example
        "I use this computer for patient charts and it keeps freezing.",
        "I'm a nurse and my badge reader isn't working.",
        "Patients are waiting but the printer in the lobby is out of toner.",
        "The doctor's laptop is slow during patient visits.",
        "My laptop is slow, and I'm a nurse so I need it for patient charts.",
        "The wifi in the waiting room is slow and patients are complaining.",
        "eClinicalWorks is slow.",
        "I'm in a clinic.",
        "",
    ],
)
def test_patient_context_is_not_patient_care(text):
    assert value(PC, text) is None


@pytest.mark.parametrize(
    "text",
    ["Patient care is not affected, but eClinicalWorks is slow.", "It isn't affecting patient care.", "Patients are not affected."],
)
def test_patient_care_explicitly_not_affected(text):
    assert value(PC, text) is False


# --- affected_scope ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("It's just me, my Outlook is slow.", "one_person"),
        ("Just my computer is acting up.", "one_person"),
        ("Nobody else is having this.", "one_person"),
        ("My whole team can't print.", "several_people"),
        ("Nobody in billing can log in.", "several_people"),
        ("Several of us on the second floor can't print.", "several_people"),
        ("The whole front desk is stuck.", "several_people"),
        ("Teams keeps crashing for the whole billing department.", "several_people"),
        ("None of the providers at our site can open charts.", "several_people"),
        ("The internet is out at the entire Middletown site.", "whole_site"),
        ("Everyone in the office is offline.", "whole_site"),
        ("It's down for the whole Newburgh office.", "whole_site"),
        ("All of our locations are down.", "whole_site"),
    ],
)
def test_scope_statements(text, expected):
    assert value(SC, text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "My Outlook is slow.", "The printer is jammed.", "I forgot my password.", "The label printer in the lab is out.",
        "The lab printer stopped.", "The front desk printer is jammed.", "I'm working from home today.",
    ],
)
def test_unstated_scope_is_none(text):
    assert value(SC, text) is None


def test_two_different_scopes_conflict():
    assert gate.inspect(SC, "Just me, but the whole office is slow too.").conflict


def test_unknown_field_is_rejected():
    with pytest.raises(ValueError):
        gate.inspect("priority", "anything")


# --- the gate against every labelled corpus utterance ----------------------------------------------


def test_gate_never_contradicts_the_corpus_truth_and_finds_the_explicit_statements():
    from eval.extraction_corpus import CASES

    contradictions, missed = [], []
    for case in CASES:
        for field, key in ((WB, "wb"), (PC, "pc"), (SC, "scope")):
            verdict = gate.inspect(field, case["text"])
            truth = case[key]
            if verdict.conflict:
                contradictions.append((case["id"], field, "conflict"))
            elif verdict.value is not None and verdict.value != truth:
                contradictions.append((case["id"], field, verdict.value, truth))
            if truth is not None and verdict.value != truth:
                missed.append((case["id"], field, truth))
    assert not contradictions, contradictions
    assert not missed, missed


# --- judge_fact: the model proposes, the words decide --------------------------------------------


def fact(value=None, basis="none", evidence=None):
    return {"value": value, "basis": basis, "evidence": evidence}


def judge(field, raw, text):
    return strict_extraction.judge_fact(field, raw, text, Source.DESCRIPTION)


def test_gate_confirms_a_model_claim_and_keeps_the_models_quote():
    f = judge(WB, fact(True, "explicit", "can't work"), "My Outlook won't open and I can't work.")
    assert f.value is True and f.evidence == "can't work" and f.confidence >= 0.9


def test_a_grounded_claim_the_words_do_not_support_is_rejected():
    # The model quotes real words, but they do not state that the caller cannot work.
    f = judge(WB, fact(True, "explicit", "won't open"), "Outlook won't open.")
    assert f.value is None and f.reason is UnknownReason.GATE_REJECTED


def test_model_and_words_disagree_is_a_conflict():
    f = judge(WB, fact(False, "explicit", "I can't work"), "I can't work.")
    assert f.value is None and f.reason is UnknownReason.CONFLICTING


def test_explicit_words_are_accepted_even_when_the_model_found_nothing():
    f = judge(WB, fact(None, "none"), "I can't do my job right now.")
    assert f.value is True and f.evidence == "i can't do my job"      # the matched words are the evidence


def test_contradicting_statements_become_unknown_whatever_the_model_says():
    f = judge(WB, fact(True, "explicit", "I can't work"), "I can't work today but I can still type my notes.")
    assert f.value is None and f.reason is UnknownReason.CONFLICTING


def test_scope_claim_must_match_the_words():
    ok = judge(SC, fact("whole_site", "explicit", "the whole office"), "The whole office is down.")
    assert ok.value == "whole_site"
    off = judge(SC, fact("one_person", "explicit", "the whole office"), "The whole office is down.")
    assert off.value is None and off.reason is UnknownReason.CONFLICTING


@pytest.mark.parametrize(
    "text",
    ["I use this for patient charts.", "My laptop is slow and I am a nurse who needs it for patient charts."],
)
def test_patient_charts_mention_never_becomes_patient_care(text):
    for raw in (fact(True, "explicit", "patient charts"), fact(True, "implied", "patient charts"), fact(None, "none")):
        assert judge(PC, raw, text).value is None


def test_inference_is_never_accepted_even_with_a_real_quote():
    for text in ("Outlook won't open.", "The printer is jammed.", "I forgot my password.", "VPN is slow."):
        for quote in (text, text.rstrip(".")):
            f = judge(WB, fact(True, "explicit", quote), text)
            assert f.value is None, text

"""Priority reasoning: one function decides the priority *and* why.

A real test call produced: "Since this is affecting one user, minor issue,
I'm marking it high priority." The priority came from one rule ("the caller
can't work") and the reason from a different place (the model's free-text
impact note, "minor issue"), so the agent contradicted itself out loud.

Here the priority and its reason come from the same rule, so what the agent
says can never disagree with what it decided. Every rule uses only facts the
caller stated (can they work, who is affected, is patient care affected); the
model's own rating is an input, never the explanation.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from app.db.models import Priority
from app.voice import facts as facts_mod
from app.voice.facts import Fact, UnknownReason

SCOPES = {"one_person": "One person", "several_people": "Several people", "whole_site": "Whole site"}

_ORDER = [Priority.LOW, Priority.MEDIUM, Priority.HIGH, Priority.URGENT]

# How the agent says a level out loud. The enum predates Critical, so URGENT is spoken "critical".
SPOKEN = {Priority.URGENT: "critical", Priority.HIGH: "high", Priority.MEDIUM: "medium", Priority.LOW: "low"}


@dataclass(frozen=True)
class Assessment:
    priority: Priority
    #: Spoken clause completing "Since ___": "you can't work". None = nothing to say.
    reason: str | None
    #: Recorded on the ticket. Built from the same facts as `reason`, never from model prose.
    impact: str
    #: Which rule fired -- for tests and the conversation inspector.
    rule: str
    #: Facts the caller never settled ("work_impact", "patient_impact"). Strict extraction only.
    unverified: tuple[str, ...] = ()
    #: A clinical issue whose safety facts are unresolved: a person should look at the ticket.
    needs_triage: bool = False
    #: The caller's own words behind the rule that fired, when there are any.
    basis: str | None = None


def _cap(priority: Priority, ceiling: Priority) -> Priority:
    return _ORDER[min(_ORDER.index(priority), _ORDER.index(ceiling))]


def _floor(priority: Priority, floor: Priority) -> Priority:
    return _ORDER[max(_ORDER.index(priority), _ORDER.index(floor))]


def impact_text(*, work_blocked: bool | None, scope: str | None, patient_care: bool | None) -> str:
    """"Several people, unable to work" -- consistent with the rule that fired."""
    parts = []
    if patient_care:
        parts.append("Patient care affected")
    if scope in SCOPES:
        parts.append(SCOPES[scope])
    if work_blocked is True:
        parts.append("unable to work")
    elif work_blocked is False:
        parts.append("can still work")
    return ", ".join(parts) if parts else "Impact not stated"


def assess(
    *,
    model_priority: Priority | None,
    work_blocked: bool | None,
    scope: str | None,
    patient_care: bool | None = None,
) -> Assessment:
    """Decide priority from what the caller said.

    - patient care blocked, or a whole site down          -> Critical
    - several people can't work                            -> High (Critical if the model said so)
    - the caller can't work at all                         -> High
    - only the caller is affected and they can still work -> at most Medium
    - a team/site is affected but the caller can work     -> the model's rating, explained if high
    - nothing stated                                       -> the model's rating, unexplained
    """
    model = model_priority or Priority.MEDIUM
    impact = impact_text(work_blocked=work_blocked, scope=scope, patient_care=patient_care)

    def result(priority: Priority, reason: str | None, rule: str) -> Assessment:
        return Assessment(priority, reason, impact, rule)

    if patient_care and work_blocked is not False:
        return result(Priority.URGENT, "it's affecting patient care", "patient_care")
    if scope == "whole_site" and work_blocked is not False:
        return result(Priority.URGENT, "it's affecting your whole site", "site_wide")

    if work_blocked is True:
        if scope == "several_people":
            top = Priority.URGENT if model == Priority.URGENT else Priority.HIGH
            return result(top, "your team can't work", "team_blocked")
        return result(Priority.HIGH, "you can't work", "caller_blocked")

    if work_blocked is False:
        if scope in ("whole_site", "several_people"):
            # The caller can work, but others are affected: how bad that is
            # is the model's call. Explain it only if the level is high.
            label = "your whole site" if scope == "whole_site" else "several people"
            return result(model, f"it's affecting {label}" if _order(model) >= 2 else None, "others_affected")
        # One person (or unstated) who can still work is never above Medium.
        return result(_cap(model, Priority.MEDIUM), None, "caller_working")

    # The caller didn't say whether they can work: trust the model's rating,
    # and only explain it if the scope alone supports the explanation.
    if _order(model) >= 2:
        reason = "it's affecting several people" if scope == "several_people" else None
        return result(model, reason, "model_only")
    return result(model, None, "model_only")


def _order(priority: Priority) -> int:
    return _ORDER.index(priority)


def notice(assessment: Assessment) -> str | None:
    """The spoken priority sentence, or None when there is nothing worth announcing.

    Only High and Critical are announced: telling every caller "medium" is noise.
    """
    if assessment.priority not in (Priority.HIGH, Priority.URGENT):
        return None
    word = SPOKEN[assessment.priority]
    if assessment.reason:
        return f"Since {assessment.reason}, I'll mark this {word} priority."
    return f"I'll mark this {word} priority."


# --- strict extraction: three-state facts --------------------------------------------------------

#: Categories whose outage plausibly affects patient care even when the caller did not say so.
CLINICAL_CATEGORIES = {"eClinicalWorks"}

_BASIS_FIELD = {
    "patient_care": facts_mod.PATIENT_CARE,
    "site_wide": facts_mod.SCOPE,
    "team_blocked": facts_mod.WORK_BLOCKED,
    "caller_blocked": facts_mod.WORK_BLOCKED,
    "caller_working": facts_mod.WORK_BLOCKED,
    "others_affected": facts_mod.WORK_BLOCKED,
}


def assess_facts(
    *,
    model_priority: Priority | None,
    facts: Mapping[str, Fact],
    category: str | None = None,
    patient_context: bool = False,
) -> Assessment:
    """`assess` over three-state facts, plus what is still unverified.

    The rules themselves are unchanged: true boosts, false caps, and unknown does
    neither (the model's rating, which strict extraction asks for on stated impact
    only, stands). What is new is saying so: an unknown that matters is listed in
    `unverified`, a clinical issue with an unresolved safety fact sets `needs_triage`,
    and `basis` quotes the caller's words behind the rule that decided the priority.
    """
    # A fact below the confidence threshold counts as unknown: it neither boosts nor caps.
    work, patient, scope = (
        fact if fact.is_trusted() else Fact.unknown(fact.field, UnknownReason.GATE_REJECTED, source=fact.source)
        for fact in (facts[facts_mod.WORK_BLOCKED], facts[facts_mod.PATIENT_CARE], facts[facts_mod.SCOPE])
    )

    base = assess(
        model_priority=model_priority,
        work_blocked=work.value,
        scope=scope.value,
        patient_care=patient.value is True,
    )

    # Patients blocked, or the whole site down, already fix the priority: how the caller's
    # own work is going no longer matters, so it is not "unverified".
    decided = patient.value is True or scope.value == "whole_site"
    clinical = patient_context or category in CLINICAL_CATEGORIES
    unverified = []
    if not work.is_known and not decided:
        unverified.append("work_impact")
    if not patient.is_known and clinical:
        unverified.append("patient_impact")

    evidence_fact = {facts_mod.WORK_BLOCKED: work, facts_mod.PATIENT_CARE: patient, facts_mod.SCOPE: scope}.get(
        _BASIS_FIELD.get(base.rule, "")
    )
    basis = f'caller said "{evidence_fact.evidence}"' if evidence_fact and evidence_fact.evidence else None

    return replace(base, unverified=tuple(unverified), needs_triage=clinical and bool(unverified), basis=basis)


def triage_note(assessment: Assessment) -> str | None:
    """The first line of a ticket whose clinical safety facts were never settled."""
    if not assessment.needs_triage:
        return None
    parts = []
    if "patient_impact" in assessment.unverified:
        parts.append("patient impact not confirmed")
    if "work_impact" in assessment.unverified:
        parts.append("work impact not confirmed")
    return "NEEDS TRIAGE REVIEW - " + ", ".join(parts) + "."

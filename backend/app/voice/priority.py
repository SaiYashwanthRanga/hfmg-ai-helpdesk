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

from dataclasses import dataclass

from app.db.models import Priority

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

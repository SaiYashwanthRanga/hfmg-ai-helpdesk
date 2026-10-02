"""Three-state facts about a call, with the evidence behind each one.

Priority depends on three facts the caller may or may not have stated:
`work_blocked`, `patient_care_affected` and `affected_scope`. Each is

    true / false / unknown   (scope: one_person / several_people / whole_site / unknown)

and `unknown` is a real answer, never a coerced false. A fact carries where it
came from (`source`), the caller's own words (`evidence`), how sure we are
(`confidence`) and, when unknown, why (`reason`).

Storage is the existing `collected` JSON column, so there is no migration:

    collected["facts"][name] = {...fact as a dict...}
    collected[name]          = True / False / None   (the legacy mirror)

The mirror keeps every existing reader (`priority.assess`, the ticket text, the
simulator, the tests) working unchanged. `apply_fact` writes both.

Precedence (see `merge`): a direct answer to a question beats what the caller
volunteered; a later explicit statement beats an earlier one at the same
level; an unknown never overwrites a known value.
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger("hfmg.voice.facts")

#: A fact is trusted (used to change priority) only at or above this confidence.
ACCEPT_THRESHOLD = 0.8

WORK_BLOCKED = "work_blocked"
PATIENT_CARE = "patient_care_affected"
SCOPE = "affected_scope"
FIELDS = (WORK_BLOCKED, PATIENT_CARE, SCOPE)
BOOLEAN_FIELDS = (WORK_BLOCKED, PATIENT_CARE)
SCOPE_VALUES = ("one_person", "several_people", "whole_site")

FACTS_KEY = "facts"


class Source(str, Enum):
    LEGACY = "legacy"            # rebuilt from a plain value with no record of where it came from
    DESCRIPTION = "description"  # volunteered while describing the problem
    DETAILS = "details"          # said in reply to the "when did it start / can you work" question
    ANSWER = "answer"            # a direct answer to a clarification question
    CORRECTION = "correction"    # the caller changed it while the ticket was read back


#: Higher beats lower. Equal rank: the later statement wins.
SOURCE_RANK = {
    Source.LEGACY: 0,
    Source.DESCRIPTION: 1,
    Source.DETAILS: 2,
    Source.ANSWER: 3,
    Source.CORRECTION: 4,
}


class UnknownReason(str, Enum):
    NOT_STATED = "not_stated"                    # the caller said nothing about it
    IMPLIED_UNCONFIRMED = "implied_unconfirmed"  # it sounded likely, but nothing explicit
    ASKED_UNCLEAR = "asked_unclear"              # we asked and the answer did not settle it
    CALLER_UNSURE = "caller_unsure"              # we asked and the caller does not know
    GATE_REJECTED = "gate_rejected"              # the quote did not state the fact
    VERIFIER_REJECTED = "verifier_rejected"      # the verifier would not confirm the quote
    CONFLICTING = "conflicting"                  # the caller said both
    UNGROUNDED = "ungrounded"                    # the claimed quote is not in what the caller said


@dataclass(frozen=True)
class Fact:
    field: str
    value: bool | str | None
    source: Source = Source.DESCRIPTION
    confidence: float = 0.0
    evidence: str | None = None
    reason: UnknownReason | None = None

    def __post_init__(self) -> None:
        if self.field not in FIELDS:
            raise ValueError(f"unknown fact field: {self.field!r}")
        if self.value is not None:
            if self.field in BOOLEAN_FIELDS and not isinstance(self.value, bool):
                raise ValueError(f"{self.field} must be True, False or None, got {self.value!r}")
            if self.field == SCOPE and self.value not in SCOPE_VALUES:
                raise ValueError(f"{self.field} must be one of {SCOPE_VALUES} or None, got {self.value!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be between 0 and 1, got {self.confidence}")

    # --- construction ---------------------------------------------------------------

    @classmethod
    def known(
        cls, field: str, value: bool | str, *, source: Source = Source.DESCRIPTION,
        confidence: float = 1.0, evidence: str | None = None,
    ) -> "Fact":
        if value is None:
            raise ValueError("a known fact needs a value; use Fact.unknown")
        return cls(field, value, source, confidence, evidence, None)

    @classmethod
    def unknown(
        cls, field: str, reason: UnknownReason = UnknownReason.NOT_STATED, *,
        source: Source = Source.DESCRIPTION, confidence: float = 0.0, evidence: str | None = None,
    ) -> "Fact":
        return cls(field, None, source, confidence, evidence, reason)

    # --- reading --------------------------------------------------------------------

    @property
    def is_known(self) -> bool:
        return self.value is not None

    @property
    def state(self) -> str:
        """"true" / "false" / "unknown" for the boolean facts; the scope value or "unknown"."""
        if self.value is None:
            return "unknown"
        if isinstance(self.value, bool):
            return "true" if self.value else "false"
        return self.value

    def is_trusted(self, threshold: float = ACCEPT_THRESHOLD) -> bool:
        return self.is_known and self.confidence >= threshold

    # --- (de)serialisation ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "state": self.state,
            "source": self.source.value,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "reason": self.reason.value if self.reason else None,
        }

    @classmethod
    def from_dict(cls, field: str, data: dict[str, Any]) -> "Fact":
        reason = data.get("reason")
        return cls(
            field=field,
            value=data.get("value"),
            source=Source(data.get("source") or Source.LEGACY.value),
            confidence=float(data.get("confidence") or 0.0),
            evidence=data.get("evidence"),
            reason=UnknownReason(reason) if reason else None,
        )


def merge(current: Fact | None, new: Fact) -> Fact:
    """Combine an existing fact with a newly extracted one. Pure; returns the fact to keep.

    - Nothing yet: take the new one.
    - New is unknown: never overwrite a known value. Between two unknowns keep
      the one from the higher-ranked source (the later one on a tie).
    - New is known: it replaces an unknown, and replaces a known value of the
      same or lower source rank. A lower-ranked statement never overrides a
      higher-ranked one (a description cannot undo what the caller answered).
    """
    if current is None:
        return new
    if current.field != new.field:
        raise ValueError(f"cannot merge {current.field} with {new.field}")

    new_rank, cur_rank = SOURCE_RANK[new.source], SOURCE_RANK[current.source]
    if not new.is_known:
        if current.is_known:
            return current
        return new if new_rank >= cur_rank else current
    if not current.is_known:
        return new
    return new if new_rank >= cur_rank else current


def conflicts(current: Fact | None, new: Fact) -> bool:
    """True when two *known* facts disagree. Worth logging, whichever one wins."""
    return bool(current and current.is_known and new.is_known and current.value != new.value)


# --- the `collected` mapping ---------------------------------------------------------


def get_fact(collected: dict, field: str) -> Fact:
    """The fact for `field`, rebuilt from the legacy value if there is no record.

    A legacy `work_blocked` or scope is a known value. A legacy
    `patient_care_affected` of False only ever meant "not asserted" (the old
    extraction could not tell "no" from "not said"), so it reads as unknown.
    """
    record = (collected.get(FACTS_KEY) or {}).get(field)
    if record:
        return Fact.from_dict(field, record)

    value = collected.get(field)
    if value is None:
        return Fact.unknown(field, source=Source.LEGACY)
    if field == PATIENT_CARE and value is False:
        return Fact.unknown(field, source=Source.LEGACY)
    if field == SCOPE and value not in SCOPE_VALUES:
        return Fact.unknown(field, source=Source.LEGACY)
    return Fact.known(field, value, source=Source.LEGACY, confidence=1.0)


def all_facts(collected: dict) -> dict[str, Fact]:
    return {field: get_fact(collected, field) for field in FIELDS}


def apply_fact(collected: dict, new: Fact) -> dict:
    """Merge `new` into `collected` and return a new dict (the input is not changed).

    Writes the full fact under `facts` and its plain value to the legacy key.
    Assigning the returned dict to the session is what makes SQLAlchemy see
    the change to the JSON column.
    """
    existing = (collected.get(FACTS_KEY) or {}).get(new.field)
    current = get_fact(collected, new.field) if (existing or collected.get(new.field) is not None) else None
    merged = merge(current, new)
    if conflicts(current, new):
        # Two explicit statements disagree: keep an audit trail of which one won and why.
        logger.info(
            "fact %s: %s (%s) vs %s (%s) -> kept %s",
            new.field, current.state, current.source.value, new.state, new.source.value, merged.state,
        )
    facts = {**(collected.get(FACTS_KEY) or {}), new.field: merged.to_dict()}
    return {**collected, FACTS_KEY: facts, new.field: merged.value}


def apply_facts(collected: dict, facts: "list[Fact] | tuple[Fact, ...]") -> dict:
    for fact in facts:
        collected = apply_fact(collected, fact)
    return collected


# --- grounding: is the quoted evidence really in what the caller said? ---------------

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "‛": "'", "`": "'", "´": "'"})
_NON_WORD = re.compile(r"[^a-z0-9' ]+")


def normalize_text(text: str) -> str:
    """Lower-case, straighten apostrophes, drop punctuation, collapse spaces."""
    text = (text or "").translate(_APOSTROPHES).lower()
    text = _NON_WORD.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_grounded(utterance: str, quote: str | None) -> bool:
    """True when `quote` really appears in `utterance` (ignoring case and punctuation).

    The model must copy the caller's words as evidence; a quote it made up or
    paraphrased is not evidence. A quote with "..." in it is several pieces that
    must each appear, in order.
    """
    if not quote or not quote.strip():
        return False
    haystack = normalize_text(utterance)
    position = 0
    pieces = [p for p in re.split(r"\.{2,}|…", quote) if p.strip()]
    if not pieces:
        return False
    for piece in pieces:
        needle = normalize_text(piece)
        if len(needle) < 3:
            return False
        found = haystack.find(needle, position)
        if found < 0:
            return False
        position = found + len(needle)
    return True

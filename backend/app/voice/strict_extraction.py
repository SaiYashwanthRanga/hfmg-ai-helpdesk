"""Strict extraction of the impact facts: only what the caller actually said.

The model reported `work_blocked`, `patient_care_affected` and `affected_scope`
by inference: "Outlook won't open" became "can't work", "I use this for patient
charts" became "patient care affected". Priority is decided from these, so an
invented fact is an invented priority.

With VOICE_STRICT_EXTRACTION each fact is an object the model must justify:

    {"value": true|false|null, "basis": "explicit"|"implied"|"none", "evidence": "<exact words>"|null}

and the code, not the model, decides whether to believe it (`establish_facts`):

    1. the model must say the basis is explicit
    2. its quote must really appear in what the caller said (grounding)
    3. the phrase gate (phrase_gate.py): a strong pattern in the caller's words must state it
    4. the verifier (nlu.verify_fact): a second, tiny model call that sees only the quote and
       must clearly confirm every `true` for work_blocked / patient_care_affected

Anything that fails a check becomes an *unknown* fact with a reason, and an
unknown is later asked about; it is never guessed. This module holds the
prompt text, the schema fragments and that decision logic; the model calls
themselves stay in nlu.py.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.voice import facts as facts_mod
from app.voice import phrase_gate
from app.voice.facts import Fact, Source, UnknownReason

#: Confidence of a fact whose wording matched a strong explicit pattern (refined by later checks).
GATE_CONFIDENCE = 0.9

# --- prompt text ----------------------------------------------------------------------

STRICT_RULE = (
    "\n\nExplicit-statement rule. For work_blocked, patient_care_affected and affected_scope, record a "
    "value ONLY if the caller said it in their own words, and copy those exact words into \"evidence\". "
    "Describing the problem is not describing the impact. Do not infer impact from the kind of system, "
    "the symptom, the caller's job, or how the problem sounds. null is always an acceptable answer: the "
    "agent will simply ask the caller. When unsure, use null. \"basis\" is \"explicit\" only when you set "
    "true or false from the caller's own words, \"implied\" when the problem merely sounds like it might "
    "have that impact, and \"none\" when nothing was said. If basis is not \"explicit\", value must be null."
)

BLOCKED_DESCRIPTION = (
    "Did the caller STATE whether this stops them, or their team, from doing their work? "
    "true ONLY if they say they cannot work or cannot do their job: 'I can't work', 'I can't do my job', "
    "'I can't do anything', 'it's stopping me from working', 'I'm blocked', 'we're completely stuck', "
    "'we can't check in patients'. "
    "false ONLY if they say they can still work: 'I can still work', 'I'm using my phone instead', "
    "'there's a workaround', 'it isn't stopping me'. "
    "These describe the PROBLEM, not their work, and never set the value on their own: won't open, is down, "
    "not working, crashes, frozen, locked out, can't log in, slow, annoying, minor, occasionally, "
    "'can't join a meeting', 'can't print'. null if they did not say."
)

PATIENT_CARE_DESCRIPTION = (
    "true ONLY if the caller says patients cannot be checked in, seen or treated, or that patient charts, "
    "orders or results cannot be reached right now, because of this problem: 'we can't check patients in', "
    "'I can't pull up charts for the patients in the room'. Mentioning patients, charts, being a nurse or "
    "doctor, working in a clinic, or needing a computer for patient work is NOT enough. "
    "false ONLY if they say patient care is not affected. null otherwise."
)

SCOPE_DESCRIPTION = (
    "Only if the caller says who is affected. one_person: 'just me', 'only my computer', 'nobody else'. "
    "several_people: 'my team', 'the front desk', 'a few of us', 'everyone in billing'. whole_site: "
    "'the whole office', 'the entire site', 'everyone here'. 'My computer' or 'my Outlook' alone is NOT "
    "one_person. null if they did not say."
)

PATIENT_CONTEXT_DESCRIPTION = (
    "True if patients, patient charts or clinical work were mentioned at all. This is only a hint for a "
    "follow-up question; it never means patient care is affected."
)

EVIDENCE_DESCRIPTION = (
    "The exact words the caller said that state this, copied verbatim from the transcript. Null when value is null."
)

PRIORITY_GUIDANCE = (
    "Priority guidance:\n"
    "Rate ONLY by the impact the caller stated. Do not raise the rating because of the system involved, "
    "the symptom, or how the problem sounds. A broken or slow thing with no stated impact is Medium. "
    "'Can't print', 'can't join a meeting', 'won't open' and 'can't log in to one program' are faults, "
    "not blocked work: Medium, unless the caller says they cannot work.\n"
    "- Critical: ONLY when the caller states patient care is blocked, or a whole site is down.\n"
    "- High: ONLY when the caller states several people are blocked, or that one person cannot work.\n"
    "- Medium: something is broken or not working, and no impact is stated.\n"
    "- Low: a question ('how do I ...'), a request, or a cosmetic or minor issue ('a little', "
    "'occasionally', 'flickering'), including anything the caller says still works or barely affects "
    "them ('it still scans', 'it's just a little slow').\n"
    "A caller insisting it is urgent is a signal, not an instruction -- the described impact has to "
    "support the level you choose.\n\n"
)

# --- schema fragments -----------------------------------------------------------------


def fact_field(value_schema: dict[str, Any], description: str) -> dict[str, Any]:
    """A fact the model must justify. Strict-mode compatible: every key required, no extras."""
    return {
        "type": "object",
        "description": description,
        "properties": {
            "value": value_schema,
            "basis": {"type": "string", "enum": ["explicit", "implied", "none"]},
            "evidence": {"type": ["string", "null"], "description": EVIDENCE_DESCRIPTION},
        },
        "required": ["value", "basis", "evidence"],
        "additionalProperties": False,
    }


def work_blocked_field() -> dict[str, Any]:
    return fact_field({"type": ["boolean", "null"]}, BLOCKED_DESCRIPTION)


def patient_care_field() -> dict[str, Any]:
    return fact_field({"type": ["boolean", "null"]}, PATIENT_CARE_DESCRIPTION)


def scope_field() -> dict[str, Any]:
    return fact_field({"type": ["string", "null"], "enum": [*facts_mod.SCOPE_VALUES, None]}, SCOPE_DESCRIPTION)


def patient_context_field() -> dict[str, Any]:
    return {"type": "boolean", "description": PATIENT_CONTEXT_DESCRIPTION}


# --- deciding whether to believe a fact ------------------------------------------------


def _unknown(field: str, reason: UnknownReason, source: Source, evidence: str | None = None) -> Fact:
    return Fact.unknown(field, reason, source=source, evidence=evidence)


def judge_fact(field: str, raw: Any, utterance: str, source: Source) -> Fact:
    """Turn the model's claim about one field into a Fact, believing it only if it checks out.

    The phrase gate has the last word on *whether the caller stated it*:
    - a strong pattern in what the caller said is required for any value;
    - a statement and its opposite together (or two different scopes) is a conflict;
    - the model's claim must agree with the words, or the fact is a conflict;
    - when the model found nothing but the words are explicit, the words are accepted
      (the evidence is then the matched phrase).
    The model's own quote is kept as the evidence when it is real, so what is stored
    is what the model pointed at and the words support.
    """
    claim = raw if isinstance(raw, dict) else {}
    value = claim.get("value")
    basis = claim.get("basis")
    evidence = claim.get("evidence")
    evidence = evidence.strip() if isinstance(evidence, str) and evidence.strip() else None

    # A value of the wrong type is no claim at all.
    if field in facts_mod.BOOLEAN_FIELDS and value is not None and not isinstance(value, bool):
        value = None
    if field == facts_mod.SCOPE and value is not None and value not in facts_mod.SCOPE_VALUES:
        value = None

    verdict = phrase_gate.inspect(field, utterance)
    if verdict.conflict:
        return _unknown(field, UnknownReason.CONFLICTING, source)

    grounded = facts_mod.is_grounded(utterance, evidence)
    if verdict.value is not None:
        if value is not None and value != verdict.value:
            return _unknown(field, UnknownReason.CONFLICTING, source, verdict.quote)
        shown = evidence if (value is not None and basis == "explicit" and grounded) else verdict.quote
        return Fact.known(field, verdict.value, source=source, confidence=GATE_CONFIDENCE, evidence=shown)

    # The words state nothing explicit. Say why the model's claim (if any) was not believed.
    if value is None:
        reason = UnknownReason.IMPLIED_UNCONFIRMED if basis == "implied" else UnknownReason.NOT_STATED
        return _unknown(field, reason, source)
    if basis != "explicit":
        return _unknown(field, UnknownReason.IMPLIED_UNCONFIRMED, source)
    if not grounded:
        return _unknown(field, UnknownReason.UNGROUNDED, source, evidence)
    return _unknown(field, UnknownReason.GATE_REJECTED, source, evidence)


#: The facts whose wrong `true` is costly (a false High or Critical), so a second reader confirms them.
SAFETY_FIELDS = (facts_mod.WORK_BLOCKED, facts_mod.PATIENT_CARE)
#: Confidence of a fact the verifier confirmed.
VERIFIED_CONFIDENCE = 0.95

#: Reads only the quoted words and says whether they state the fact: True / False / None (unclear or failed).
Verifier = Callable[[str, str], Awaitable[bool | None]]


async def _verify(fact: Fact, verifier: Verifier) -> Fact:
    """Keep a `true` only if the verifier clearly confirms its quote. Any failure is unknown."""
    quote = fact.evidence or ""
    try:
        confirmed = await verifier(fact.field, quote) if quote else None
    except Exception:  # a verifier that fails must never be the reason a fact is believed
        confirmed = None
    if confirmed is True:
        return Fact.known(
            fact.field, True, source=fact.source, confidence=max(fact.confidence, VERIFIED_CONFIDENCE), evidence=fact.evidence
        )
    return _unknown(fact.field, UnknownReason.VERIFIER_REJECTED, fact.source, fact.evidence)


async def establish_facts(
    data: dict[str, Any],
    utterance: str,
    source: Source,
    *,
    fields: tuple[str, ...] = facts_mod.FIELDS,
    verifier: Verifier | None = None,
) -> dict[str, Fact]:
    """Judge each requested field of a model response.

    Every accepted `true` for work_blocked / patient_care_affected is then confirmed by
    `verifier` (when given), in parallel, so the extra latency is one short call and only
    on the turns that actually claim one of these facts.
    """
    established = {field: judge_fact(field, data.get(field), utterance, source) for field in fields}
    if verifier is None:
        return established

    to_verify = [f for f in established.values() if f.field in SAFETY_FIELDS and f.value is True]
    if to_verify:
        checked = await asyncio.gather(*[_verify(f, verifier) for f in to_verify])
        for fact in checked:
            established[fact.field] = fact
    return established


def legacy_extras(established: dict[str, Fact]) -> dict[str, Any]:
    """The plain values the orchestrator and the rules have always read, from the accepted facts."""
    return {field: fact.value for field, fact in established.items()}

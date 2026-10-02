"""Deterministic stand-in for a model provider: LLM_PROVIDER=fake.

For load-testing the AI Call Simulator and demos without an API key. It
answers the voice NLU schemas with keyword rules and sleeps a configurable
latency, so a load test measures the platform (database, locking, pool)
rather than model quality. Refused when ENVIRONMENT=production -- see
app/llm/factory.py.

It is not a second agent: the orchestrator, prompts and state machine are
unchanged; only the model behind `structured()` is swapped.
"""

import asyncio
import re
import time
from typing import Any

from app.core import trace
from app.core.config import get_settings

settings = get_settings()

_SPEECH_RE = re.compile(r"<caller_speech>\n(.*?)\n</caller_speech>", re.DOTALL)

_CATEGORY_KEYWORDS = (
    ("Password", ("locked out", "lockout", "password", "mfa", "reset")),
    ("eClinicalWorks", ("eclinicalworks", "ecw", "chart", "ehr")),
    ("Microsoft 365", ("outlook", "teams", "excel", "word", "onedrive", "sharepoint", "email")),
    ("Network", ("internet", "wifi", "vpn", "network", "drive")),
    ("Printer", ("print", "scanner", "label")),
)
# Mirrors nlu.py's guidance: Critical is a whole site down or patient care
# blocked; a whole department is "multiple users" (High), not Critical.
_URGENT = ("whole office", "whole site", "entire", "can't check in", "down for the whole", "all patients")
_HIGH = ("can't do anything", "none of", "department", "locked out", "can't log in", "can't label")
_LOW = ("question", "how do i", "a little", "a bit", "still scans")
_HUMAN = ("real person", "talk to someone", "speak to someone", "a human", "representative", "operator")
_YES = ("yes", "yeah", "yep", "correct", "that's right", "sure")
_NO = ("no", "nope", "that's all", "that's everything", "nothing else", "wrong")


_STARTED_RE = re.compile(
    r"(this morning|yesterday(?: evening| afternoon)?|today|since \w+|last week|an hour ago|\w+ minutes ago|a couple of days)"
)


def _started(text: str) -> str | None:
    match = _STARTED_RE.search(text)
    return match.group(1) if match else None


def _blocked(text: str) -> bool | None:
    if _has(text, ("can't work", "cannot work", "can't do anything", "can't get in", "stuck", "can't check")):
        return True
    if _has(text, ("can still work", "i can work", "still working", "workaround", "using the")):
        return False
    return None


def _speech(user: str) -> str:
    match = _SPEECH_RE.search(user)
    return (match.group(1) if match else user).strip()


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


_BLOCKED_PHRASES = (
    "can't work", "cannot work", "can't do my job", "cannot do my job", "can't do anything", "cannot do anything",
    "not able to do any work", "unable to work", "i'm blocked", "i am blocked", "completely stuck", "i'm stuck",
    "is stuck", "stopping me from working", "can't check in any patients", "can't check anyone in",
)
_CAN_WORK_PHRASES = ("can still work", "i can work", "using my phone", "workaround", "isn't stopping me", "paper charts")
_IMPLIED_BLOCK = ("won't open", "won't start", "can't log in", "locked out", "is down", "not working")
_PATIENT_BLOCKED = ("can't check in any patients", "can't check in patients", "can't check anyone in", "can't see patients",
                    "can't open patient charts", "cannot check in")
_SCOPE_PHRASES = (
    ("whole_site", ("whole office", "whole site", "entire site", "entire office", "everyone in the office")),
    ("several_people", ("my team", "front desk is", "whole front desk", "the department", "billing department", "nobody in billing", "all of us")),
    ("one_person", ("just me", "only my computer", "only me")),
)


def _quote(speech: str, phrases: tuple[str, ...]) -> str | None:
    """The first phrase present in `speech`, with the caller's own capitalisation."""
    lowered = speech.lower().replace("\u2019", "'")
    for phrase in phrases:
        at = lowered.find(phrase)
        if at >= 0:
            return speech[at:at + len(phrase)]
    return None


def _fact(value: Any, basis: str, evidence: str | None) -> dict[str, Any]:
    return {"value": value, "basis": basis, "evidence": evidence}


def _strict_blocked(speech: str) -> dict[str, Any]:
    """Explicit statements only -- what the strict prompt asks of a real model."""
    can = _quote(speech, _CAN_WORK_PHRASES)
    cannot = _quote(speech, _BLOCKED_PHRASES)
    if cannot and not can:
        return _fact(True, "explicit", cannot)
    if can and not cannot:
        return _fact(False, "explicit", can)
    if _has(speech.lower(), _IMPLIED_BLOCK):
        return _fact(None, "implied", None)
    return _fact(None, "none", None)


def _strict_patient_care(speech: str) -> dict[str, Any]:
    hit = _quote(speech, _PATIENT_BLOCKED)
    return _fact(True, "explicit", hit) if hit else _fact(None, "none", None)


def _strict_scope(speech: str) -> dict[str, Any]:
    for value, phrases in _SCOPE_PHRASES:
        hit = _quote(speech, phrases)
        if hit:
            return _fact(value, "explicit", hit)
    return _fact(None, "none", None)


def _clarification(lowered: str, choices: list[str]) -> str:
    """Keyword stand-in for the clarification interpreter."""
    if "just_me" in choices:  # the scope question
        if _has(lowered, ("whole office", "whole site", "everyone in the office", "entire")):
            return "whole_site"
        if _has(lowered, ("just me", "only me", "only happening to me", "nobody else")):
            return "just_me"
        if _has(lowered, ("team", "others", "everyone", "a few of us", "other people", "billing")):
            return "others"
    else:
        if _has(lowered, ("don't know", "not sure", "no idea")):
            return "unsure"
        if _has(lowered, ("can still work", "i can work", "workaround", "not stopping", "paper", "not held up")):
            return "no"
        if _has(lowered, ("partly", "sometimes", "kind of")):
            return "partly"
        if _has(lowered, ("stopping me", "can't work", "cannot work", "can't do anything", "can't check", "yes", "yeah")):
            return "yes"
        if _has(lowered, ("no", "nope")):
            return "no"
    if _has(lowered, ("don't know", "not sure", "no idea")):
        return "unsure"
    return "unclear"


def _answer(schema_name: str, speech: str, strict: bool = False, choices: list[str] | None = None) -> dict[str, Any]:
    lowered = speech.lower()
    if schema_name in ("verify_work_blocked", "verify_patient_care_affected"):
        # Reads only the quoted words, like the real verifier: yes when they state the fact.
        phrases = _BLOCKED_PHRASES if schema_name == "verify_work_blocked" else _PATIENT_BLOCKED
        return {"answer": "yes" if _quote(speech, phrases) else "no"}
    if schema_name == "record_clarification":
        answer = _clarification(lowered, choices or [])
        return {"escalation_requested": _has(lowered, _HUMAN), "answer": answer, "unable_to_determine": answer == "unclear"}
    wants_human = _has(lowered, _HUMAN)
    empty = not lowered.strip()

    if schema_name == "record_issue":
        category = next((name for name, words in _CATEGORY_KEYWORDS if _has(lowered, words)), "Other")
        priority = (
            "Critical" if _has(lowered, _URGENT)
            else "High" if _has(lowered, _HIGH)
            else "Low" if _has(lowered, _LOW)
            else "Medium"
        )
        is_description = not empty and not wants_human and len(lowered.split()) >= 3
        return {
            "escalation_requested": wants_human,
            "is_problem_description": is_description,
            "description": speech if is_description else None,
            "short_issue": category if is_description else None,
            "category": category if is_description else None,
            "priority": priority if is_description else None,
            "patient_care_affected": True if "check in" in lowered or "patients" in lowered else False,
            "caller_name_confidence": None,
            "category_confidence": "high" if category != "Other" else "medium",
            "affected_scope": "several_people" if priority in ("Critical", "High") else "one_person",
            "caller_name": None,
            "department": None,
            "started": _started(lowered),
            "work_blocked": _blocked(lowered),
            "unable_to_determine": not is_description,
            **(
                {
                    "work_blocked": _strict_blocked(speech),
                    "patient_care_affected": _strict_patient_care(speech),
                    "affected_scope": _strict_scope(speech),
                    "patient_context_mentioned": _has(lowered, ("patient", "chart")),
                }
                if strict
                else {}
            ),
        }
    if schema_name == "record_details":
        started, blocked = _started(lowered), _blocked(lowered)
        if strict:
            strict_blocked = _strict_blocked(speech)
            return {
                "escalation_requested": wants_human,
                "started": started,
                "work_blocked": strict_blocked,
                "patient_care_affected": _strict_patient_care(speech),
                "affected_scope": _strict_scope(speech),
                "unable_to_determine": started is None and strict_blocked["value"] is None,
            }
        return {
            "escalation_requested": wants_human,
            "started": started,
            "work_blocked": blocked,
            "affected_scope": None,
            "unable_to_determine": started is None and blocked is None,
        }
    if schema_name == "record_name_fix":
        return {"escalation_requested": wants_human, "corrected_name": None, "unable_to_determine": True}
    if schema_name == "record_correction":
        dept = re.search(r"department (?:is|was) ([a-z ]+)", lowered)
        agrees = _has(lowered, ("that's fine", "go ahead", "that is fine"))
        return {
            "escalation_requested": wants_human,
            "nothing_to_change": agrees,
            "caller_name": None,
            "department": dept.group(1).strip().title() if dept else None,
            "issue": None,
            "issue_details": None,
            "started": _started(lowered),
            "work_blocked": _strict_blocked(speech) if strict else _blocked(lowered),
            "phone_number": None,
            "category": None,
            "unable_to_determine": not (dept or agrees or _started(lowered)),
        }
    if schema_name == "record_name":
        body = re.sub(r"^(hi,?\s*)?(this is|my name is|it's|i'm)\s+", "", speech, flags=re.IGNORECASE).strip(" .")
        name, _, rest = body.partition(",")
        department = re.sub(r"^\s*(i'm |i am )?(in |from |with )?(the )?", "", rest, flags=re.IGNORECASE).strip(" .") or None
        name = name.strip(" .")
        return {
            "escalation_requested": wants_human,
            "name": name or None,
            "name_confidence": "high",
            "department": department,
            "unable_to_determine": not name,
        }
    if schema_name == "record_phone":
        digits = "".join(c for c in speech if c.isdigit())
        return {"escalation_requested": wants_human, "phone_number": digits or None, "unable_to_determine": not digits}
    if schema_name == "record_email":
        declined = _has(lowered, ("skip", "no email", "don't have"))
        return {
            "escalation_requested": wants_human,
            "declined": declined,
            "email": None if declined else speech,
            "unable_to_determine": empty,
        }
    if schema_name == "record_answer":
        answer = True if _has(lowered, _YES) else False if _has(lowered, _NO) else None
        return {"escalation_requested": wants_human, "answer": answer, "unable_to_determine": answer is None}
    if schema_name == "ticket_summary":
        return {"summary": f"[Simulated summary] Caller reported: {speech[:200]}"}
    return {"unable_to_determine": True}


class FakeProvider:
    name = "fake"

    @property
    def is_configured(self) -> bool:
        return True

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        timeout: float | None = None,
        max_retries: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any] | None:
        started = time.perf_counter()
        await asyncio.sleep(settings.fake_llm_latency_ms / 1000)
        strict = (schema.get("properties", {}).get("work_blocked", {}) or {}).get("type") == "object"
        choices = (schema.get("properties", {}).get("answer", {}) or {}).get("enum")
        output = _answer(schema_name, _speech(user), strict=strict, choices=choices)
        trace.record_llm_call(
            kind="structured",
            schema_name=schema_name,
            started=started,
            system=system,
            user=user,
            output=output,
            error=None,
            attempts=1,
        )
        return output

    async def text(
        self,
        *,
        system: str,
        user: str,
        max_output_tokens: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> str | None:
        started = time.perf_counter()
        await asyncio.sleep(settings.fake_llm_latency_ms / 1000)
        output = "Simulated summary: caller reported an IT issue; see description."
        trace.record_llm_call(
            kind="text", schema_name=None, started=started, system=system, user=user,
            output=output, error=None, attempts=1,
        )
        return output

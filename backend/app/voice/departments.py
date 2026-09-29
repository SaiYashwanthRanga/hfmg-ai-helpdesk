"""Department validation.

Callers name their department in free speech ("radiology", "the front desk",
"AI Director", "Development"). This maps what was said onto HFMG's real
departments when it can, and flags it as unverified when it can't, so a
made-up or misheard department is read back and marked on the ticket instead
of being silently accepted.

IMPORTANT: DEFAULT_DEPARTMENTS is a starting point written without access to
HFMG's org chart. Set VOICE_DEPARTMENTS (comma-separated) to the real list.
"""

import difflib
import re
from dataclasses import dataclass

from app.core.config import get_settings

DEFAULT_DEPARTMENTS = [
    "Administration", "Accounts Payable", "Behavioral Health", "Billing", "Cardiology", "Family Medicine",
    "Finance", "Front Desk", "Human Resources", "IT", "Laboratory", "Medical Records", "Nursing",
    "Pediatrics", "Pharmacy", "Radiology", "Reception", "Referrals", "Scheduling", "Urgent Care",
]

#: Ways people say a department, mapped to the canonical name.
ALIASES = {
    "hr": "Human Resources", "human resource": "Human Resources", "personnel": "Human Resources",
    "it": "IT", "i t": "IT", "information technology": "IT", "tech support": "IT", "help desk": "IT",
    "lab": "Laboratory", "labs": "Laboratory", "x ray": "Radiology", "xray": "Radiology", "imaging": "Radiology",
    "ap": "Accounts Payable", "a p": "Accounts Payable", "payables": "Accounts Payable",
    "accounting": "Finance", "front": "Front Desk", "check in": "Front Desk", "check-in": "Front Desk",
    "records": "Medical Records", "medical record": "Medical Records", "peds": "Pediatrics",
    "pediatric": "Pediatrics", "family": "Family Medicine", "family practice": "Family Medicine",
    "mental health": "Behavioral Health", "behavioural health": "Behavioral Health", "admin": "Administration",
    "management": "Administration", "appointments": "Scheduling", "referral": "Referrals",
    "nurses": "Nursing", "nurse": "Nursing", "urgent": "Urgent Care",
}

_NOISE = re.compile(r"\b(the|a|an|our|my|department|dept|team|office|group|section|in|of)\b")


@dataclass(frozen=True)
class DepartmentMatch:
    name: str
    #: True when it matched HFMG's list (exactly, by alias, or by a near-miss spelling).
    verified: bool


def known_departments() -> list[str]:
    configured = [d.strip() for d in (get_settings().voice_departments or "").split(",") if d.strip()]
    return configured or DEFAULT_DEPARTMENTS


def _normalize(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", _NOISE.sub(" ", text)).strip()


def canonicalize(raw: str | None) -> DepartmentMatch | None:
    """Map free speech onto the department list. None when nothing was said."""
    cleaned = " ".join((raw or "").split())
    if not cleaned:
        return None
    key = _normalize(cleaned)
    departments = known_departments()
    by_key = {_normalize(d): d for d in departments}

    if key in by_key:
        return DepartmentMatch(by_key[key], True)
    if key in ALIASES and ALIASES[key] in departments:
        return DepartmentMatch(ALIASES[key], True)
    # A near-miss spelling ("billin"), not a different department: real
    # departments can look alike ("radiology"/"cardiology" score 0.84), so
    # the first letter must agree and the score must be high.
    close = [
        k for k in difflib.get_close_matches(key, list(by_key), n=3, cutoff=0.88) if k[:1] == key[:1]
    ]
    if close:
        return DepartmentMatch(by_key[close[0]], True)
    # "IT department", "billing office": a known department plus extra words.
    for candidate_key, candidate in by_key.items():
        if len(candidate_key) >= 4 and re.search(rf"\b{re.escape(candidate_key)}\b", key):
            return DepartmentMatch(candidate, True)
    return DepartmentMatch(cleaned[:1].upper() + cleaned[1:], False)

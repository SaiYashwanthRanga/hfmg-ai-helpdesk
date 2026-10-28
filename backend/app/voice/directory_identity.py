"""How an employee-directory lookup feeds a call, as plain data and deterministic rules.

A directory match is a proposal the caller must confirm, never proof of who is calling and
never authentication. Nothing here calls a model, and nothing logs what the directory holds.

The directory's Name column is a phone-system label ("1001 Exec First Last", "Billing First
Last", "Lab Slate Hill"), not a clean person name. The merged directory (data/directory_merged.csv)
adds the person's real name from their mailbox and says what each record is, so there the name is
the clean Display Name of a `person` record and nothing else is ever named (employee_name). On a
plain phone-system export the name is derived conservatively from the label (person_name). When
a name cannot be had with confidence the entry is treated as a shared or unclear line and the
agent simply asks who is calling.
"""

import re

from app.services.user_directory import DirectoryEntry, LookupResult, LookupStatus, clean_display_name
from app.voice import names

#: Words that mark a phone-system label (a room, a role, a department, a device), not a person.
LABEL_WORDS = frozenset({
    "room", "rooms", "lab", "labs", "desk", "front", "back", "nurse", "nurses", "nursing", "station", "exam",
    "reception", "receptionist", "kitchen", "hallway", "lunch", "office", "manager", "lead", "phone", "line",
    "user", "users", "test", "testing", "unused", "free", "available", "park", "call", "voicemail", "vm",
    "wireless", "private", "refills", "check", "checkin", "checkout", "in", "out", "main", "fax", "scanner",
    "conference", "pending", "extra", "number", "surg", "clinical", "team", "staff", "admin", "helpdesk",
    "it", "exec", "billing", "accounting", "outreach", "dr", "doctor", "md", "pa", "np", "dermatology",
    "pulmonology", "endoscopy", "hearing", "unknown", "cleanup", "locate", "hub", "aux", "global", "service",
})

_LEADING_EXTENSION = re.compile(r"^\s*\d+\s*[-–]?\s*")
_NAME_WORD = re.compile(r"^[A-Za-z][A-Za-z'’.\-]*$")
_SPLIT = re.compile(r"[^a-z0-9]+")
#: Account types of a record that is a person. A `likely_person` is a phone line whose label looks like a name but
#: that no mailbox backs up; everything else (shared line, call park, service, system, test, external) is not one.
_PERSON, _LIKELY_PERSON = "person", "likely_person"


def _context_words(entry: DirectoryEntry) -> set[str]:
    """Words of the entry's own location and teams: a label's department/site prefix is made of them."""
    text = f"{entry.location or ''} {' '.join(entry.teams)}".lower()
    return {word for word in _SPLIT.split(text) if word}


def _is_prefix_word(token: str, context: set[str]) -> bool:
    """A leading token that names the entry's site or team: equal to one of its words, or a
    run-together form of one ("CornwallOpth", "NWJensen")."""
    parts = [part for part in _SPLIT.split(token.lower()) if part]
    if parts and all(part in context for part in parts):
        return True
    word = token.lower()
    return len(word) >= 8 and any(
        len(c) >= 4 and (word.startswith(c) or word.endswith(c)) and len(word) - len(c) >= 2 for c in context
    )


def person_name(entry: DirectoryEntry) -> str | None:
    """"First Last" if the entry's label reduces to exactly that, else None.

    Strips a leading extension number, then leading words that belong to the entry's own site
    or team. Anything that still is not two plain words, or contains a label word, an initial
    or acronym, or a digit, is not presented as a person's name. A three-part real name is
    therefore not proposed either: the safe direction is to ask.
    """
    tokens = _LEADING_EXTENSION.sub("", entry.name or "").replace(",", " ").split()
    context = _context_words(entry)
    while len(tokens) > 2 and _is_prefix_word(tokens[0], context):
        tokens.pop(0)
    if len(tokens) != 2:
        return None
    for token in tokens:
        bare = token.rstrip(".")
        if not _NAME_WORD.match(token) or len(bare) < 2:
            return None
        if bare.lower() in LABEL_WORDS or (bare.isupper() and len(bare) <= 3):
            return None
        if _is_prefix_word(token, context):  # a site or team word left over: "Slate Hill Lab", "CornwallOpth Maxwell"
            return None
    return " ".join(tokens)


def employee_name(entry: DirectoryEntry) -> str | None:
    """The name the agent may say aloud for a directory entry, or None (ask who is calling).

    The merged directory says what every record is. A `person` is named by its clean Display Name, never by the
    phone-system label. A `likely_person` has no mailbox to confirm it, so the conservative label rule applies, as it
    does to every entry of a plain phone-system export (which has no account type). A shared line, call park,
    service, system, test or external record is never a person. Whether the record is live is the caller's check."""
    if entry.account_type is None or entry.account_type == _LIKELY_PERSON:
        return person_name(entry)
    if entry.account_type == _PERSON:
        return clean_display_name(entry.display_name)
    return None


def with_first_name(entries: list[DirectoryEntry], first_name: str | None) -> list[DirectoryEntry]:
    """The entries (people of the merged directory, found by last name) whose clean display name starts with this
    first name, allowing a recognizer's slip. Used to pick one of several people who share a last name."""
    heard = names.letters(first_name)
    if not heard:
        return []
    return [
        e for e in entries
        if e.display_name and names.same_name_part(heard, names.letters(e.display_name.split()[0]))
    ]


def with_extension(entries: list[DirectoryEntry], extension: str | None) -> list[DirectoryEntry]:
    """The entries that have exactly this extension."""
    return [e for e in entries if extension and e.extension == extension]


def directory_email_for(name: str, email: str | None) -> str | None:
    """An address from the caller's confirmed directory entry that is safe to offer, else None.

    The address is returned exactly as it is on file (never repaired: hmfg.net stays hmfg.net), and
    only when it is in the usual shape and belongs to this person: its username must contain the last
    name and either the first name or the first initial. That also keeps out shared mailboxes and
    placeholders ("0301user@", "helpdesk@"), which are nobody's name.
    The directory has entries whose email belongs to someone else; those are never offered.
    """
    if not email or not name:
        return None
    address = email.strip().lower()
    local, _, domain = address.partition("@")
    if not local or not domain:
        return None
    words = [re.sub(r"[^a-z]", "", part.lower()) for part in name.split()]
    if len(words) != 2 or min(len(w) for w in words) < 2:
        return None
    first, last = words
    letters = re.sub(r"[^a-z]", "", local)
    if last not in letters:
        return None
    return address if (first in letters or letters.startswith(first[0])) else None


def entry_context(entry: DirectoryEntry) -> dict:
    """The directory-provided values kept with the call once the caller has confirmed.

    An entry of the merged directory also carries the clean identity fields. Its email is the real mailbox
    address when there is one (the phone system's is often a placeholder or someone else's)."""
    data = {
        "directory_name": entry.name,
        "email": entry.mailbox_email or entry.email,
        "extension": entry.extension,
        "teams": list(entry.teams),
        "location": entry.location,
        "active": entry.active,
        "source_row": entry.source_row,
    }
    if entry.account_type is not None:
        data.update(
            account_type=entry.account_type, display_name=entry.display_name, phone=entry.phone,
            department=entry.department, site=entry.site, job_title=entry.job_title, credentials=entry.credentials,
            is_provider=entry.is_provider, status=entry.status, match_confidence=entry.match_confidence,
        )
    return data


def compact_lookup(result: LookupResult | None) -> dict:
    """What is stored in the call's session state for one lookup. Only a unique match carries an
    entry; an ambiguous one carries just how many records matched, never which."""
    if result is None:
        return {"status": "not_looked_up", "reason": "no_caller_number"}
    data: dict = {"status": result.status.value}
    if result.reason:
        data["reason"] = result.reason
    if result.status is LookupStatus.AMBIGUOUS:
        data["candidate_count"] = result.candidate_count
    if result.matched and result.entry is not None:
        data["entry"] = entry_context(result.entry)
        # Only a live entry with a name we can say aloud is ever proposed to the caller.
        name = employee_name(result.entry) if result.entry.active else None
        data["proposed_name"] = name
        if name is None:
            data["reason"] = "inactive_entry" if not result.entry.active else "not_a_person_name"
    return data

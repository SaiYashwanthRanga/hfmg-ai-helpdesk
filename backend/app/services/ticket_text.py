"""The sections inside a voice ticket's description, and how to read them back.

A voice ticket's description is assembled from, in order:

    <issue>                       the problem statement (may follow agent notes)
    --- Details ---               what the caller added: symptoms, specifics
    --- Intake details ---        department, started, impact, priority, ...
    --- Call transcript ---       the whole conversation

Everything the helpdesk needs to understand the problem lives above the
transcript, so the summary and the notification email never need it. Tickets
that did not come from a call (web form) have no markers and read as all issue.
"""

DETAILS_MARKER = "--- Details ---"
INTAKE_MARKER = "--- Intake details ---"
TRANSCRIPT_MARKER = "--- Call transcript ---"

_MARKERS = (DETAILS_MARKER, INTAKE_MARKER, TRANSCRIPT_MARKER)

# Notes the voice agent prepends to a description (orchestrator.py, voice/priority.py).
# Exact prefixes, not a pattern: a web-form ticket may legitimately start "VPN DOWN - ...".
_AGENT_NOTES = ("NEEDS TRIAGE REVIEW - ", "INCOMPLETE VOICE INTAKE - ", "CALLBACK REQUESTED - ")


def without_transcript(description: str) -> str:
    """Everything except the call transcript: the text the summary is written from."""
    return description.split(TRANSCRIPT_MARKER, 1)[0].strip()


def extract_issue(description: str) -> str:
    """The caller's own problem statement, without agent notes or later sections.

    Never empty for a non-empty description: a callback ticket where the caller
    never described a problem has only its agent note, so that is the issue.
    """
    head = description
    for marker in _MARKERS:
        head = head.split(marker, 1)[0]
    paragraphs = [p.strip() for p in head.split("\n\n")]
    notes = []
    while paragraphs and (not paragraphs[0] or paragraphs[0].startswith(_AGENT_NOTES)):
        notes.append(paragraphs.pop(0))
    issue = "\n\n".join(p for p in paragraphs if p)
    return issue or "\n\n".join(n for n in notes if n) or head.strip()


def extract_details(description: str) -> str:
    """What the caller said about the problem beyond the issue; empty if nothing."""
    if DETAILS_MARKER not in description:
        return ""
    section = description.split(DETAILS_MARKER, 1)[1]
    for marker in (INTAKE_MARKER, TRANSCRIPT_MARKER):
        section = section.split(marker, 1)[0]
    return section.strip()

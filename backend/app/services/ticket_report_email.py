"""The report email: an HTML body and a plain-text body built from a saved analysis.

Pure functions of the stored analysis -- no model call, no database. The email holds four sections and nothing else:

    Team Summary       Metric | Count            Open, Overdue, Unassigned, AI Critical, AI High
    Employee Workload  Employee | Unresolved | Critical/High | Overdue | Observation
    Waiting on Vendor  Ticket ID | Issue | Assigned To | Days Open | Status | Evidence
    Provider Issues    Ticket ID | Provider-Related Issue | Assigned To | Days Open | Business Impact

The email says what the issues are and never what to do about them: the IT team knows the next steps, so there is no
next-action or follow-up column anywhere (the dashboard keeps them). It is signed as the help desk agent that sends it.

One workload row per employee; the tickets nobody is assigned to are the "No Agent" row (also the owner shown for such a
ticket). The vendor and provider lists are the weekly follow-up groups from the saved analysis. The ticket-by-ticket
analysis, the priority issues, recurring issues, summary statements and data limitations stay in the dashboard.
Everything that came from the uploaded export or from the model is HTML-escaped. The layout is plain nested tables with
inline styles, which Outlook and other mail clients render.
"""

import html
from typing import Any

SUBJECT = "HFMG IT Ticket Priorities and Workload Report"
UNAVAILABLE = "Not available"
NO_AGENT = "No Agent"  # the row for tickets with no agent (the dashboard calls it "Unassigned")
NO_VALUE = "—"  # a cell whose text the AI left empty
INTRO = "AI recommendations are for a person to review; no Freshworks ticket was changed."
#: The sign-off: the email reads as sent by the help desk agent (the name the agent uses when it answers a call).
SIGNATURE = ["Regards,", "HFMG AI Help Desk Agent", "Horizon Family Medical Group IT Help Desk"]
FONT = "font-family:Segoe UI,Arial,sans-serif;"
WORKLOAD_HEADERS = ["Employee", "Unresolved", "Critical/High", "Overdue", "Observation"]
NO_WORKLOAD = "Not available: the file has no Assigned-to column."

# The weekly follow-up sections.
MAX_FOLLOWUP_ROWS = 25  # per section; more are in the dashboard
MAX_ISSUE_CHARS = 120
VENDOR_HEADERS = ["Ticket ID", "Issue", "Assigned To", "Days Open", "Status", "Evidence"]
PROVIDER_HEADERS = ["Ticket ID", "Provider-Related Issue", "Assigned To", "Days Open", "Business Impact"]
NO_VENDOR = "No ticket shows evidence of waiting on a vendor."
NO_PROVIDER = "No provider-related issue was found."
NO_FOLLOWUP = "Not available: this report was made before the vendor and provider groups existed."
FOLLOWUP_FAILED = "Not available: the vendor and provider check did not complete."
_VENDOR_STATUS = {"waiting_on_vendor": "Waiting on Vendor", "needs_review": "Needs Review"}


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _count(value: int | None) -> str:
    return UNAVAILABLE if value is None else str(value)


def subject_line(analysis: dict[str, Any] | None = None) -> str:
    return SUBJECT


def _intro(analysis: dict[str, Any]) -> str:
    return f"As of {analysis['as_of'][:10]} (UTC). {INTRO}"


def _summary_rows(analysis: dict[str, Any]) -> list[tuple[str, str]]:
    s = analysis["summary"]
    return [
        ("Open Tickets", _count(s["unresolved"])),
        ("Overdue Tickets", _count(s["overdue"])),
        ("Unassigned Tickets", _count(s["unassigned"])),
        ("AI Critical", _count(s["ai_critical"])),
        ("AI High", _count(s["ai_high"])),
    ]


def _name(e: dict[str, Any]) -> str:
    return NO_AGENT if e["unassigned"] else e["name"]


def _observation(e: dict[str, Any]) -> str:
    """The calculated sentence for the row. A report saved before the sentence existed gets the plain counts."""
    if e.get("observation"):
        return e["observation"]
    return f"{e['total']} unresolved ticket{'s' if e['total'] != 1 else ''}" + (f"; {e['overdue']} overdue" if e["overdue"] else "") + "."


def _workload_rows(analysis: dict[str, Any]) -> list[list[str]]:
    """Employee | Unresolved | Critical/High | Overdue | Observation, as plain strings (escaped when rendered)."""
    return [
        [_name(e), str(e["total"]), str(e["critical"] + e["high"]), str(e["overdue"]), _observation(e)]
        for e in analysis["employees"]
    ]


def _short(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_ISSUE_CHARS else text[: MAX_ISSUE_CHARS - 1].rstrip() + "…"


def _days(value: int | None) -> str:
    return UNAVAILABLE if value is None else str(value)


def _followup_blocks(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """The two weekly follow-up sections as {title, headers, aligns, rows, message, notes}. A section with no rows has a
    `message` instead of a table; a report saved before the groups existed, or whose check failed, says so rather than
    looking like "nothing found"."""
    followup = analysis.get("followup")
    blocks = [
        {"title": "Waiting on Vendor", "headers": VENDOR_HEADERS, "aligns": ["left", "left", "left", "right", "left", "left"],
         "items": (followup or {}).get("vendor", []), "none": NO_VENDOR,
         "row": lambda v: [f"#{v['ticket_id']}", _short(v["subject"]) or "(no subject)", v["assignee"] or NO_AGENT, _days(v["days_open"]),
                           _VENDOR_STATUS.get(v["status"], v["status"]), v["evidence"] or NO_VALUE]},
        {"title": "Provider Issues", "headers": PROVIDER_HEADERS, "aligns": ["left", "left", "left", "right", "left"],
         "items": (followup or {}).get("provider", []), "none": NO_PROVIDER,
         "row": lambda p: [f"#{p['ticket_id']}", p["issue"], p["assignee"] or NO_AGENT, _days(p["days_open"]), p["impact"] or NO_VALUE]},
    ]
    out = []
    for block in blocks:
        items = block.pop("items")
        row, none = block.pop("row"), block.pop("none")
        notes: list[str] = []
        if followup is None:
            rows, message = [], NO_FOLLOWUP
        elif followup["status"] == "unavailable":
            rows, message = [], FOLLOWUP_FAILED
        else:
            rows = [row(item) for item in items[:MAX_FOLLOWUP_ROWS]]
            message = None if rows else none
            if len(items) > MAX_FOLLOWUP_ROWS:
                notes.append(f"Showing {MAX_FOLLOWUP_ROWS} of {len(items)}. The rest are in the dashboard.")
            if followup["status"] == "partial":
                notes.append(f"Covers {followup['classified']} tickets; some could not be classified, so this list may be incomplete.")
        out.append({**block, "rows": rows, "message": message, "notes": notes})
    return out


# --- plain text -------------------------------------------------------------------------------------------------------


def build_text(analysis: dict[str, Any]) -> str:
    lines = [SUBJECT.upper(), _intro(analysis), "", "TEAM SUMMARY", "Metric | Count"]
    lines += [f"{label} | {value}" for label, value in _summary_rows(analysis)]
    lines += ["", "EMPLOYEE WORKLOAD"]
    rows = _workload_rows(analysis)
    if rows:
        lines.append(" | ".join(WORKLOAD_HEADERS))
        lines += [" | ".join(row) for row in rows]
    else:
        lines.append(NO_WORKLOAD)
    for block in _followup_blocks(analysis):
        lines += ["", block["title"].upper()]
        if block["rows"]:
            lines.append(" | ".join(block["headers"]))
            lines += [" | ".join(row) for row in block["rows"]]
        else:
            lines.append(block["message"])
        lines += block["notes"]
    lines += ["", *SIGNATURE]
    return "\n".join(lines)


# --- HTML -------------------------------------------------------------------------------------------------------------


def _h2(text: str) -> str:
    return f'<h2 style="{FONT}font-size:16px;margin:22px 0 8px;color:#1c1917">{_e(text)}</h2>'


def _table(headers: list[str], rows: list[list[str]], aligns: list[str]) -> str:
    """Plain nested table. Every cell is escaped here, so callers pass plain text."""
    head = "".join(
        f'<th align="{a}" style="padding:6px 8px;border-bottom:2px solid #d6d3d1;font-size:12px;color:#57534e;{FONT}">{_e(h)}</th>'
        for h, a in zip(headers, aligns)
    )
    body = "".join(
        "<tr>" + "".join(
            f'<td align="{a}" valign="top" style="padding:6px 8px;border-bottom:1px solid #e7e5e4;font-size:13px;{FONT}">{_e(cell)}</td>'
            for cell, a in zip(row, aligns)
        ) + "</tr>"
        for row in rows
    )
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" '
        f'style="border-collapse:collapse"><tr>{head}</tr>{body}</table>'
    )


def build_html(analysis: dict[str, Any]) -> str:
    parts = [
        f'<div style="{FONT}color:#1c1917;max-width:820px">',
        f'<h1 style="font-size:20px;margin:0 0 4px">{_e(SUBJECT)}</h1>',
        f'<p style="margin:0;font-size:12px;color:#57534e">{_e(_intro(analysis))}</p>',
        _h2("Team Summary"),
        _table(["Metric", "Count"], [list(row) for row in _summary_rows(analysis)], ["left", "right"]),
        _h2("Employee Workload"),
    ]
    rows = _workload_rows(analysis)
    if rows:
        parts.append(_table(WORKLOAD_HEADERS, rows, ["left", "right", "right", "right", "left"]))
    else:
        parts.append(f'<p style="font-size:13px">{_e(NO_WORKLOAD)}</p>')
    for block in _followup_blocks(analysis):
        parts.append(_h2(block["title"]))
        if block["rows"]:
            parts.append(_table(block["headers"], block["rows"], block["aligns"]))
        else:
            parts.append(f'<p style="font-size:13px">{_e(block["message"])}</p>')
        parts += [f'<p style="font-size:11px;color:#78716c;margin:6px 0 0">{_e(note)}</p>' for note in block["notes"]]
    parts.append(f'<p style="margin:24px 0 0;font-size:13px;color:#1c1917">{"<br>".join(_e(line) for line in SIGNATURE)}</p>')
    parts.append("</div>")
    return "".join(parts)

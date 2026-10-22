"""The numbers in a ticket report. Everything here is calculated from the uploaded rows; no model is involved.

A number that the export cannot establish is `None` ("unavailable"), never 0 and never a guess:

- *open* / *resolved* come from the Status column (or a resolution date when the status is not recognized). A ticket
  whose status cannot be read is *unknown*, and is counted as neither.
- *overdue* is an open ticket whose "Due by" time has passed. Without a due time, or when the ticket may already be
  resolved, it is not overdue -- an old ticket is not an overdue ticket.
- *SLA breach* is a ticket the export's own SLA column marks as breached, or one that was resolved after its "Due by"
  time. Without either column the count is unavailable.
- *unassigned* is a ticket that is not resolved and has no agent, and needs an Assigned-to column to be known.
"""

from collections import Counter
from datetime import datetime
from typing import Any

PRIORITY_LEVELS = ("critical", "high", "medium", "low")
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MAX_LISTED_IDS = 50


def _moment(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def enrich(rows: list[dict[str, Any]], fields_present: list[str], as_of: datetime) -> list[dict[str, Any]]:
    """Each row with `age_days`, `overdue` and `late` (resolved after its due time) added -- None when unknown."""
    has_due = "due" in fields_present
    out: list[dict[str, Any]] = []
    for row in rows:
        created, due, resolved = _moment(row.get("created")), _moment(row.get("due")), _moment(row.get("resolved"))
        state = row["state"]
        age = None
        if created and state != "resolved":
            age = max((as_of - created).days, 0)
        overdue: bool | None = None
        days_overdue: int | None = None
        if has_due and due is not None and state == "open":
            overdue = due < as_of
            days_overdue = (as_of - due).days if overdue else None
        late: bool | None = None
        if due is not None and resolved is not None and state == "resolved":
            late = resolved > due
        out.append({**row, "age_days": age, "overdue": overdue, "days_overdue": days_overdue, "late": late})
    return out


def _is_breach(row: dict[str, Any]) -> bool | None:
    if row.get("sla_breached") is True or row.get("late") is True:
        return True
    if row.get("sla_breached") is False or row.get("late") is False:
        return False
    return None


def summarize(rows: list[dict[str, Any]], fields_present: list[str]) -> dict[str, Any]:
    """Counts for the executive summary. `rows` are enriched rows (resolved ones included)."""
    has = set(fields_present)
    states = Counter(r["state"] for r in rows)
    status_known = states["open"] + states["resolved"] > 0
    unresolved = [r for r in rows if r["state"] != "resolved"]  # open + unknown: work that may still be waiting

    def levels(subset: list[dict[str, Any]]) -> dict[str, int]:
        counts = Counter(r["priority_level"] for r in subset if r["priority_level"])
        result = {level: counts.get(level, 0) for level in PRIORITY_LEVELS}
        result["unrecognized"] = sum(1 for r in subset if r["priority"] and not r["priority_level"])
        result["not_specified"] = sum(1 for r in subset if not r["priority"])
        return result

    # Known only when at least one ticket actually says so (the export's own SLA text, or a due and a resolved time
    # to compare). A column that exists but is empty on every row establishes nothing: that is "unavailable", not 0.
    breach_known = any(_is_breach(r) is not None for r in rows)
    breaches = [r for r in rows if _is_breach(r) is True] if breach_known else None
    first_response_known = any(r.get("first_response_breached") is not None for r in rows)
    unassigned = [r for r in unresolved if not r["assignee"]] if "assignee" in has else None
    categories = Counter(r["category"] for r in rows if r["category"]) if "category" in has else None
    ages = [r["age_days"] for r in unresolved if r["age_days"] is not None]

    return {
        "total": len(rows),
        "status_available": "status" in has,
        # Not resolved: open plus the tickets whose status could not be read. Unavailable only when no ticket's
        # status could be read at all (then nothing is known to be open or resolved).
        "unresolved": len(unresolved) if status_known else None,
        "open": states["open"] if status_known else None,
        "resolved": states["resolved"] if status_known else None,
        "unknown_status": states["unknown"],
        "by_priority": levels(rows) if "priority" in has else None,
        "by_priority_unresolved": levels(unresolved) if "priority" in has else None,
        "critical_high_unresolved": (
            sum(1 for r in unresolved if r["priority_level"] in ("critical", "high")) if "priority" in has else None
        ),
        "overdue": sum(1 for r in rows if r["overdue"] is True) if "due" in has and status_known else None,
        "sla_breaches": len(breaches) if breaches is not None else None,
        "first_response_breaches": (
            sum(1 for r in rows if r.get("first_response_breached") is True) if first_response_known else None
        ),
        "unassigned": len(unassigned) if unassigned is not None else None,
        "unassigned_ticket_ids": [r["ticket_id"] for r in unassigned[:MAX_LISTED_IDS]] if unassigned else [],
        "top_categories": [{"name": n, "count": c} for n, c in categories.most_common(8)] if categories else None,
        "oldest_unresolved_age_days": max(ages) if ages else None,
        "median_unresolved_age_days": sorted(ages)[len(ages) // 2] if ages else None,
    }


def _natural(ticket_id: str) -> tuple[int, int, str]:
    """Ticket IDs in the order a person expects: 2 before 10 (numeric IDs by value, anything else after, as text)."""
    return (0, int(ticket_id), "") if ticket_id.isdigit() else (1, 0, ticket_id)


_AI_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def sort_actions(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report tickets in the order IT should work them: AI Critical, High, Medium, Low (tickets with no
    recommendation last); within a level, overdue first, then the oldest, then by ticket number."""
    def key(t: dict[str, Any]) -> tuple[int, int, int, tuple[int, int, str]]:
        age = t.get("age_days")
        return (
            _AI_RANK.get(t["ai"].get("priority") or "", 4),
            0 if t.get("overdue") is True else 1,
            -(age if age is not None else -1),
            _natural(t["ticket_id"]),
        )

    return sorted(tickets, key=key)


def headline_ids(sorted_tickets: list[dict[str, Any]], medium_items: int) -> list[str]:
    """The tickets the dashboard's short list and the email show: every AI Critical and High ticket, then the first
    `medium_items` Medium ones. With no Critical or High at all, the first `medium_items` analyzed tickets in work
    order instead -- the list is never padded with recommendations the AI did not make."""
    analyzed = [t for t in sorted_tickets if t["ai"].get("status") == "analyzed"]
    urgent = [t for t in analyzed if t["ai"]["priority"] in ("Critical", "High")]
    if urgent:
        medium = [t for t in analyzed if t["ai"]["priority"] == "Medium"][: max(medium_items, 0)]
        chosen = urgent + medium
    else:
        chosen = analyzed[: max(medium_items, 0)]
    return [t["ticket_id"] for t in chosen]


def workload_observation(row: dict[str, Any]) -> str:
    """A plain statement of one row's numbers (a fact, not an AI opinion): "17 unresolved tickets (34% of all
    unresolved tickets); 13 overdue; oldest is 22 days old." Parts that are not known or not applicable are left out."""
    total = row["total"]
    parts = [f"{total} unresolved ticket{'s' if total != 1 else ''}"
             + (f" ({row['share_percent']}% of all unresolved tickets)" if row.get("share_percent") is not None else "")]
    if row.get("overdue"):
        parts.append(f"{row['overdue']} overdue")
    age = row.get("oldest_age_days")
    if age is not None:
        parts.append(f"oldest is {age} day{'s' if age != 1 else ''} old")
    return "; ".join(parts) + "."


def employee_table(tickets: list[dict[str, Any]], fields_present: list[str]) -> list[dict[str, Any]]:
    """One row per agent (and one "Unassigned" row) for the unresolved tickets in the report.

    `tickets` are the report's ticket rows (unresolved only, each with its `ai` result). Every unresolved ticket is in
    exactly one row, so the rows add up to the report's unresolved total. Unassigned tickets are never attributed to a
    person. Empty when the export has no Assigned-to column. Busiest first: Critical plus High, then overdue, then total."""
    if "assignee" not in fields_present:
        return []
    groups: dict[str | None, list[dict[str, Any]]] = {}
    for ticket in tickets:
        groups.setdefault(ticket["assignee"] or None, []).append(ticket)
    rows = []
    for name, items in groups.items():
        critical = sum(1 for t in items if t["ai"].get("priority") == "Critical")
        high = sum(1 for t in items if t["ai"].get("priority") == "High")
        ages = [t["age_days"] for t in items if t.get("age_days") is not None]
        row = {
            "name": name or "Unassigned",
            "unassigned": name is None,
            "total": len(items),
            "overdue": sum(1 for t in items if t.get("overdue") is True),
            "critical": critical,
            "high": high,
            "remaining": len(items) - critical - high,
            "unknown_status": sum(1 for t in items if t["state"] == "unknown"),
            "oldest_age_days": max(ages) if ages else None,
            "share_percent": round(100 * len(items) / len(tickets)),
        }
        row["observation"] = workload_observation(row)
        rows.append(row)
    rows.sort(key=lambda r: (-(r["critical"] + r["high"]), -r["overdue"], -r["total"], r["unassigned"], r["name"].lower()))
    return rows


def _days_first(days: int | None, ticket_id: str) -> tuple[int, tuple[int, int, str]]:
    """Longest open first (a ticket with no known age last), then by ticket number."""
    return (-(days if days is not None else -1), _natural(ticket_id))


def followup_sections(tickets: list[dict[str, Any]], answers: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The three weekly follow-up lists, from the report's tickets and the follow-up pass's validated answers.

    - **vendor**: tickets that show they are waiting on a vendor, then those that need review (a vendor is hinted at but
      not shown to be what the ticket waits on). Longest open first within each.
    - **provider**: tickets that affect a provider or their work, highest AI priority first, then longest open.
    - **daily_work**: the tickets the AI recommended as Critical or High, in the report's work order.

    A ticket can be in more than one list (a provider who cannot work is also a priority issue). Every entry carries the
    assigned employee and the days the ticket has been open (None when the file has no created time)."""
    def base(t: dict[str, Any]) -> dict[str, Any]:
        return {"ticket_id": t["ticket_id"], "subject": t["subject"], "assignee": t["assignee"], "days_open": t["age_days"]}

    vendor, provider = [], []
    for t in tickets:
        found = answers.get(t["ticket_id"])
        if not found:
            continue
        if found["vendor"]["status"] != "not_vendor":
            vendor.append({**base(t), "status": found["vendor"]["status"], "evidence": found["vendor"]["evidence"],
                           "next_action": found["vendor"]["next_action"]})
        if found["provider"]["related"]:
            provider.append({**base(t), "issue": found["provider"]["issue"], "impact": found["provider"]["impact"],
                             "next_action": found["provider"]["next_action"], "priority": t["ai"].get("priority"),
                             "freshworks_priority": t["priority"]})
    vendor.sort(key=lambda v: (0 if v["status"] == "waiting_on_vendor" else 1, *_days_first(v["days_open"], v["ticket_id"])))
    provider.sort(key=lambda p: (_AI_RANK.get(p["priority"] or "", 4), *_days_first(p["days_open"], p["ticket_id"])))
    daily = [
        {**base(t), "priority": t["ai"]["priority"], "freshworks_priority": t["priority"], "overdue": t["overdue"],
         "reason": t["ai"]["reason"], "next_action": t["ai"]["next_action"]}
        for t in tickets if t["ai"].get("priority") in ("Critical", "High")
    ]
    return {"vendor": vendor, "provider": provider, "daily_work": daily}


def highlights(summary: dict[str, Any], employees: list[dict[str, Any]], focus: dict[str, Any] | None) -> list[str]:
    """At most three short statements, written from the report's own numbers (and the AI's focus, when it gave one).

    Nothing is fixed text with numbers in it: a figure that is not available is said to be, not left out or zeroed."""
    out: list[str] = []
    unresolved, overdue = summary.get("unresolved"), summary.get("overdue")
    if unresolved is None:
        out.append("The number of open tickets is not available: the file's status could not be read.")
    else:
        count = f"{unresolved} open ticket{'s' if unresolved != 1 else ''}"
        out.append(f"{count}; {overdue} overdue." if overdue is not None else f"{count}; the overdue count is not available.")

    people = [e for e in employees if not e["unassigned"]]
    if people and people[0]["total"]:
        top = people[0]
        urgent = top["critical"] + top["high"]
        detail = f"{top['total']} open" + (f", {urgent} Critical/High" if urgent else "") + (f", {top['overdue']} overdue" if top["overdue"] else "")
        out.append(f"{top['name']} has the highest outstanding workload ({detail}).")

    critical, high, analyzed = summary.get("ai_critical"), summary.get("ai_high"), summary.get("ai_analyzed") or 0
    if focus:
        ids = ", ".join(f"#{i}" for i in focus["ticket_ids"])
        out.append(f"Review first: {focus['theme']} ({ids}).")
    elif critical or high:
        parts = [f"{n} {label}" for n, label in ((critical, "Critical"), (high, "High")) if n]
        out.append(f"{' and '.join(parts)} ticket{'s are' if (critical or 0) + (high or 0) != 1 else ' is'} recommended for review first.")
    elif analyzed:
        out.append("No ticket was recommended as Critical or High; review the overdue and oldest tickets first.")
    return out[:3]


def urgency_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    """Order in which open tickets are given to the model when there are more than the limit allows: overdue first,
    then Freshworks priority, then age. Deterministic, so which tickets were analyzed is never arbitrary."""
    return (
        0 if row["overdue"] else 1,
        _PRIORITY_RANK.get(row["priority_level"] or "", 4),
        -(row["age_days"] or 0),
        row["ticket_id"],
    )

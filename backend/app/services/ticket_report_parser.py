"""Reads a Freshworks ticket export (.csv or .xlsx) into normalized rows, and says what is wrong with it.

Nothing here calls a model. Two kinds of problem:

- **Fatal** (`ReportUploadError`): the file cannot be used at all -- wrong type, not what its extension says, empty,
  unreadable, or over the size / row limits. The upload is refused.
- **Validation issues** (returned in `ParsedUpload.errors` / `.warnings`): the file is readable. An *error* blocks the
  analysis (no ticket-ID column, no usable rows); a *warning* does not (duplicate IDs, dates that could not be read,
  columns that were ignored). Whatever cannot be established is left empty -- never guessed.

Privacy: requester names, emails and phone numbers are never read into the rows (those columns are listed as
ignored). Free text (subject, description) is reduced to plain text, shortened and run through `redact`, which masks
the identifiers it can recognise (emails, phone numbers, SSNs, dates of birth, record / account numbers). It cannot
recognise a patient's name written in free text; the report says so.
"""

import csv
import html
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings

settings = get_settings()

ACCEPTED_EXTENSIONS = (".csv", ".xlsx")
#: A zipped .xlsx can expand enormously; refuse one whose parts add up to more than this.
_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_SUBJECT_CHARS = 200
MAX_DESCRIPTION_CHARS = 600
#: For the weekly follow-up pass (is a vendor involved, is it a provider issue): the start and the END of a long
#: description, because a pasted email thread says where things stand at the end.
THREAD_HEAD_CHARS = 1500
THREAD_TAIL_CHARS = 700
MAX_TAGS_CHARS = 200
MAX_TICKET_ID_CHARS = 64
MAX_COLUMNS = 200
_MAX_LISTED = 10  # how many example IDs / row numbers a warning names


class ReportUploadError(Exception):
    """The upload cannot be used. `status_code` is the HTTP status the API answers with."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# --- columns ----------------------------------------------------------------------------------------------------------

#: Canonical field -> header names (lower-case, letters and digits only) a Freshworks export may use.
_ALIASES: dict[str, tuple[str, ...]] = {
    "ticket_id": ("ticketid", "ticketnumber", "ticketno", "displayid", "ticket", "requestid", "id", "number"),
    "subject": ("subject", "title", "ticketsubject", "summary"),
    "description": ("description", "descriptiontext", "ticketdescription", "details", "body"),
    "status": ("status", "ticketstatus", "state"),
    "priority": ("priority", "ticketpriority"),
    "assignee": ("agent", "agentname", "assignedto", "assignee", "responder", "assignedagent", "owner", "technician"),
    "group": ("group", "groupname", "team", "assigngroup"),
    "category": ("category", "issuecategory", "categoryname"),
    "sub_category": ("subcategory", "subcategoryname"),
    # Freshservice's "Type" is Incident / Service Request, not a second category.
    "ticket_type": ("type", "tickettype"),
    # Freshservice derives Priority from Impact and Urgency, so they say more than the Priority alone.
    "impact": ("impact",),
    "urgency": ("urgency",),
    "business_impact": ("businessimpact",),
    "impacted_locations": ("impactedlocations", "affectedlocations"),
    "customers_impacted": ("noofcustomersimpacted", "customersimpacted", "numberofcustomersimpacted", "noofusersimpacted", "usersimpacted"),
    "created": ("createdtime", "createdat", "created", "createddate", "createdon", "opendate", "creationdate"),
    "due": ("dueby", "duebytime", "duedate", "due", "resolutiondue", "resolutionduedate", "duebyat"),
    "resolved": ("resolvedtime", "resolvedat", "resolveddate", "resolutiondate"),
    "closed": ("closedtime", "closedat", "closeddate"),
    "sla_status": ("resolutionstatus", "slastatus", "slabreached", "slaviolated", "resolutionslastatus"),
    "first_response": ("firstresponsestatus", "firstresponseslastatus"),
    "tags": ("tags", "tag", "labels"),
}
_FIELD_LABELS = {
    "ticket_id": "Ticket ID", "subject": "Subject", "description": "Description", "status": "Status",
    "priority": "Priority", "assignee": "Assigned to", "group": "Group", "category": "Category",
    "created": "Created", "due": "Due by", "resolved": "Resolved", "closed": "Closed", "sla_status": "SLA status",
    "sub_category": "Sub-category", "ticket_type": "Type", "impact": "Impact", "urgency": "Urgency",
    "business_impact": "Business impact", "impacted_locations": "Impacted locations",
    "customers_impacted": "Customers impacted", "first_response": "First response status", "tags": "Tags",
}
#: Columns that identify a person (often a patient or a colleague): recognised only to be ignored. Counts such as
#: "No. of customers impacted" are not about a person; "Customer name" and "Customer email" are.
_PERSONAL = re.compile(
    r"requester|contact|reporter|caller|e-?mail|phone|mobile|address|full ?name|(?:customer|client|patient|user|employee)\s*(?:name|id)",
    re.IGNORECASE,
)

#: The report cannot be built without these (a subject OR a description is enough to describe the issue).
REQUIRED_FIELDS = ("ticket_id",)
DESCRIBING_FIELDS = ("subject", "description")


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def map_columns(headers: list[str]) -> tuple[dict[str, int], list[dict[str, Any]], list[str]]:
    """(field -> column index, one entry per column describing how it was read, notes about duplicate columns)."""
    lookup = {alias: field_name for field_name, aliases in _ALIASES.items() for alias in aliases}
    # An exact alias beats a partial one, and the first column to claim a field keeps it.
    mapping: dict[str, int] = {}
    detail: list[dict[str, Any]] = []
    notes: list[str] = []
    for index, header in enumerate(headers):
        name = header.strip()
        field_name = lookup.get(_key(name)) if name else None
        entry: dict[str, Any] = {"name": name or f"(column {index + 1})", "field": None, "used": False}
        if field_name is not None:
            if field_name in mapping:
                notes.append(
                    f'Column "{entry["name"]}" also looks like {_FIELD_LABELS[field_name]}; '
                    f'the first one ("{headers[mapping[field_name]].strip()}") is used.'
                )
            else:
                mapping[field_name] = index
                entry.update(field=field_name, used=True)
        elif name and _PERSONAL.search(name):
            entry["note"] = "ignored: may identify a person"
        detail.append(entry)
    return mapping, detail, notes


# --- text cleaning ----------------------------------------------------------------------------------------------------

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[id]"),
    (re.compile(r"\b(?:dob|d\.o\.b\.?|date of birth|birth ?date)\b[:\s#-]*[\w/.,-]{4,}", re.IGNORECASE), "DOB [removed]"),
    (re.compile(
        r"\b(?:mrn|medical record(?: number| no\.?)?|chart (?:number|no\.?)|account(?: number| no\.?)?|acct|"
        r"member id|policy(?: number| no\.?)?|patient id)\b[:\s#-]*[A-Za-z0-9-]{4,}", re.IGNORECASE), "[record number]"),
    # (845) 555-0142, 845-555-0142, 845.555.0142, +1 845 555 0142. Not an IP address (172.22.6.98 has no 3-3-4 shape).
    # A full stop after it ("...555-0142.") still ends the number; only a digit after the stop continues it.
    (re.compile(r"(?<![\d.])(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b(?!\d|\.\d)"), "[phone]"),
    (re.compile(r"(?<![\d.])\d{9,}(?![\d.])"), "[number]"),
)


def redact(text: str) -> str:
    """Mask the identifiers that can be recognised by their shape. Idempotent."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def clean_text(value: Any, limit: int) -> str:
    """Plain, single-spaced, redacted text of at most `limit` characters ("" for blank / non-text)."""
    if value is None:
        return ""
    text = html.unescape(_TAGS.sub(" ", str(value)))
    text = _SPACES.sub(" ", text).strip()
    text = redact(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clean_excerpt(value: Any, head: int = THREAD_HEAD_CHARS, tail: int = THREAD_TAIL_CHARS) -> str:
    """Like `clean_text`, but a long text keeps its start AND its end (the middle is marked "[...]"). Redaction runs
    on the whole text first, so a masked identifier is never cut in half."""
    if value is None:
        return ""
    text = redact(_SPACES.sub(" ", html.unescape(_TAGS.sub(" ", str(value)))).strip())
    if len(text) <= head + tail:
        return text
    return f"{text[:head].rstrip()} [...] {text[-tail:].lstrip()}"


# --- ticket ID, priority, status, dates -------------------------------------------------------------------------------


def normalize_ticket_id(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # Excel stores 1042 as 1042.0
    text = _SPACES.sub(" ", str(value).strip().lstrip("#")).strip()
    return text[:MAX_TICKET_ID_CHARS]


#: Original priority text -> a level the report can count. Anything else stays "unrecognized" (the text is kept).
_PRIORITY_LEVELS = {
    "critical": "critical", "urgent": "critical", "emergency": "critical", "p1": "critical", "sev1": "critical",
    "severity1": "critical",
    "high": "high", "p2": "high", "major": "high", "sev2": "high", "severity2": "high",
    "medium": "medium", "normal": "medium", "moderate": "medium", "p3": "medium", "sev3": "medium", "severity3": "medium",
    "low": "low", "minor": "low", "p4": "low", "sev4": "low", "severity4": "low", "trivial": "low",
}
# A bare number is not read: Freshdesk numbers priorities 1 (low) to 4 (urgent), other tools the other way round.


def priority_level(text: str) -> str | None:
    return _PRIORITY_LEVELS.get(_key(text)) if text else None


#: Statuses that mean the work is done, and statuses that mean it is not. A status in neither list is "unknown":
#: the ticket is not counted as open or as resolved, and the report says how many there were.
_DONE = {"resolved", "closed", "complete", "completed", "done", "cancelled", "canceled", "deleted", "merged",
         "fulfilled", "rejected", "declined", "archived"}
_NOT_DONE = {"open", "new", "pending", "inprogress", "onhold", "hold", "waitingoncustomer", "waitingonthirdparty",
             "waitingforcustomer", "waitingforthirdparty", "waitingforrequester", "waitingonrequester", "assigned",
             "reopened", "awaitingresponse", "inprogressopen", "escalated", "scheduled", "planning", "analysis",
             "underinvestigation", "investigating", "customerresponded", "workinprogress", "acknowledged"}


def status_state(text: str, *, has_resolution_date: bool = False) -> str:
    """"open" | "resolved" | "unknown" -- only what the status text (or, without a recognizable status, a
    resolution date) establishes. A recognized status wins: a reopened ticket keeps its old resolution date."""
    key = _key(text) if text else ""
    if key in _DONE:
        return "resolved"
    if key in _NOT_DONE:
        return "open"
    return "resolved" if has_resolution_date else "unknown"


_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d %b, %Y %I:%M %p", "%d %b, %Y %H:%M", "%d %b, %Y", "%d %b %Y %H:%M", "%d %b %Y", "%d %B, %Y %I:%M %p",
    "%d %B %Y", "%b %d, %Y %I:%M %p", "%b %d, %Y %H:%M", "%b %d, %Y", "%B %d, %Y",
    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%m/%d/%y %H:%M", "%m/%d/%y",
)
_SLASH_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}")


def parse_date(value: Any) -> tuple[datetime | None, bool]:
    """(the moment, was it a value we could not read). A blank cell is (None, False). Naive values are read as UTC.
    Slash dates are month-first (US); the report states that assumption."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, False
    if isinstance(value, datetime):
        moment = value
    elif hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):  # a date
        moment = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        moment = None
        try:
            moment = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError:
            for pattern in _DATE_FORMATS:
                try:
                    moment = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
        if moment is None:
            return None, True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    if not 1990 <= moment.year <= 2100:
        return None, True
    return moment, False


def _used_slash_dates(value: Any) -> bool:
    return isinstance(value, str) and bool(_SLASH_DATE.match(value.strip()))


_SLA_OK = re.compile(
    r"\bnot\s+(?:breach|viol|overdue)|\bwithin\b|\bmet\b|\bon\s?time\b|\bok\b|^\W*(?:no|false|n|0)\W*$", re.IGNORECASE
)
_SLA_BREACH = re.compile(r"viol|breach|overdue|missed|exceed|\bfail|^\W*(?:yes|true|y|1)\W*$", re.IGNORECASE)


def sla_breached(text: str) -> bool | None:
    """From an explicit SLA column only: True / False, or None when the text does not say."""
    if not text:
        return None
    if _SLA_OK.search(text):
        return False
    return True if _SLA_BREACH.search(text) else None


# --- result -----------------------------------------------------------------------------------------------------------


@dataclass
class ParsedUpload:
    file_type: str
    source_row_count: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[dict[str, Any]] = field(default_factory=list)
    fields_present: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors


def _issue(code: str, message: str, **detail: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **({"detail": detail} if detail else {})}


# --- reading the file -------------------------------------------------------------------------------------------------


def _check_type(filename: str, content: bytes) -> str:
    name = (filename or "").strip()
    lowered = name.lower()
    if lowered.endswith(".xls"):
        raise ReportUploadError(415, "Old Excel (.xls) files are not supported. Save the export as .xlsx or .csv.")
    extension = next((e for e in ACCEPTED_EXTENSIONS if lowered.endswith(e)), None)
    if extension is None:
        raise ReportUploadError(415, "Unsupported file type. Upload a Freshworks export as .csv or .xlsx.")
    if not content.strip():
        raise ReportUploadError(422, "The file is empty.")
    if extension == ".xlsx":
        if not content.startswith(b"PK\x03\x04"):
            raise ReportUploadError(422, "This is not a real Excel (.xlsx) file, whatever its extension says.")
    else:
        if content.startswith((b"PK\x03\x04", b"%PDF", b"\xd0\xcf\x11\xe0")) or b"\x00" in content[:4096]:
            raise ReportUploadError(422, "This is not a CSV text file, whatever its extension says.")
    return extension.lstrip(".")


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def _read_csv(content: bytes, limit: int) -> list[list[Any]]:
    text = _decode(content)
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max((",", ";", "\t"), key=first_line.count) if first_line else ","
    rows: list[list[Any]] = []
    try:
        for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
            if any(cell.strip() for cell in row):
                rows.append(row)
            if len(rows) > limit + 1:  # the header, the allowed rows, and one more to know it is too many
                break
    except csv.Error as exc:
        raise ReportUploadError(422, f"The CSV file could not be read ({exc}).") from exc
    return rows


def _read_xlsx(content: bytes, limit: int) -> list[list[Any]]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > _MAX_UNCOMPRESSED_BYTES:
                raise ReportUploadError(413, "The Excel file expands to more data than is allowed.")
            if "xl/workbook.xml" not in archive.namelist():
                raise ReportUploadError(422, "This is not a real Excel (.xlsx) file, whatever its extension says.")
    except zipfile.BadZipFile as exc:
        raise ReportUploadError(422, "The Excel file is damaged and could not be opened.") from exc

    from openpyxl import load_workbook  # imported here: only an .xlsx upload needs it

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for damaged files
        raise ReportUploadError(422, "The Excel file could not be read.") from exc
    rows: list[list[Any]] = []
    try:
        sheet = next((ws for ws in workbook.worksheets if ws.sheet_state == "visible"), None)
        if sheet is None:
            raise ReportUploadError(422, "The Excel file has no visible sheet.")
        for row in sheet.iter_rows(values_only=True):
            cells = list(row)
            if any(cell is not None and str(cell).strip() for cell in cells):
                rows.append(cells)
            if len(rows) > limit + 1:
                break
    finally:
        workbook.close()
    return rows


# --- parsing ----------------------------------------------------------------------------------------------------------


def parse_upload(filename: str, content: bytes) -> ParsedUpload:
    """Validate and normalize an uploaded export. Raises `ReportUploadError` for a file that cannot be used."""
    max_rows = settings.ticket_report_max_rows
    if len(content) > settings.ticket_report_max_upload_bytes:
        raise ReportUploadError(
            413, f"The file is larger than the {settings.ticket_report_max_upload_bytes // 1_000_000} MB limit."
        )
    file_type = _check_type(filename, content)
    raw = _read_csv(content, max_rows) if file_type == "csv" else _read_xlsx(content, max_rows)
    if not raw:
        raise ReportUploadError(422, "The file has no data.")
    headers = ["" if cell is None else str(cell) for cell in raw[0]]
    if len(headers) > MAX_COLUMNS:
        raise ReportUploadError(422, f"The file has more than {MAX_COLUMNS} columns, which is not a ticket export.")
    body = raw[1:]
    if len(body) > max_rows:
        raise ReportUploadError(413, f"The file has more than {max_rows} ticket rows, which is the limit.")

    result = ParsedUpload(file_type=file_type, source_row_count=len(body))
    mapping, result.columns, duplicate_notes = map_columns(headers)
    result.fields_present = [name for name in _ALIASES if name in mapping]

    if not body:
        result.errors.append(_issue("no_rows", "The file has a header row but no tickets."))
    if "ticket_id" not in mapping:
        result.errors.append(_issue(
            "missing_ticket_id", "No ticket ID column was found. Expected a column such as “Ticket ID” or “ID”."
        ))
    if not any(name in mapping for name in DESCRIBING_FIELDS):
        result.errors.append(_issue(
            "missing_subject", "No Subject or Description column was found, so the issues cannot be described."
        ))
    for note in duplicate_notes:
        result.warnings.append(_issue("duplicate_column", note))
    unmapped = [c["name"] for c in result.columns if c["field"] is None and not c.get("note") and c["name"]]
    if unmapped:
        shown = ", ".join(unmapped[:_MAX_LISTED]) + (" …" if len(unmapped) > _MAX_LISTED else "")
        result.warnings.append(_issue(
            "unexpected_columns", f"{len(unmapped)} column(s) are not used by the report: {shown}.", columns=unmapped[:50]
        ))
    ignored_personal = [c["name"] for c in result.columns if c.get("note")]
    if ignored_personal:
        result.warnings.append(_issue(
            "personal_columns_ignored",
            f"Columns that may identify a person were ignored and not stored: {', '.join(ignored_personal[:_MAX_LISTED])}.",
        ))
    if result.errors:
        return result

    _normalize_rows(result, body, mapping)
    if not result.rows:
        result.errors.append(_issue("no_usable_rows", "None of the rows has a ticket ID, so there is nothing to analyze."))
    return result


def _cell(row: list[Any], mapping: dict[str, int], name: str) -> Any:
    index = mapping.get(name)
    if index is None or index >= len(row):
        return None
    value = row[index]
    return value.strip() if isinstance(value, str) else value


def _normalize_rows(result: ParsedUpload, body: list[list[Any]], mapping: dict[str, int]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    missing_id_rows: list[int] = []
    bad_dates: dict[str, int] = {}
    slash_dates = False
    unreadable_status = 0
    for position, row in enumerate(body, start=2):  # spreadsheet row numbers: the header is row 1
        ticket_id = normalize_ticket_id(_cell(row, mapping, "ticket_id"))
        if not ticket_id:
            missing_id_rows.append(position)
            continue
        if ticket_id in seen:
            duplicates.append(ticket_id)
            continue
        seen.add(ticket_id)

        dates: dict[str, str | None] = {}
        for name in ("created", "due", "resolved", "closed"):
            raw_value = _cell(row, mapping, name)
            moment, bad = parse_date(raw_value)
            if bad:
                bad_dates[name] = bad_dates.get(name, 0) + 1
            slash_dates = slash_dates or _used_slash_dates(raw_value)
            dates[name] = moment.isoformat() if moment else None

        status_text = clean_text(_cell(row, mapping, "status"), 60)
        priority_text = clean_text(_cell(row, mapping, "priority"), 40)
        resolved_at = dates["resolved"] or dates["closed"]
        state = status_state(status_text, has_resolution_date=bool(resolved_at))
        if status_text and state == "unknown":
            unreadable_status += 1
        result.rows.append({
            "ticket_id": ticket_id,
            "subject": clean_text(_cell(row, mapping, "subject"), MAX_SUBJECT_CHARS),
            "description": clean_text(_cell(row, mapping, "description"), MAX_DESCRIPTION_CHARS),
            "thread": clean_excerpt(_cell(row, mapping, "description")),
            "tags": clean_text(_cell(row, mapping, "tags"), MAX_TAGS_CHARS) or None,
            "status": status_text or None,
            "state": state,
            "priority": priority_text or None,
            "priority_level": priority_level(priority_text),
            "assignee": clean_text(_cell(row, mapping, "assignee"), 80) or None,
            "group": clean_text(_cell(row, mapping, "group"), 80) or None,
            "category": clean_text(_cell(row, mapping, "category"), 80) or None,
            "sub_category": clean_text(_cell(row, mapping, "sub_category"), 80) or None,
            "ticket_type": clean_text(_cell(row, mapping, "ticket_type"), 40) or None,
            "impact": clean_text(_cell(row, mapping, "impact"), 40) or None,
            "urgency": clean_text(_cell(row, mapping, "urgency"), 40) or None,
            "business_impact": clean_text(_cell(row, mapping, "business_impact"), 200) or None,
            "impacted_locations": clean_text(_cell(row, mapping, "impacted_locations"), 120) or None,
            "customers_impacted": clean_text(_cell(row, mapping, "customers_impacted"), 20) or None,
            "first_response_breached": (
                sla_breached(clean_text(_cell(row, mapping, "first_response"), 60)) if "first_response" in mapping else None
            ),
            "created": dates["created"],
            "due": dates["due"],
            "resolved": resolved_at,
            "sla_breached": sla_breached(clean_text(_cell(row, mapping, "sla_status"), 60)) if "sla_status" in mapping else None,
        })

    if missing_id_rows:
        shown = ", ".join(str(n) for n in missing_id_rows[:_MAX_LISTED]) + (" …" if len(missing_id_rows) > _MAX_LISTED else "")
        result.warnings.append(_issue(
            "missing_ticket_id_rows", f"{len(missing_id_rows)} row(s) have no ticket ID and were skipped (rows {shown}).",
            count=len(missing_id_rows),
        ))
    if duplicates:
        shown = ", ".join(dict.fromkeys(duplicates[:_MAX_LISTED]))
        result.warnings.append(_issue(
            "duplicate_ticket_ids",
            f"{len(duplicates)} row(s) repeat a ticket ID that appeared earlier and were skipped (e.g. {shown}). "
            "The first row for each ticket is used.",
            count=len(duplicates),
        ))
    for name, count in bad_dates.items():
        result.warnings.append(_issue(
            "invalid_dates", f"{count} “{_FIELD_LABELS[name]}” value(s) could not be read as a date and were left empty.",
            field=name, count=count,
        ))
    if slash_dates:
        result.warnings.append(_issue(
            "ambiguous_dates", "Dates written like 03/04/2026 were read as month/day/year, and dates without a time "
            "zone as UTC."
        ))
    if unreadable_status:
        result.warnings.append(_issue(
            "unrecognized_status",
            f"{unreadable_status} ticket(s) have a status the report does not recognize, so they are not counted as "
            "open or resolved.",
            count=unreadable_status,
        ))
    for name in ("status", "priority", "assignee", "created", "due"):
        if name not in mapping:
            result.warnings.append(_issue(
                f"no_{name}_column", f"No {_FIELD_LABELS[name]} column was found; anything that depends on it is unavailable.",
                field=name,
            ))
    result.stats = {"duplicates_skipped": len(duplicates), "rows_without_id": len(missing_id_rows)}

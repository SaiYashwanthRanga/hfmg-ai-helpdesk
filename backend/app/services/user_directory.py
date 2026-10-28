"""Employee directory: look a caller up by phone number, work email or extension.

Built from the phone system's users.csv export, or from data/directory_merged.csv, which
backend/build_directory.py makes from that export plus the mailbox export and which says, for
every record, what it is (a person, a shared line, a service, a test) and who the person is.
A lookup answers "who might this be", never "who is this": a match is a hint for the
conversation, not proof of identity, and many entries are shared lines (exam rooms, front
desks), not people. Only the employee call flow consults it (CALL_FLOW.md 2.2).

The file is read once, off the event loop, into an immutable snapshot; lookups are plain
dict reads, safe from any number of concurrent requests or threads. Nothing here logs a
name, number or address: only counts and row numbers.
"""

import asyncio
import csv
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from fastapi import Request

from app.voice import names

logger = logging.getLogger("hfmg.directory")

#: Columns the file must have (matched case-insensitively). Anything else is optional.
REQUIRED_COLUMNS = ("Name", "Email", "Phone Number", "Extension", "Status")
_TEAM, _LOCATION, _DEACTIVATED = "Team", "Location", "Deactivated Date"
#: Only the merged directory has these. "Account Type" being present is what marks a file as merged; a record
#: that exists only as a mailbox ("Record Source" mailbox_only) has no phone-system account to be live on.
_ACCOUNT_TYPE, _RECORD_SOURCE, _MAILBOX_ONLY = "Account Type", "Record Source", "mailbox_only"
#: The merged directory's account type of a record that is a person (shared lines, services, tests and the like are not).
_PERSON = "person"
#: One word of a person's real name: letters (any alphabet), then letters, apostrophes, hyphens or periods.
_REAL_NAME_WORD = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’.\-])*$")
#: A trailing word that is not part of the family name: "John Smith Jr." is a Smith.
_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})


class LookupStatus(str, Enum):
    MATCHED = "matched"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DirectoryEntry:
    """The fields a call workflow may use. Not proof of who is calling."""

    name: str  # the phone system's label ("1001 Exec First Last"), not a clean person name
    email: str | None
    extension: str | None
    teams: tuple[str, ...]
    location: str | None
    active: bool
    source_row: int  # line in the CSV, for diagnostics
    # Only from the merged directory; None on a plain phone-system export, whose entries are what they always were.
    account_type: str | None = None  # person, likely_person, shared_line, call_park, shared_or_service, system_account, ...
    display_name: str | None = None  # the person's clean name, from the mailbox
    phone: str | None = None  # E.164
    mailbox_email: str | None = None  # the real mailbox address; `email` is the phone system's
    department: str | None = None
    site: str | None = None
    job_title: str | None = None
    credentials: str | None = None
    is_provider: bool | None = None
    status: str | None = None  # the Status cell as written ("Active", "Inactive", "Unknown")
    match_confidence: str | None = None  # how sure the merge is that this line and that mailbox are one person


@dataclass(frozen=True)
class LookupResult:
    status: LookupStatus
    entry: DirectoryEntry | None = None  # only when matched
    candidate_count: int = 0  # only when ambiguous; the candidates themselves are never returned
    reason: str | None = None  # why not matched, as a short code

    @property
    def matched(self) -> bool:
        return self.status is LookupStatus.MATCHED


@dataclass(frozen=True)
class DirectoryLoadReport:
    """What loading found. Counts and row numbers only: nothing that identifies an employee."""

    total_rows: int
    loaded_records: int
    rejected_rows: tuple[tuple[int, str], ...]  # (line, reason): wrong_field_count, csv_error, missing_name
    missing_phone: int
    missing_email: int
    missing_extension: int
    missing_status: int
    invalid_phones: int
    invalid_emails: int
    invalid_extensions: int
    inactive_records: int
    records_without_identifiers: int
    duplicate_phone_numbers: int  # distinct numbers shared by more than one record
    duplicate_emails: int
    duplicate_extensions: int
    ambiguous_phone_numbers: int  # of those, how many a lookup cannot resolve
    ambiguous_emails: int
    ambiguous_extensions: int

    def summary(self) -> dict[str, int]:
        reasons = {"wrong_field_count": 0, "csv_error": 0, "missing_name": 0}
        for _, reason in self.rejected_rows:
            reasons[reason] = reasons.get(reason, 0) + 1
        return {
            "total_rows": self.total_rows,
            "loaded_records": self.loaded_records,
            "rejected_rows": len(self.rejected_rows),
            "rejected_wrong_field_count": reasons["wrong_field_count"],
            "rejected_csv_error": reasons["csv_error"],
            "rejected_missing_name": reasons["missing_name"],
            "missing_phone": self.missing_phone,
            "missing_email": self.missing_email,
            "missing_extension": self.missing_extension,
            "missing_status": self.missing_status,
            "invalid_phones": self.invalid_phones,
            "invalid_emails": self.invalid_emails,
            "invalid_extensions": self.invalid_extensions,
            "inactive_records": self.inactive_records,
            "records_without_identifiers": self.records_without_identifiers,
            "duplicate_phone_numbers": self.duplicate_phone_numbers,
            "duplicate_emails": self.duplicate_emails,
            "duplicate_extensions": self.duplicate_extensions,
            "ambiguous_phone_numbers": self.ambiguous_phone_numbers,
            "ambiguous_emails": self.ambiguous_emails,
            "ambiguous_extensions": self.ambiguous_extensions,
        }


class DirectoryLoadError(Exception):
    """The directory could not be loaded at all. `reason` is a short code."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


# --- normalization --------------------------------------------------------------------

_NANP = re.compile(r"^[2-9]\d{2}[2-9]\d{2}\d{4}$")  # area code and exchange cannot start with 0 or 1
_PHONE_PUNCTUATION = re.compile(r"[\s().\-]")
_URI_PREFIX = re.compile(r"^(?:sips?|tel):", re.IGNORECASE)
_PHONE_EXTENSION_SUFFIX = re.compile(r"\s*(?:x|ext\.?)\s*\d+$", re.IGNORECASE)
_EMAIL = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
_EXTENSION_PREFIX = re.compile(r"^\s*(?:ext\.?|x|#)\s*", re.IGNORECASE)


def _us(national: str) -> str | None:
    return f"+1{national}" if _NANP.match(national) else None


def normalize_phone(raw: str | None) -> str | None:
    """E.164, or None when it is not a number we can be sure of.

    US numbers (10 digits, or 1 + 10, with or without punctuation or a +1) become +1XXXXXXXXXX
    and must be valid North American numbers. A number written with a different country code
    ("+44...", "0044...") is kept as +<digits> without further rules. A bare number that is
    neither 10 digits nor 1 + 10 is not guessed at: two different numbers are never merged.
    """
    text = (raw or "").strip()
    if not text:
        return None
    text = _URI_PREFIX.sub("", text).split("@", 1)[0]  # sip:+1845...@gateway, tel:+1845...
    text = _PHONE_EXTENSION_SUFFIX.sub("", text)
    international = text.startswith("+")
    if international:
        text = text[1:]
    elif text.startswith("00"):
        international, text = True, text[2:]
    digits = _PHONE_PUNCTUATION.sub("", text)
    if not digits.isdigit():
        return None
    if international:
        if digits.startswith("1"):
            return _us(digits[1:])
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return _us(digits)
    if len(digits) == 11 and digits.startswith("1"):
        return _us(digits[1:])
    return None


def normalize_email(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if text.lower().startswith("mailto:"):
        text = text[7:]
    text = text.lower()
    return text if _EMAIL.match(text) else None


def normalize_extension(raw: str | None) -> str | None:
    """Digits only (leading zeros kept), 2-6 of them; "x1004" and "ext. 1004" are 1004."""
    text = _EXTENSION_PREFIX.sub("", (raw or "").strip())
    return text if text.isdigit() and 2 <= len(text) <= 6 else None


# --- loading --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    records: tuple[DirectoryEntry, ...]
    by_phone: dict[str, tuple[int, ...]]
    by_email: dict[str, tuple[int, ...]]
    by_extension: dict[str, tuple[int, ...]]
    people: tuple[tuple[str, int], ...]  # (letters of one word of the name, position), for each live, real person
    report: DirectoryLoadReport


def clean_display_name(raw: str | None) -> str | None:
    """A mailbox display name the agent may say aloud, else None: two to four words, each made of letters (and
    an apostrophe, hyphen or period), the first and last at least two characters. Anything else (a digit, a
    single word, an address, a stray symbol) is not guessed at: the agent asks who is calling."""
    words = (raw or "").split()
    if not 2 <= len(words) <= 4 or len(" ".join(words)) > 60:
        return None
    if not all(_REAL_NAME_WORD.match(word) for word in words):
        return None
    if min(len(words[0].rstrip(".")), len(words[-1].rstrip("."))) < 2:
        return None
    return " ".join(words)


def _name_keys(entry: DirectoryEntry) -> tuple[str, ...]:
    """The letters of every part of the name of an entry that is a live person with a clean display name, else none:
    a shared line, a service, a test, a call park, a deactivated user and a mailbox with no phone account have none.

    Every word counts, not just the last: the directory does not say which word is the family name ("Ranga Sai
    Yashwanth" and "Yashwanth Ranga" are the same person to a caller who says "Ranga"). A suffix ("Jr"), a lone
    initial and a hyphenated name's whole and parts ("Smith-Jones", "Smith", "Jones") are handled."""
    if entry.account_type != _PERSON or not entry.active:
        return ()
    name = clean_display_name(entry.display_name)
    if name is None:
        return ()
    keys: dict[str, None] = {}
    for word in name.split():
        for part in (word, *word.split("-")):
            key = names.letters(part)
            if len(key) >= 2 and key not in _NAME_SUFFIXES:
                keys[key] = None
    return tuple(keys)


def _teams(cell: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in cell.split(",") if part.strip()))


def _merged_fields(cell, row: list[str], phone: str | None, mailbox_email: str | None) -> dict:
    """The entry fields only the merged directory has. Empty when the file has no "Account Type" column."""
    provider = cell(row, "Is Provider").upper()
    return dict(
        account_type=cell(row, _ACCOUNT_TYPE).lower(),
        display_name=cell(row, "Display Name") or None,
        phone=phone,
        mailbox_email=mailbox_email,
        department=cell(row, "Department") or None,
        site=cell(row, "Site") or None,
        job_title=cell(row, "Job Title") or None,
        credentials=cell(row, "Credentials") or None,
        is_provider={"Y": True, "N": False}.get(provider),
        status=cell(row, "Status") or None,
        match_confidence=cell(row, "Match Confidence").lower() or None,
    )


def _resolve(entries: list[DirectoryEntry]) -> LookupResult:
    """One record, or why there isn't one. A deactivated duplicate does not make a live
    record ambiguous (a number reassigned to a new employee); if nothing is live, the
    deactivated record is still reported, as inactive."""
    if not entries:
        return LookupResult(LookupStatus.NOT_FOUND, reason="no_match")
    live = [e for e in entries if e.active]
    pool = live or entries
    if len(pool) == 1:
        return LookupResult(LookupStatus.MATCHED, entry=pool[0])
    return LookupResult(
        LookupStatus.AMBIGUOUS,
        candidate_count=len(pool),
        reason="duplicate_active" if live else "duplicate_inactive",
    )


def _index(keyed: dict[str, list[int]]) -> dict[str, tuple[int, ...]]:
    return {key: tuple(positions) for key, positions in keyed.items()}


def load_snapshot(path: Path) -> _Snapshot:
    """Read and validate the file. Blocking: call it off the event loop."""
    try:
        handle = path.open(encoding="utf-8-sig", newline="")
    except FileNotFoundError:
        raise DirectoryLoadError("file_not_found", f"directory file not found: {path}") from None
    except OSError as exc:
        raise DirectoryLoadError("unreadable", f"directory file cannot be read: {path} ({type(exc).__name__})") from None

    with handle:
        try:
            return _parse(csv.reader(handle, strict=True), path)
        except UnicodeDecodeError:
            raise DirectoryLoadError("encoding", f"directory file is not valid UTF-8: {path}") from None


def _parse(reader, path: Path) -> _Snapshot:
    header = next(reader, None)
    if not header or not any(cell.strip() for cell in header):
        raise DirectoryLoadError("empty", f"directory file is empty: {path}")
    column = {" ".join(cell.strip().lower().split()): i for i, cell in enumerate(header)}
    missing = [name for name in REQUIRED_COLUMNS if name.lower() not in column]
    if missing:
        raise DirectoryLoadError("missing_columns", f"directory file lacks required columns: {', '.join(missing)}")
    merged = _ACCOUNT_TYPE.lower() in column

    def cell(row: list[str], name: str) -> str:
        position = column.get(name.lower())
        return row[position].strip() if position is not None else ""

    records: list[DirectoryEntry] = []
    by_phone: dict[str, list[int]] = {}
    by_email: dict[str, list[int]] = {}
    by_extension: dict[str, list[int]] = {}
    rejected: list[tuple[int, str]] = []
    counts = dict(
        total=0, missing_phone=0, missing_email=0, missing_extension=0, missing_status=0,
        invalid_phones=0, invalid_emails=0, invalid_extensions=0, inactive=0, no_identifiers=0,
    )

    while True:
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error:
            counts["total"] += 1
            rejected.append((reader.line_num, "csv_error"))
            continue
        if not any(value.strip() for value in row):
            continue  # a blank line
        counts["total"] += 1
        line = reader.line_num
        if len(row) != len(header):
            rejected.append((line, "wrong_field_count"))
            continue
        name = cell(row, "Name")
        if not name:
            rejected.append((line, "missing_name"))
            continue

        raw_phone, raw_email, raw_ext, status = cell(row, "Phone Number"), cell(row, "Email"), cell(row, "Extension"), cell(row, "Status")
        phone, email, extension = normalize_phone(raw_phone), normalize_email(raw_email), normalize_extension(raw_ext)
        counts["missing_phone"] += not raw_phone
        counts["missing_email"] += not raw_email
        counts["missing_extension"] += not raw_ext
        counts["missing_status"] += not status
        counts["invalid_phones"] += bool(raw_phone) and phone is None
        counts["invalid_emails"] += bool(raw_email) and email is None
        counts["invalid_extensions"] += bool(raw_ext) and extension is None
        # Live only when explicitly active and not deactivated; a blank status is not assumed live. A record that
        # exists only as a mailbox is never live, whatever status it was given: it has no phone-system account.
        active = (
            status.lower() == "active" and not cell(row, _DEACTIVATED)
            and cell(row, _RECORD_SOURCE).lower() != _MAILBOX_ONLY
        )
        counts["inactive"] += not active
        counts["no_identifiers"] += not (phone or email or extension)
        mailbox_email = normalize_email(cell(row, "Mailbox Email")) if merged else None

        position = len(records)
        records.append(
            DirectoryEntry(
                name=name, email=email, extension=extension, teams=_teams(cell(row, _TEAM)),
                location=cell(row, _LOCATION) or None, active=active, source_row=line,
                **(_merged_fields(cell, row, phone, mailbox_email) if merged else {}),
            )
        )
        for key, index in ((phone, by_phone), (email, by_email), (extension, by_extension), (mailbox_email, by_email)):
            if key and position not in index.get(key, ()):  # one record is listed once, even if both addresses match
                index.setdefault(key, []).append(position)

    if not records:
        raise DirectoryLoadError("empty", f"directory file has no usable records: {path}")

    def duplicates(index: dict[str, list[int]]) -> tuple[int, int]:
        shared = [positions for positions in index.values() if len(positions) > 1]
        ambiguous = sum(_resolve([records[i] for i in positions]).status is LookupStatus.AMBIGUOUS for positions in shared)
        return len(shared), ambiguous

    dup_phone, amb_phone = duplicates(by_phone)
    dup_email, amb_email = duplicates(by_email)
    dup_ext, amb_ext = duplicates(by_extension)
    report = DirectoryLoadReport(
        total_rows=counts["total"], loaded_records=len(records), rejected_rows=tuple(rejected),
        missing_phone=counts["missing_phone"], missing_email=counts["missing_email"],
        missing_extension=counts["missing_extension"], missing_status=counts["missing_status"],
        invalid_phones=counts["invalid_phones"], invalid_emails=counts["invalid_emails"],
        invalid_extensions=counts["invalid_extensions"], inactive_records=counts["inactive"],
        records_without_identifiers=counts["no_identifiers"],
        duplicate_phone_numbers=dup_phone, duplicate_emails=dup_email, duplicate_extensions=dup_ext,
        ambiguous_phone_numbers=amb_phone, ambiguous_emails=amb_email, ambiguous_extensions=amb_ext,
    )
    people = tuple((key, i) for i, record in enumerate(records) for key in _name_keys(record))
    return _Snapshot(tuple(records), _index(by_phone), _index(by_email), _index(by_extension), people, report)


# --- the service ----------------------------------------------------------------------


class UserDirectoryService:
    """Holds the loaded directory and answers lookups. Never raises from a lookup."""

    def __init__(self) -> None:
        self._snapshot: _Snapshot | None = None
        self._unavailable_reason = "not_loaded"

    @property
    def available(self) -> bool:
        return self._snapshot is not None

    @property
    def unavailable_reason(self) -> str | None:
        return None if self.available else self._unavailable_reason

    @property
    def report(self) -> DirectoryLoadReport | None:
        return self._snapshot.report if self._snapshot else None

    async def load(self, path: str | Path | None) -> None:
        """Read the file once, off the event loop. A failure is logged and leaves the service
        unavailable (or keeps the previous good directory on a reload); it never raises, so a
        bad file cannot stop the application from starting."""
        if not path:
            self._unavailable_reason = "not_configured"
            logger.info("User directory not configured (USER_DIRECTORY_CSV_PATH is empty)")
            return
        try:
            snapshot = await asyncio.to_thread(load_snapshot, Path(path))
        except DirectoryLoadError as exc:
            self._fail(exc.reason, str(exc))
            return
        except Exception as exc:  # unexpected: still must not take the app down
            self._fail("load_error", f"unexpected {type(exc).__name__} loading the directory")
            return
        self._snapshot = snapshot  # one assignment: readers see the old directory or the new, never a mix
        rejected = len(snapshot.report.rejected_rows)
        logger.info(
            "User directory loaded: %d records, %d rows rejected, %d inactive",
            snapshot.report.loaded_records, rejected, snapshot.report.inactive_records,
        )
        if rejected:
            logger.warning("User directory: %d malformed or incomplete rows were not loaded (lines %s)",
                           rejected, ", ".join(str(line) for line, _ in snapshot.report.rejected_rows[:20]))

    def _fail(self, reason: str, message: str) -> None:
        if self._snapshot is None:
            self._unavailable_reason = reason
            logger.error("User directory unavailable (%s): %s", reason, message)
        else:
            logger.error("User directory reload failed (%s): %s; keeping the previously loaded directory", reason, message)

    def _lookup(self, index_name: str, key: str | None, invalid_reason: str) -> LookupResult:
        snapshot = self._snapshot
        if snapshot is None:
            return LookupResult(LookupStatus.UNAVAILABLE, reason=self._unavailable_reason)
        if key is None:
            return LookupResult(LookupStatus.NOT_FOUND, reason=invalid_reason)
        positions = getattr(snapshot, index_name).get(key, ())
        return _resolve([snapshot.records[i] for i in positions])

    def find_by_phone(self, phone: str | None) -> LookupResult:
        return self._lookup("by_phone", normalize_phone(phone), "invalid_phone")

    def find_by_email(self, email: str | None) -> LookupResult:
        return self._lookup("by_email", normalize_email(email), "invalid_email")

    def find_by_extension(self, extension: str | None) -> LookupResult:
        return self._lookup("by_extension", normalize_extension(extension), "invalid_extension")

    def find_by_last_name(self, last_name: str | None) -> tuple[DirectoryEntry, ...]:
        """The live people whose clean display name has this name as any of its words (first, middle or last, so
        the order the directory writes a name in does not matter), in file order; none when it is blank, unknown or
        the directory is unavailable.

        Only entries that are a real person (a `person` of the merged directory, active, with a name that is
        safe to say) are ever returned: never a shared line, service, test or call park. Matching is
        case-insensitive and as tolerant of a recognizer's slip as names are everywhere else (names.same_name_part);
        a name that matches exactly is not mixed up with near misses. What comes back is a list of people the
        caller may be asked about, not an answer to who is calling."""
        snapshot = self._snapshot
        heard = names.letters(last_name)
        if snapshot is None or not heard:
            return ()
        exact = [i for key, i in snapshot.people if key == heard]
        positions = exact or [i for key, i in snapshot.people if names.same_name_part(heard, key)]
        return tuple(snapshot.records[i] for i in sorted(set(positions)))

    def find_by_identifier(self, identifier: str | None) -> LookupResult:
        """Look up by whatever the caller gave: an email address, a phone number (including a
        caller-ID such as "+18455550142" or "sip:+18455550142@host"), or an extension. The file
        has no employee-ID column, so there is no other kind of identifier."""
        text = (identifier or "").strip()
        if "@" in text and not _URI_PREFIX.match(text):
            return self.find_by_email(text)
        if normalize_phone(text) is not None:
            return self.find_by_phone(text)
        if normalize_extension(text) is not None:
            return self.find_by_extension(text)
        if self._snapshot is None:
            return LookupResult(LookupStatus.UNAVAILABLE, reason=self._unavailable_reason)
        return LookupResult(LookupStatus.NOT_FOUND, reason="unrecognized_identifier")


def get_user_directory(request: Request) -> UserDirectoryService:
    """FastAPI dependency: the directory loaded at startup, or an unavailable one."""
    service = getattr(request.app.state, "user_directory", None)
    return service if service is not None else UserDirectoryService()

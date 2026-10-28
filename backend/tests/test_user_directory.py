"""Employee directory service. Every record here is synthetic (example.test, 555 numbers)."""

import asyncio
import csv
import hashlib
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.main import app
from app.services import user_directory
from app.services.user_directory import (
    LookupStatus,
    UserDirectoryService,
    get_user_directory,
    normalize_email,
    normalize_extension,
    normalize_phone,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

HEADER = [
    "Name", "Email", "Phone Number", "Extension", "Team", "Role", "Status", "Created Date",
    "Updated Date", "Deactivated Date", "Location", "Time Zone", "Voice-Enabled",
]
P1, P2, P3 = "(845) 555-0101", "(845) 555-0102", "(845) 555-0103"


@pytest.fixture(autouse=True)
def _truncate_tables():
    """Overrides conftest's per-test TRUNCATE: nothing in this module touches the database."""
    yield


def row(name="Test Person", email=None, phone="", ext="", team="Service", status="Active",
        deactivated="", location="Test Clinic") -> list[str]:
    email = f"{name.lower().replace(' ', '.')}@example.test" if email is None else email
    return [name, email, phone, ext, team, "User", status, "", "", deactivated, location, "", "Y"]


def write_csv(tmp_path: Path, rows: list[list[str]], *, header=HEADER, bom=False, name="users.csv") -> Path:
    path = tmp_path / name
    with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    return path


#: The columns of data/directory_merged.csv (backend/build_directory.py): the phone system's, then the mailbox side's.
MERGED_HEADER = [
    "Name", "Email", "Phone Number", "Extension", "Team", "Role", "Status", "Deactivated Date", "Location",
    "Voice-Enabled", "Account Type", "Display Name", "Credentials", "Job Title", "Is Provider", "Department", "Site",
    "Mailbox Email", "Additional Mailbox Emails", "Original Email", "Signature Emails", "Signature Phones",
    "Signature Extensions", "Mailbox Role", "Match Method", "Match Confidence", "Review Notes",
]


def merged_row(label="Test Person", *, kind="person", display="", phone="", ext="", email=None, status="Active",
               team="Service", location="Test Clinic", credentials="", title="", provider="N", department="",
               site="", mailbox="", method="", confidence="", record_source=None) -> list[str]:
    """One row of the merged directory. `record_source` adds that column (a newer build writes it)."""
    email = f"{label.lower().replace(' ', '.')}@example.test" if email is None else email
    cells = {
        "Name": label, "Email": email, "Phone Number": phone, "Extension": ext, "Team": team, "Role": "User",
        "Status": status, "Deactivated Date": "", "Location": location, "Voice-Enabled": "Y", "Account Type": kind,
        "Display Name": display, "Credentials": credentials, "Job Title": title, "Is Provider": provider,
        "Department": department, "Site": site, "Mailbox Email": mailbox, "Match Method": method,
        "Match Confidence": confidence,
    }
    values = [cells.get(column, "") for column in MERGED_HEADER]
    return values if record_source is None else [*values, record_source]


async def loaded(path: Path) -> UserDirectoryService:
    service = UserDirectoryService()
    await service.load(path)
    return service


# --- loading ----------------------------------------------------------------------------


async def test_a_valid_csv_loads_and_reports_what_it_found(tmp_path):
    path = write_csv(tmp_path, [
        row("Ann Example", phone=P1, ext="1001", team="Service, Front Desk, Service"),
        row("Bob Example", phone="", ext="1002", team=""),
    ])

    service = await loaded(path)

    assert service.available and service.unavailable_reason is None
    report = service.report
    assert (report.total_rows, report.loaded_records, report.rejected_rows) == (2, 2, ())
    assert (report.missing_phone, report.missing_email, report.missing_extension) == (1, 0, 0)
    entry = service.find_by_phone(P1).entry
    assert (entry.name, entry.extension, entry.location, entry.active) == ("Ann Example", "1001", "Test Clinic", True)
    assert entry.teams == ("Service", "Front Desk")  # a multi-team cell, deduplicated, in order
    assert service.find_by_extension("1002").entry.teams == ()


async def test_a_utf8_bom_is_ignored_and_other_utf8_is_kept(tmp_path):
    path = write_csv(tmp_path, [row("Zoë Example", phone=P1)], bom=True)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")

    service = await loaded(path)

    assert service.available
    assert service.find_by_phone(P1).entry.name == "Zoë Example"


async def test_missing_required_columns_make_the_directory_unavailable(tmp_path):
    header = [h for h in HEADER if h != "Phone Number"]
    path = write_csv(tmp_path, [[v for i, v in enumerate(row("Ann Example")) if HEADER[i] != "Phone Number"]], header=header)

    service = await loaded(path)

    assert not service.available and service.unavailable_reason == "missing_columns"
    assert service.find_by_email("ann.example@example.test").status is LookupStatus.UNAVAILABLE


async def test_header_names_match_regardless_of_case_and_spacing(tmp_path):
    header = [h.upper() if i % 2 else f"  {h.lower()} " for i, h in enumerate(HEADER)]
    service = await loaded(write_csv(tmp_path, [row("Ann Example", phone=P1)], header=header))

    assert service.find_by_phone(P1).matched


async def test_malformed_rows_are_reported_not_loaded_and_do_not_hide_the_good_ones(tmp_path):
    path = tmp_path / "users.csv"
    good = ",".join(row("Good Person", phone=P1))
    wrong_width = "Surname, MD,sm@example.test,,1003,Service,User,Active,,,,Clinic,,Y"  # an unquoted comma in a name
    path.write_text("\n".join([",".join(HEADER), good, wrong_width, ",".join(row("Another Person", phone=P2))]) + "\n", encoding="utf-8")

    service = await loaded(path)

    assert service.report.loaded_records == 2
    assert service.report.rejected_rows == ((3, "wrong_field_count"),)
    assert service.find_by_phone(P1).matched and service.find_by_phone(P2).matched
    assert service.find_by_email("sm@example.test").status is LookupStatus.NOT_FOUND  # reported, never half-loaded


async def test_a_broken_quote_is_a_rejected_row_not_a_crash(tmp_path):
    path = tmp_path / "users.csv"
    path.write_text(",".join(HEADER) + "\n" + ",".join(row("Good Person", phone=P1)) + '\n"Unterminated,Name,,\n', encoding="utf-8")

    service = await loaded(path)

    assert service.available and service.report.loaded_records == 1
    assert [reason for _, reason in service.report.rejected_rows] == ["csv_error"]


async def test_a_row_without_a_name_is_rejected_but_a_missing_phone_is_normal(tmp_path):
    service = await loaded(write_csv(tmp_path, [row("", email="x@example.test", phone=P1), row("Has Name", phone="")]))

    assert service.report.rejected_rows == ((2, "missing_name"),)
    assert service.report.loaded_records == 1 and service.report.missing_phone == 1
    assert service.find_by_phone(P1).status is LookupStatus.NOT_FOUND


async def test_invalid_values_are_counted_and_unindexed_not_dropped_or_guessed(tmp_path):
    service = await loaded(write_csv(tmp_path, [
        row("Bad Phone", phone="555-0142", email="not-an-email", ext="12ab"),
        row("Fine Person", phone=P1, ext="1001"),
    ]))

    report = service.report
    assert (report.invalid_phones, report.invalid_emails, report.invalid_extensions) == (1, 1, 1)
    assert report.loaded_records == 2 and report.records_without_identifiers == 1
    assert service.find_by_extension("1001").matched


async def test_blank_lines_are_skipped_not_counted(tmp_path):
    path = tmp_path / "users.csv"
    path.write_text(",".join(HEADER) + "\n\n" + ",".join(row("Ann Example", phone=P1)) + "\n\n\n", encoding="utf-8")

    service = await loaded(path)

    assert (service.report.total_rows, service.report.loaded_records) == (1, 1)


async def test_loading_leaves_the_source_file_untouched(tmp_path):
    path = write_csv(tmp_path, [row("Ann Example", phone=P1), row("Surname, MD", phone=P2)])
    before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)

    await loaded(path)

    assert (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == before


# --- phone normalization ------------------------------------------------------------------


@pytest.mark.parametrize("raw", [
    "(845) 555-0142", "845-555-0142", "845.555.0142", "8455550142", "845 555 0142", "1-845-555-0142",
    "18455550142", "+18455550142", "+1 (845) 555-0142", "tel:+18455550142", "sip:+18455550142@gw.example.test",
    " (845) 555-0142 x101 ", "(845) 555-0142 ext. 12", "001 845 555 0142",
])
async def test_us_phone_formats_normalize_to_one_e164_number(raw):
    assert normalize_phone(raw) == "+18455550142"


@pytest.mark.parametrize("raw", [
    None, "", "   ", "anonymous", "unknown", "abc", "555-0142", "845555014", "84555501422",
    "(045) 555-0142", "(145) 555-0142", "(845) 055-0142", "(845) 155-0142", "+1845555014", "+18455550142abc",
    "845-555-0142-9999", "+44", "+0",
])
async def test_numbers_that_cannot_be_trusted_do_not_normalize(raw):
    assert normalize_phone(raw) is None


@pytest.mark.parametrize("raw, expected", [
    ("+442079460958", "+442079460958"), ("0044 20 7946 0958", "+442079460958"), ("+91 98765 43210", "+919876543210"),
])
async def test_other_country_codes_are_kept_as_international_numbers(raw, expected):
    assert normalize_phone(raw) == expected


async def test_a_foreign_number_never_matches_a_us_number_that_shares_its_digits(tmp_path):
    service = await loaded(write_csv(tmp_path, [row("Ann Example", phone="(207) 946-0958")]))

    assert service.find_by_phone("+442079460958").status is LookupStatus.NOT_FOUND
    assert service.find_by_phone("(207) 946-0958").matched
    assert service.find_by_phone("+1 207 946 0958").matched


async def test_the_same_number_in_different_formats_matches_the_same_record(tmp_path):
    service = await loaded(write_csv(tmp_path, [row("Ann Example", phone="(845) 555-0142")]))

    for caller_id in ("+18455550142", "18455550142", "8455550142", "845.555.0142", "sip:+18455550142@gw.example.test"):
        assert service.find_by_phone(caller_id).entry.name == "Ann Example"


# --- lookups ----------------------------------------------------------------------------------


async def test_a_unique_phone_matches_and_an_unknown_one_does_not(tmp_path):
    service = await loaded(write_csv(tmp_path, [row("Ann Example", phone=P1)]))

    hit, miss = service.find_by_phone(P1), service.find_by_phone("(845) 555-0199")

    assert (hit.status, hit.candidate_count, hit.reason) == (LookupStatus.MATCHED, 0, None)
    assert (miss.status, miss.entry, miss.reason) == (LookupStatus.NOT_FOUND, None, "no_match")


async def test_an_unusable_query_is_not_found_with_a_reason_and_never_raises(tmp_path):
    service = await loaded(write_csv(tmp_path, [row("Ann Example", phone=P1)]))

    for call, bad, reason in ((service.find_by_phone, "anonymous", "invalid_phone"),
                              (service.find_by_phone, None, "invalid_phone"),
                              (service.find_by_email, "not an email", "invalid_email"),
                              (service.find_by_extension, "1", "invalid_extension"),
                              (service.find_by_extension, None, "invalid_extension")):
        result = call(bad)
        assert (result.status, result.reason) == (LookupStatus.NOT_FOUND, reason)


async def test_a_phone_shared_by_two_people_is_ambiguous_and_names_neither(tmp_path):
    service = await loaded(write_csv(tmp_path, [row("Ann Example", phone=P1), row("Bob Example", phone=P1), row("Cy Example", phone=P2)]))

    shared = service.find_by_phone(P1)

    assert (shared.status, shared.entry, shared.candidate_count, shared.reason) == (LookupStatus.AMBIGUOUS, None, 2, "duplicate_active")
    assert service.find_by_phone(P2).matched  # the unique one beside it is unaffected
    assert (service.report.duplicate_phone_numbers, service.report.ambiguous_phone_numbers) == (1, 1)


async def test_email_lookup_ignores_case_and_surrounding_space_and_flags_duplicates(tmp_path):
    service = await loaded(write_csv(tmp_path, [
        row("Ann Example", email="Ann.Example@Example.TEST"),
        row("Bob One", email="shared@example.test"),
        row("Bob Two", email="SHARED@example.test"),
    ]))

    assert service.find_by_email("  ann.example@example.test ").entry.name == "Ann Example"
    assert service.find_by_email("mailto:ANN.EXAMPLE@example.test").matched
    shared = service.find_by_email("shared@example.test")
    assert (shared.status, shared.candidate_count) == (LookupStatus.AMBIGUOUS, 2)
    assert service.find_by_email("nobody@example.test").status is LookupStatus.NOT_FOUND


@pytest.mark.parametrize("raw", ["1004", " 1004 ", "x1004", "ext. 1004", "#1004"])
async def test_extension_formats(raw):
    assert normalize_extension(raw) == "1004"


@pytest.mark.parametrize("raw", [None, "", "1", "1234567", "12a4", "x"])
async def test_extensions_that_are_not_extensions(raw):
    assert normalize_extension(raw) is None


async def test_extension_lookup_and_duplicate_extensions(tmp_path):
    service = await loaded(write_csv(tmp_path, [
        row("Ann Example", ext="1001"), row("Bob One", ext="2002"), row("Bob Two", ext="2002"), row("No Ext", ext=""),
    ]))

    assert service.find_by_extension("x1001").entry.name == "Ann Example"
    assert service.find_by_extension("2002").status is LookupStatus.AMBIGUOUS
    assert service.find_by_extension("9999").status is LookupStatus.NOT_FOUND
    assert (service.report.missing_extension, service.report.duplicate_extensions) == (1, 1)


async def test_leading_zeros_in_an_extension_are_kept(tmp_path):
    service = await loaded(write_csv(tmp_path, [row("Ann Example", ext="0042"), row("Bob Example", ext="42")]))

    assert service.find_by_extension("0042").entry.name == "Ann Example"
    assert service.find_by_extension("42").entry.name == "Bob Example"


@pytest.mark.parametrize("raw, expected", [("A@B.example", "a@b.example"), (" mailto:x@y.example ", "x@y.example")])
async def test_email_normalization(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "no-at-sign", "a@b", "a b@c.example", "<a@b.example>", "a@@b.example", "a@b.example, c@d.example"])
async def test_things_that_are_not_an_email_address(raw):
    assert normalize_email(raw) is None


# --- identifiers -------------------------------------------------------------------------------


async def test_find_by_identifier_works_out_what_it_was_given(tmp_path):
    service = await loaded(write_csv(tmp_path, [row("Ann Example", phone=P1, ext="1001", email="ann@example.test")]))

    for identifier in ("ann@example.test", P1, "+18455550101", "sip:+18455550101@gw.example.test", "1001", "x1001"):
        assert service.find_by_identifier(identifier).entry.name == "Ann Example", identifier
    for identifier in ("", None, "anonymous", "just words", "12345678"):
        result = service.find_by_identifier(identifier)
        assert (result.status, result.reason) == (LookupStatus.NOT_FOUND, "unrecognized_identifier"), identifier


async def test_records_with_no_identifier_load_but_can_never_match_and_duplicates_are_ambiguous(tmp_path):
    service = await loaded(write_csv(tmp_path, [
        row("No Identifiers", email="", phone="", ext=""),
        row("Twin One", email="twin@example.test"), row("Twin Two", email="twin@example.test"),
    ]))

    assert service.report.loaded_records == 3 and service.report.records_without_identifiers == 1
    assert service.find_by_identifier("twin@example.test").status is LookupStatus.AMBIGUOUS
    assert service.find_by_identifier("").status is LookupStatus.NOT_FOUND


# --- inactive employees --------------------------------------------------------------------------


async def test_inactive_records_are_flagged_and_do_not_make_a_live_record_ambiguous(tmp_path):
    service = await loaded(write_csv(tmp_path, [
        row("Left Company", phone=P1, status="Inactive"),
        row("Live Owner", phone=P2, status="Active"), row("Former Owner", phone=P2, status="Inactive"),
        row("Gone One", phone=P3, status="Inactive"), row("Gone Two", phone=P3, status="Disabled"),
    ]))

    only_inactive = service.find_by_phone(P1)
    assert only_inactive.matched and only_inactive.entry.active is False  # reported, flagged, never presented as live
    assert service.find_by_phone(P2).entry.name == "Live Owner"
    both_gone = service.find_by_phone(P3)
    assert (both_gone.status, both_gone.reason) == (LookupStatus.AMBIGUOUS, "duplicate_inactive")
    assert service.report.inactive_records == 4


async def test_a_deactivated_date_or_a_blank_status_is_not_treated_as_live(tmp_path):
    service = await loaded(write_csv(tmp_path, [
        row("Dated Out", ext="1001", status="Active", deactivated="Mon Jan 01 2024"),
        row("No Status", ext="1002", status=""),
        row("Status Spaced", ext="1003", status="  active "),
    ]))

    assert service.find_by_extension("1001").entry.active is False
    assert service.find_by_extension("1002").entry.active is False and service.report.missing_status == 1
    assert service.find_by_extension("1003").entry.active is True


# --- unavailable ------------------------------------------------------------------------------------


@pytest.mark.parametrize("make, reason", [
    (lambda tmp: tmp / "nope.csv", "file_not_found"),
    (lambda tmp: tmp, "unreadable"),  # a directory, not a file
    (lambda tmp: "", "not_configured"),
    (lambda tmp: None, "not_configured"),
])
async def test_a_missing_or_unusable_file_is_unavailable_and_every_lookup_says_so(tmp_path, make, reason):
    service = await loaded(make(tmp_path))

    assert not service.available and service.unavailable_reason == reason and service.report is None
    for result in (service.find_by_phone(P1), service.find_by_email("a@example.test"),
                   service.find_by_extension("1001"), service.find_by_identifier("1001")):
        assert (result.status, result.entry, result.reason) == (LookupStatus.UNAVAILABLE, None, reason)


async def test_a_service_that_never_loaded_is_unavailable_not_empty():
    result = UserDirectoryService().find_by_phone(P1)

    assert (result.status, result.reason) == (LookupStatus.UNAVAILABLE, "not_loaded")


async def test_a_file_that_is_not_utf8_is_unavailable_rather_than_silently_altered(tmp_path):
    path = tmp_path / "users.csv"
    path.write_bytes(",".join(HEADER).encode() + b"\n" + "Zoë Example,z@example.test,,1001,,User,Active,,,,,,Y\n".encode("latin-1"))

    service = await loaded(path)

    assert not service.available and service.unavailable_reason == "encoding"


@pytest.mark.parametrize("content", [b"", b"\n\n", b"   \n"])
async def test_an_empty_file_is_unavailable_not_a_directory_where_nobody_exists(tmp_path, content):
    path = tmp_path / "users.csv"
    path.write_bytes(content)

    service = await loaded(path)

    assert not service.available and service.unavailable_reason == "empty"
    assert service.find_by_phone(P1).status is LookupStatus.UNAVAILABLE


async def test_a_header_with_no_usable_rows_is_unavailable(tmp_path):
    only_header = await loaded(write_csv(tmp_path, []))
    only_rejects = await loaded(write_csv(tmp_path, [row("", phone=P1)], name="rejects.csv"))

    assert only_header.unavailable_reason == only_rejects.unavailable_reason == "empty"


async def test_a_failed_reload_keeps_the_directory_that_was_already_loaded(tmp_path):
    path = write_csv(tmp_path, [row("Ann Example", phone=P1)])
    service = await loaded(path)

    path.write_text("", encoding="utf-8")
    await service.load(path)

    assert service.available and service.find_by_phone(P1).entry.name == "Ann Example"


# --- concurrency and the event loop ----------------------------------------------------------------------


async def test_loading_does_not_block_the_event_loop(tmp_path, monkeypatch):
    path = write_csv(tmp_path, [row("Ann Example", phone=P1)])
    real = user_directory.load_snapshot

    def slow(p):
        time.sleep(0.4)  # stands in for a large file
        return real(p)

    monkeypatch.setattr(user_directory, "load_snapshot", slow)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    service = await loaded(path)
    task.cancel()

    assert service.available
    assert ticks >= 10  # the loop kept running while the file was read


async def test_many_concurrent_reads_all_get_the_right_answer(tmp_path):
    service = await loaded(write_csv(tmp_path, [row(f"Person {n}", phone=f"(845) 555-{n:04d}", ext=str(1000 + n)) for n in range(100, 200)]))

    results = await asyncio.gather(*(asyncio.to_thread(service.find_by_phone, f"(845) 555-{100 + n % 100:04d}") for n in range(400)))
    on_loop = await asyncio.gather(*(asyncio.sleep(0, service.find_by_extension(str(1100 + n % 100))) for n in range(400)))

    assert all(r.matched for r in results) and all(r.matched for r in on_loop)
    assert all(r.entry.name == f"Person {100 + n % 100}" for n, r in enumerate(results))


async def test_reads_during_a_reload_see_the_old_directory_or_the_new_never_an_error(tmp_path):
    path_a = write_csv(tmp_path, [row("Version A", phone=P1)], name="a.csv")
    path_b = write_csv(tmp_path, [row("Version B", phone=P1)], name="b.csv")
    service = await loaded(path_a)
    stop, seen, errors = threading.Event(), set(), []

    def reader():
        while not stop.is_set():
            try:
                result = service.find_by_phone(P1)
                assert result.matched
                seen.add(result.entry.name)
            except Exception as exc:  # noqa: BLE001 - the test is that nothing is ever raised
                errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for n in range(20):
        await service.load(path_b if n % 2 == 0 else path_a)
    stop.set()
    for thread in threads:
        thread.join()

    assert errors == [] and seen <= {"Version A", "Version B"} and seen


# --- privacy, app wiring ------------------------------------------------------------------------------------


async def test_loading_logs_counts_but_no_names_numbers_or_addresses(tmp_path, caplog):
    path = tmp_path / "users.csv"
    path.write_text("\n".join([
        ",".join(HEADER),
        ",".join(row("Sentinel Nameson", email="sentinel.nameson@example.test", phone="(845) 555-0177", ext="7777")),
        "Sentinel Surname, MD,sentinel.md@example.test,(845) 555-0178,7778,Service,User,Active,,,,Clinic,,Y",
        '"Sentinel Broken,sentinel.broken@example.test,(845) 555-0179',
    ]) + "\n", encoding="utf-8")

    with caplog.at_level("DEBUG"):
        service = await loaded(path)
        service.find_by_phone("(845) 555-0177")
        service.find_by_email("sentinel.nameson@example.test")

    assert service.report.loaded_records == 1 and len(service.report.rejected_rows) == 2
    assert "User directory loaded" in caplog.text
    for secret in ("Sentinel", "sentinel", "555-0177", "555-0178", "555-0179", "7777", "example.test"):
        assert secret not in caplog.text


async def test_a_match_exposes_only_the_fields_the_call_workflow_needs(tmp_path):
    entry = (await loaded(write_csv(tmp_path, [row("Ann Example", phone=P1)]))).find_by_phone(P1).entry

    merged_only = {
        "account_type", "display_name", "phone", "mailbox_email", "department", "site", "job_title", "credentials",
        "is_provider", "status", "match_confidence",
    }
    assert set(vars(entry)) == {"name", "email", "extension", "teams", "location", "active", "source_row"} | merged_only
    assert all(getattr(entry, field) is None for field in merged_only)  # a plain export has none of them


# --- the merged directory (data/directory_merged.csv) -------------------------------------------


async def test_the_merged_directory_loads_the_clean_identity_fields(tmp_path):
    path = write_csv(tmp_path, [merged_row(
        "1001 Exec Max Example", display="Maximilian Example", phone=P1, ext="1001", email="1001@example.test",
        mailbox="Max.Example@Example.test", team="Service, Billing Team", location="Test Clinic - Exec",
        credentials="MD", title="Physician", provider="Y", department="Surgery", site="Goshen",
        method="phone_and_name", confidence="medium",
    )], header=MERGED_HEADER, name="directory_merged.csv")

    entry = (await loaded(path)).find_by_phone(P1).entry

    assert (entry.name, entry.display_name, entry.account_type) == ("1001 Exec Max Example", "Maximilian Example", "person")
    assert (entry.phone, entry.extension) == ("+18455550101", "1001")
    assert (entry.email, entry.mailbox_email) == ("1001@example.test", "max.example@example.test")
    assert (entry.department, entry.site, entry.location) == ("Surgery", "Goshen", "Test Clinic - Exec")
    assert (entry.job_title, entry.credentials, entry.is_provider) == ("Physician", "MD", True)
    assert (entry.status, entry.active, entry.match_confidence) == ("Active", True, "medium")
    assert entry.teams == ("Service", "Billing Team")


async def test_a_merged_record_is_found_by_phone_extension_and_either_address(tmp_path):
    service = await loaded(write_csv(tmp_path, [merged_row(
        "Ann Example", display="Ann Example", phone=P1, ext="1001", email="1001@example.test",
        mailbox="ann.example@example.test",
    )], header=MERGED_HEADER))

    found = [
        service.find_by_phone(P1), service.find_by_extension("1001"), service.find_by_email("1001@example.test"),
        service.find_by_email("ann.example@example.test"), service.find_by_identifier("Ann.Example@example.test"),
    ]

    assert all(result.matched and result.entry.display_name == "Ann Example" for result in found)


async def test_a_record_whose_two_addresses_are_the_same_is_not_ambiguous_with_itself(tmp_path):
    service = await loaded(write_csv(tmp_path, [merged_row(
        "Ann Example", display="Ann Example", phone=P1, mailbox="ann.example@example.test",
    )], header=MERGED_HEADER))

    assert service.find_by_email("ann.example@example.test").matched
    assert service.report.duplicate_emails == 0


@pytest.mark.parametrize("status, record_source, live", [
    ("Active", "phone_and_mailbox", True),
    ("Active", "phone_system_only", True),
    ("Unknown", "mailbox_only", False),  # what a mailbox with no phone-system account is given by default
    ("Active", "mailbox_only", False),  # even when the build forced it to Active: it has no phone-system account
    ("Inactive", "phone_and_mailbox", False),
])
async def test_a_record_that_exists_only_as_a_mailbox_is_never_live(tmp_path, status, record_source, live):
    path = write_csv(tmp_path, [merged_row(
        "Ann Example", display="Ann Example", phone=P1, status=status, record_source=record_source,
    )], header=[*MERGED_HEADER, "Record Source"])

    entry = (await loaded(path)).find_by_phone(P1).entry

    assert entry.active is live


async def test_the_app_loads_the_merged_directory_it_is_pointed_at(tmp_path, monkeypatch):
    path = write_csv(tmp_path, [merged_row("Ann Example", display="Ann Example", phone=P1, ext="1001")],
                     header=MERGED_HEADER, name="directory_merged.csv")
    monkeypatch.setattr(settings, "user_directory_csv_path", str(path))
    monkeypatch.setattr(app.state, "user_directory", None, raising=False)  # put back as it was when the test ends

    async with app.router.lifespan_context(app):
        entry = app.state.user_directory.find_by_phone(P1).entry

    assert (entry.display_name, entry.account_type, entry.extension) == ("Ann Example", "person", "1001")


async def test_the_app_loads_the_directory_at_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "user_directory_csv_path", str(write_csv(tmp_path, [row("Ann Example", phone=P1)])))

    async with app.router.lifespan_context(app):
        directory = app.state.user_directory
        assert directory.available and directory.find_by_phone(P1).matched
        assert get_user_directory(SimpleNamespace(app=app)) is directory


async def test_a_bad_directory_file_does_not_stop_the_app_from_starting(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "user_directory_csv_path", str(tmp_path / "missing.csv"))

    async with app.router.lifespan_context(app):
        directory = app.state.user_directory
        assert not directory.available
        assert directory.find_by_phone(P1).status is LookupStatus.UNAVAILABLE


async def test_the_dependency_hands_back_an_unavailable_service_when_startup_never_ran():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    assert get_user_directory(request).find_by_phone(P1).status is LookupStatus.UNAVAILABLE

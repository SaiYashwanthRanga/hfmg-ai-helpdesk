"""The agent's own directory sheet (HFMG.xlsx) and the employee flow built on it (CALL_FLOW.md 2.2, 2.3).

The sheet: Full Name, Extension, Email, Phone Number; several numbers in a cell ("; "), several extensions (", ").
A row is a person only with an email and a name that is safe to say; anything else (a room, a front desk) is a line.

The flow, with VOICE_EMPLOYEE_IDENTITY_FIRST:
- the caller ID finds one person: "Am I speaking with ...?", the email on file, then the problem;
- anyone else (not found, a shared number, a room's line, no caller ID, "that's not me"): the problem first, then
  "What's your last name?", looked up by sound ("Yashvant" finds Yashwanth); nobody found, the name as given, marked
  on the ticket as not found in the directory;
- "Am I speaking with ...?" answered with the problem: a callback request filed from it.

Calls run through the real /start and /turn routes against a synthetic sheet (fake names, 555 numbers), loaded by the
real service, with the language model stubbed.
"""

import openpyxl
import pytest

from app.core.config import get_settings
from app.db.models import Priority, VoiceCallState
from app.main import app
from app.services.user_directory import LookupStatus, UserDirectoryService
from app.voice import directory_identity, names, nlu, orchestrator, scripts
from tests.test_directory_identity import DirCall, _said
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

DANA, DANA_SIGNATURE, SAM, ROOM, SHARED, UNKNOWN = (
    "+18455550401", "+18455550402", "+18455550403", "+18455550410", "+18455550420", "+18455550499",
)
PROBLEM = "My Outlook keeps crashing when I open it."

SHEET = [
    ("Full Name", "Extension", "Email", "Phone Number"),
    ("Dana Sample", 3001, "dana.sample@hfmg.net", "(845) 555-0401; (845) 555-0402"),
    ("Sam Yashwanth", "3003", "sam.yashwanth@hfmg.net", "(845) 555-0403"),
    ("Robin Natekereni", "3002, 3012", "robin.natekereni@hfmg.net", None),
    ("Maria Lopez", "3004", "maria.lopez@hfmg.net", None),
    ("Kim Lopes", "3005", "kim.lopes@hfmg.net", None),
    ("Exam Room 3", "3100", None, "(845) 555-0410"),
    ("Front Desk Goshen", "3101", None, None),  # no email: a line, not a person
    ("Pat One", "3006", "pat.one@hfmg.net", "(845) 555-0420"),
    ("Pat Two", "3007", "pat.two@hfmg.net", "(845) 555-0420"),  # the same number: names nobody
    (None, None, None, None),  # a blank row
]


def write_sheet(path, rows=SHEET):
    workbook = openpyxl.Workbook()
    for row in rows:
        workbook.active.append(row)
    workbook.save(path)
    return path


@pytest.fixture
async def sheet(tmp_path, monkeypatch):
    service = UserDirectoryService()
    await service.load(write_sheet(tmp_path / "HFMG.xlsx"))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)
    return service


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture, sheet):  # noqa: F811
    monkeypatch.setattr(settings, "voice_ask_caller_type", True)
    monkeypatch.setattr(settings, "voice_employee_identity_first", True)

    def make(call_id, from_number):
        return DirCall(client, monkeypatch, call_id, from_number)

    return make


def _name_heard(call, name):
    call._say(interpret_name=nlu.TurnResult(value=name, confidence="high", unable_to_determine=name is None))


def _problem_heard(call):
    call._say(interpret_description=nlu.TurnResult(
        value=PROBLEM, confidence="high", unable_to_determine=False, category="Microsoft 365", priority=Priority.MEDIUM,
        extras={"short_issue": "your Outlook", "started": "today", "work_blocked": False},
    ))


# --- the sheet --------------------------------------------------------------------------------------------------


async def test_the_sheet_loads_every_row_and_marks_people_and_lines(sheet):
    report = sheet.report
    assert report.loaded_records == 9 and not report.rejected_rows  # the blank row is skipped
    kinds = {e.name: e.account_type for e in sheet._snapshot.records}
    assert kinds["Dana Sample"] == "person"
    assert kinds["Exam Room 3"] == kinds["Front Desk Goshen"] == "shared_line"


async def test_every_number_and_extension_in_a_cell_finds_its_person(sheet):
    for number in (DANA, DANA_SIGNATURE):
        assert directory_identity.employee_name(sheet.find_by_phone(number).entry) == "Dana Sample"
    for extension in ("3002", "3012"):
        assert sheet.find_by_extension(extension).entry.name == "Robin Natekereni"
    assert sheet.find_by_extension("3001").entry.name == "Dana Sample"  # stored by Excel as a number


async def test_a_room_is_found_but_never_named_and_a_shared_number_names_nobody(sheet):
    room = sheet.find_by_phone(ROOM)
    assert room.matched and directory_identity.employee_name(room.entry) is None
    assert sheet.find_by_phone(SHARED).status is LookupStatus.AMBIGUOUS


async def test_a_sheet_that_cannot_be_read_leaves_the_directory_unavailable(tmp_path):
    bad = tmp_path / "HFMG.xlsx"
    bad.write_bytes(b"not a workbook")
    service = UserDirectoryService()

    await service.load(bad)

    assert not service.available and service.unavailable_reason == "unreadable"


# --- the name search, by sound ------------------------------------------------------------------------------------


@pytest.mark.parametrize("heard, listed", [
    ("Yashvant", "Yashwanth"), ("Riley", "Reilly"), ("Natakarani", "Natekereni"), ("Smyth", "Smith"), ("Lopes", "Lopez"),
])
async def test_names_that_sound_alike_match(heard, listed):
    assert names.name_similarity(heard, listed) >= names.SIMILAR_ENOUGH


@pytest.mark.parametrize("heard, listed", [("Li", "Lee"), ("Mark", "Mike"), ("Smith", "Jones"), ("Dana", "Lana")])
async def test_names_that_do_not_sound_alike_do_not(heard, listed):
    assert names.name_similarity(heard, listed) < names.SIMILAR_ENOUGH


async def test_the_search_finds_a_misheard_name_and_never_mixes_an_exact_match_with_near_ones(sheet):
    assert [e.name for e in sheet.find_by_last_name("Yashvant")] == ["Sam Yashwanth"]
    assert [e.name for e in sheet.find_by_last_name("Natakarani")] == ["Robin Natekereni"]
    assert [e.name for e in sheet.find_by_last_name("Lopez")] == ["Maria Lopez"]  # exact: Kim Lopes is not offered
    assert {e.name for e in sheet.find_by_last_name("Lopas")} == {"Maria Lopez", "Kim Lopes"}  # a tie is a question


# --- A: the caller ID finds one person -----------------------------------------------------------------------------


async def test_a_known_caller_confirms_name_and_email_then_gives_the_problem(make_call, client, db_session):
    call = make_call("hfmg-known", DANA)
    await call.start()

    out = await call.turn("I'm an employee.")
    assert scripts.IDENTITY_CONFIRM.format(name="Dana Sample") in _said(out)

    out = await call.turn("Yes.")
    assert scripts.EMAIL_DIRECTORY_CONFIRM.format(email=scripts.spoken_email("dana.sample@hfmg.net")) in _said(out)

    out = await call.turn("Yes.")
    assert _said(out) == scripts.EMPLOYEE_PROBLEM_ASK.format(first_name="Dana")

    await call.report(PROBLEM)
    out = await call.turn("Billing.")  # the department

    assert out["ticket_id"] is not None
    ticket = await _ticket(client, out["ticket_id"])
    assert ticket["caller_name"] == "Dana Sample" and ticket["email"] == "dana.sample@hfmg.net"
    assert "Identity: directory entry confirmed by the caller; not authenticated" in ticket["description"]


async def test_a_number_from_the_same_cell_finds_the_same_person(make_call):
    call = make_call("hfmg-second-number", DANA_SIGNATURE)
    await call.start()

    out = await call.turn("I'm an employee.")

    assert scripts.IDENTITY_CONFIRM.format(name="Dana Sample") in _said(out)


async def test_a_known_caller_who_declines_the_email_spells_it_then_gives_the_problem(make_call, db_session):
    call = make_call("hfmg-own-email", DANA)
    await call.start()
    await call.turn("I'm an employee.")
    await call.turn("Yes.")
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=False, unable_to_determine=False))

    await call.turn("No.")

    assert (await _session(db_session, call.call_id)).state == VoiceCallState.COLLECT_EMAIL


async def test_yes_with_the_problem_keeps_both(make_call, db_session):
    call = make_call("hfmg-yes-problem", DANA)
    await call.start()
    await call.turn("I'm an employee.")
    _problem_heard(call)

    out = await call.turn(f"Yes, and {PROBLEM}")

    session = await _session(db_session, call.call_id)
    assert session.collected["caller_name"] == "Dana Sample"
    assert session.collected["description"] == PROBLEM
    assert out["ticket_id"] is None and session.state != VoiceCallState.COLLECT_DESCRIPTION


async def test_the_problem_instead_of_yes_or_no_files_a_callback_request(make_call, client, db_session, sent_emails):  # noqa: F811
    call = make_call("hfmg-lazy", DANA)
    await call.start()
    await call.turn("I'm an employee.")
    _problem_heard(call)

    out = await call.turn(PROBLEM)

    assert out["ticket_id"] is not None and out["expect_reply"] is False
    assert scripts.NAMELESS_CALLBACK.format(phone=scripts.spoken_phone_number(DANA)) in _said(out)
    ticket = await _ticket(client, out["ticket_id"])
    assert ticket["caller_name"] == "Unknown caller (voice)"
    assert ticket["description"].startswith(orchestrator.NAMELESS_CALLBACK_NOTE) and PROBLEM in ticket["description"]
    assert (await _session(db_session, call.call_id)).state == VoiceCallState.COMPLETED
    assert len(sent_emails) == 1  # the helpdesk is told, as for every ticket


# --- B: anyone else gives the problem first, then the last name ---------------------------------------------------


async def _to_the_last_name_question(call):
    await call.start()
    out = await call.turn("I'm an employee.")
    assert scripts.EMPLOYEE_PROBLEM_ASK_PLAIN in _said(out)
    out = await call.report(PROBLEM)
    assert scripts.LAST_NAME_ASK in _said(out)


@pytest.mark.parametrize("caller_id", [UNKNOWN, ROOM, SHARED, ""], ids=["not-found", "room", "shared", "no-caller-id"])
async def test_a_caller_the_number_does_not_settle_gives_the_problem_then_the_last_name(make_call, caller_id):
    await _to_the_last_name_question(make_call(f"hfmg-b-{caller_id or 'none'}", caller_id))


async def test_that_is_not_me_gives_the_problem_then_the_last_name(make_call):
    call = make_call("hfmg-not-me", DANA)
    await call.start()
    await call.turn("I'm an employee.")

    out = await call.turn("No, this isn't me.")
    assert _said(out) == f"{scripts.IDENTITY_DECLINED} {scripts.EMPLOYEE_PROBLEM_ASK_PLAIN}"

    out = await call.report(PROBLEM)
    assert scripts.LAST_NAME_ASK in _said(out)


async def test_a_misheard_last_name_finds_the_person_and_their_email_reaches_the_ticket(make_call, client):
    call = make_call("hfmg-b-found", UNKNOWN)
    await _to_the_last_name_question(call)

    _name_heard(call, "Yashvant")
    out = await call.turn("Yashvant")
    assert scripts.IDENTITY_CONFIRM_BY_LAST_NAME.format(name="Sam Yashwanth") in _said(out)

    await call.turn("Yes.")
    out = await call.turn("Billing.")  # the department
    assert scripts.EMAIL_DIRECTORY_CONFIRM.format(email=scripts.spoken_email("sam.yashwanth@hfmg.net")) in _said(out)

    call._say(interpret_yes_no=nlu.TurnResult(yes_no=True, unable_to_determine=False))
    out = await call.turn("Yes.")
    ticket = await _ticket(client, out["ticket_id"])
    assert ticket["caller_name"] == "Sam Yashwanth" and ticket["email"] == "sam.yashwanth@hfmg.net"


async def test_two_close_names_are_asked_about_never_picked(make_call):
    call = make_call("hfmg-b-tie", UNKNOWN)
    await _to_the_last_name_question(call)

    _name_heard(call, "Lopas")
    out = await call.turn("Lopas")
    assert scripts.LAST_NAME_WHICH in _said(out)

    _name_heard(call, "Maria")
    out = await call.turn("Maria")
    assert scripts.IDENTITY_CONFIRM_BY_LAST_NAME.format(name="Maria Lopez") in _said(out)


async def test_a_name_not_in_the_sheet_is_taken_as_given_and_marked_on_the_ticket(make_call, client):
    call = make_call("hfmg-b-nobody", UNKNOWN)
    await _to_the_last_name_question(call)

    _name_heard(call, "Quorvant")
    out = await call.turn("Quorvant")
    assert any(ask in _said(out) for ask in scripts.NAME_ASK_OPTIONS)

    _name_heard(call, "Alex Quorvant")
    await call.turn("Alex Quorvant, billing.")
    out = await call.turn("skip")  # the email question

    ticket = await _ticket(client, out["ticket_id"])
    assert ticket["caller_name"] == "Alex Quorvant"
    assert "Identity: not found in the directory (name as given by the caller)" in ticket["description"]


# --- the setting -----------------------------------------------------------------------------------------------


async def test_with_the_setting_off_the_problem_comes_first_and_the_name_question_is_the_old_one(make_call, monkeypatch):
    monkeypatch.setattr(settings, "voice_employee_identity_first", False)
    call = make_call("hfmg-off", DANA)
    await call.start()

    out = await call.turn("I'm an employee.")

    assert _said(out) == scripts.EMPLOYEE_ASK


# --- agreement as a sound ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "said, meaning",
    [
        ("Uh-huh.", True), ("uh huh", True), ("Mm-hmm.", True), ("Mhm.", True), ("mm hmm yes", True),
        ("Uh-uh.", False), ("Nuh-uh.", False),
        ("Okay.", None),  # read with the question by the model: "okay" to "anything else?" is not a yes
        ("Uh, uh.", None),  # a hesitation, not a no
    ],
)
async def test_agreement_and_refusal_sounds(said, meaning):
    assert nlu.quick_yes_no(said) is meaning
    assert nlu.quick_yes_no(said, strict=True) is meaning

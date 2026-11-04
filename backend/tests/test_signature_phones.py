"""The numbers people wrote in their email signatures find them by caller ID too (merged directory, "Signature Phones").

Signature numbers are mostly office and fax lines, so they never compete with the phone system's own numbers: they are
searched only when no record has the caller ID as its "Phone Number", a number that is any record's own belongs to that
record, and one written by several people names nobody. A match is still only a proposal the caller confirms.
"""

import pytest

from app.core.config import get_settings
from app.main import app
from app.services.user_directory import LookupStatus, UserDirectoryService
from app.voice import scripts
from tests.test_directory_identity import DirCall, _said
from tests.test_multi_issue_calls import _session
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import MERGED_HEADER, merged_row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

OWN = "(845) 555-0301"           # Dana's phone-system number
SIGNATURE_ONLY = "+18455550302"  # only in Robin's signature
SECOND_SIGNATURE = "+18455550303"
OFFICE_LINE = "+18455550304"     # in two people's signatures
WRITTEN_BY_OTHERS = "+18455550301"  # Dana's own number, also written in Robin's signature


def _rows():
    return [
        merged_row("Dana Sample", display="Dana Sample", phone=OWN, ext="3001", mailbox="dana.sample@example.org"),
        merged_row("Robin Example", display="Robin Example", ext="3002", mailbox="robin.example@example.org",
                   signature_phones="845.555.0302; (845) 555-0303; 845-555-0304; 845-555-0301"),
        merged_row("Casey Example", display="Casey Example", ext="3003", signature_phones="8455550304"),
    ]


@pytest.fixture
async def directory(tmp_path, monkeypatch):
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, _rows(), header=MERGED_HEADER, name="directory_merged.csv"))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)
    return service


async def test_a_number_only_in_a_signature_finds_its_person(directory):
    for number in (SIGNATURE_ONLY, SECOND_SIGNATURE):  # every number in the cell, whatever its punctuation
        result = directory.find_by_phone(number)
        assert result.matched and result.entry.display_name == "Robin Example"
        assert result.matched_on == "signature_phone"


async def test_the_phone_systems_own_number_wins_over_a_signature(directory):
    result = directory.find_by_phone(WRITTEN_BY_OTHERS)

    assert result.matched and result.entry.display_name == "Dana Sample"
    assert result.matched_on is None


async def test_a_signature_number_several_people_wrote_names_nobody(directory):
    result = directory.find_by_phone(OFFICE_LINE)

    assert result.status is LookupStatus.AMBIGUOUS and result.entry is None


async def test_a_number_in_no_column_is_not_found(directory):
    assert directory.find_by_phone("+18455550399").status is LookupStatus.NOT_FOUND


async def test_an_employee_calling_from_a_signature_number_is_asked_about_by_name(
    client, monkeypatch, _call_fixture, directory, db_session  # noqa: F811
):
    monkeypatch.setattr(settings, "voice_ask_caller_type", True)
    monkeypatch.setattr(settings, "voice_employee_identity_first", True)
    call = DirCall(client, monkeypatch, "sig-call", SIGNATURE_ONLY)
    await call.start()

    out = await call.turn("I'm an employee.")

    assert scripts.IDENTITY_CONFIRM.format(name="Robin Example") in _said(out)
    session = await _session(db_session, call.call_id)
    assert session.collected["directory"]["matched_on"] == "signature_phone"

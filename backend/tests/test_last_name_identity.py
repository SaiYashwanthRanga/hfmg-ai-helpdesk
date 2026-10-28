"""Employee flow: when the caller ID finds nobody, the caller is identified by last name.

UserDirectoryService.find_by_last_name returns live, real people only. In the employee flow a caller whose caller
ID is not found, ambiguous or could not be looked up is asked "What's your last name?" before the name question. One
match is proposed through the confirm-identity step ("Are you Ranga Nair?"); several are told apart by first name or
extension; a no, no match, or anything still unclear is the usual name question. Nothing about the person asked about
is said or kept until the caller says yes. Patient and provider calls never do any of this (their tests, with the
lookup counted, are in test_employee_directory_flow.py).

The directory is synthetic (example.test, 555 numbers) in the merged layout, loaded by the real service; calls run
through the real /start and /turn routes with the language model stubbed.
"""

import json

import pytest

from app.core.config import get_settings
from app.db.models import VoiceCallState
from app.main import app
from app.services.user_directory import UserDirectoryService
from app.voice import names, nlu, scripts
from tests.test_directory_identity import DirCall, _said
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import MERGED_HEADER, merged_row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

UNKNOWN, AMBIGUOUS_ID, NAIR_ID = "+18455550599", "+18455550504", "+18455550501"


def _rows():
    return [
        # Unique last name. The phone system's email is a placeholder; the real address is the mailbox's.
        merged_row("Ranga Nair", display="Ranga Nair", phone="(845) 555-0501", ext="5101", email="5101@example.test",
                   mailbox="ranga.nair@example.org", department="Billing"),
        # Two people share a last name; the first names differ by more than a recognizer's slip.
        merged_row("Ranga Rao", display="Ranga Rao", phone="(845) 555-0502", ext="5102", mailbox="ranga.rao@example.org"),
        merged_row("Rani Rao", display="Rani Rao", phone="(845) 555-0503", ext="5103", mailbox="rani.rao@example.org"),
        merged_row("Dana Reilly", display="Dana Reilly", phone="(845) 555-0510", ext="5110", mailbox="dana.reilly@example.org"),
        merged_row("Ivy Hill", display="Ivy Hill", phone="(845) 555-0520", ext="5120", mailbox="ivy.hill@example.org"),
        merged_row("Max Hall", display="Max Hall", phone="(845) 555-0521", ext="5121", mailbox="max.hall@example.org"),
        merged_row("Wei Li", display="Wei Li", phone="(845) 555-0511", ext="5111", mailbox="wei.li@example.org"),
        merged_row("Tom Baker Jr", display="Tom Baker Jr.", phone="(845) 555-0512", ext="5112", mailbox="tom.baker@example.org"),
        merged_row("Mary OBrien", display="Mary O'Brien", phone="(845) 555-0513", ext="5113", mailbox="mary.obrien@example.org"),
        # Records that carry a name but are not a live person. They share a last name with real people (Nair, Rao)
        # or are the only holder of theirs (Deskline, Servline, Testline): none is ever offered.
        merged_row("Front Desk Nair", kind="shared_line", display="Front Nair", phone="(845) 555-0590", ext="5190"),
        merged_row("Helpdesk Line", kind="shared_or_service", display="IT Rao", ext="5191", mailbox="helpdesk@example.test"),
        merged_row("Test Phone", kind="test", display="Test Rao", ext="5192"),
        merged_row("Quinn Rao", display="Quinn Rao", status="Inactive", ext="5193"),
        merged_row("Mailbox Rao", display="Mailbox Rao", status="Unknown", email="mailbox.rao@example.test",
                   mailbox="mailbox.rao@example.test"),
        merged_row("Front Desk Two", kind="shared_line", display="Front Deskline", ext="5194"),
        merged_row("Billing Line", kind="shared_or_service", display="Billing Servline", ext="5195"),
        merged_row("Test Phone Two", kind="test", display="Test Testline", ext="5196"),
        merged_row("Call Park 1", kind="call_park", ext="5197"),
        # Two people on one number: a caller ID that is ambiguous.
        merged_row("Pat One", display="Pat One", phone="(845) 555-0504", ext="5104"),
        merged_row("Pat Two", display="Pat Two", phone="(845) 555-0504", ext="5105"),
    ]


async def _service(tmp_path, rows=None) -> UserDirectoryService:
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, _rows() if rows is None else rows, header=MERGED_HEADER, name="directory_merged.csv"))
    return service


@pytest.fixture
async def directory(tmp_path, monkeypatch):
    service = await _service(tmp_path)
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)
    return service


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture, directory):  # noqa: F811
    def make(call_id, from_number=UNKNOWN):
        return DirCall(client, monkeypatch, call_id, from_number)

    return make


def _agent_text(session) -> str:
    return " ".join(t["text"] for t in session.turns if t["role"] == "agent")


# --- 1. UserDirectoryService.find_by_last_name -------------------------------------------------------------------------


def _found(service, last_name) -> list[str]:
    return [e.display_name for e in service.find_by_last_name(last_name)]


@pytest.mark.parametrize("said", ["Nair", "nair", "NAIR", " Nair "])
async def test_a_unique_last_name_finds_one_person_whatever_its_case(directory, said):
    assert _found(directory, said) == ["Ranga Nair"]  # the shared line "Front Nair" is not a person


async def test_several_people_with_a_last_name_are_all_returned_in_file_order(directory):
    # "IT Rao" (a service), "Test Rao" (a test), the deactivated "Quinn Rao" and the mailbox-only "Mailbox Rao" are not.
    assert _found(directory, "Rao") == ["Ranga Rao", "Rani Rao"]


@pytest.mark.parametrize("last_name", ["Deskline", "Servline", "Testline"])
async def test_a_shared_line_a_service_or_a_test_is_never_returned(directory, last_name):
    assert directory.find_by_last_name(last_name) == ()


@pytest.mark.parametrize("last_name", ["Zzyzx", "", "   ", None, "R", "1234"])
async def test_no_match_a_blank_or_a_junk_last_name_finds_nobody(directory, last_name):
    assert directory.find_by_last_name(last_name) == ()


@pytest.mark.parametrize("said, expected", [
    ("Reily", ["Dana Reilly"]),  # one letter missing: a recognizer's slip, tolerated as it is for names elsewhere
    ("Lee", []),  # a name shorter than four letters must be exact
    ("Baker", ["Tom Baker Jr."]),  # a suffix is not the family name
    ("Jr", []),
    ("OBrien", ["Mary O'Brien"]),
    ("O'Brien", ["Mary O'Brien"]),
    ("Hill", ["Ivy Hill"]),  # "Hall" is one letter away, but an exact match is never mixed with a near miss
    ("Hall", ["Max Hall"]),
    ("Hell", ["Ivy Hill", "Max Hall"]),  # no exact match: both near misses
])
async def test_matching_is_as_tolerant_as_names_are_elsewhere(directory, said, expected):
    assert _found(directory, said) == expected


async def test_any_word_of_the_name_finds_the_person_whatever_order_the_directory_writes_it(directory):
    assert _found(directory, "Ranga") == ["Ranga Nair", "Ranga Rao"]  # a first name finds everyone who has it
    assert _found(directory, "Dana") == ["Dana Reilly"]
    assert directory.find_by_last_name("Ranga Nair") == ()  # a whole name is not one name part


async def test_a_name_in_either_order_and_a_middle_name_find_the_same_person(tmp_path):
    service = await _service(tmp_path, [
        merged_row("3000 Sai Yashwanth", display="Ranga Sai Yashwanth", phone="(845) 555-0601", ext="6001"),
        merged_row("Yashwanth Ranga", display="Anna Smith-Jones", phone="(845) 555-0602", ext="6002"),
    ])

    for said in ("Ranga", "Sai", "Yashwanth"):
        assert _found(service, said) == ["Ranga Sai Yashwanth"]
    assert _found(service, "Smith") == _found(service, "Jones") == _found(service, "Smith-Jones") == ["Anna Smith-Jones"]


@pytest.mark.parametrize("caller, listed, same", [
    ("Yashwanth Ranga", ["Ranga Sai Yashwanth"], True),  # either order, the middle name left out
    ("Ranga Sai Yashwanth", ["Ranga Sai Yashwanth"], True),
    ("Ranga Yashwanth", [None, "Ranga Sai Yashwanth"], True),
    ("Yashwanth Nair", ["Ranga Sai Yashwanth"], False),
    ("Maria Ranga", ["Ranga Sai Yashwanth"], False),  # a word that is not in the listed name
    ("", ["Ranga Sai Yashwanth"], False),
    ("Ranga Sai", [], False),
])
async def test_the_extension_note_compares_a_name_by_any_word(caller, listed, same):
    from app.voice import directory_identity

    assert directory_identity.is_same_name(caller, listed) is same


async def test_an_unavailable_directory_finds_nobody():
    assert UserDirectoryService().find_by_last_name("Nair") == ()


async def test_a_plain_phone_system_export_has_no_clean_names_to_find(tmp_path):
    from tests.test_user_directory import row

    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, [row("Maria Example", phone="(845) 555-0201", ext="2001")]))

    assert service.available and service.find_by_last_name("Example") == ()


@pytest.mark.parametrize("heard, listed, same", [
    ("riley", "riley", True), ("reily", "reilly", True), ("lee", "li", False), ("lee", "lea", False),
    ("hill", "hall", True), ("smith", "jones", False),
])
async def test_one_name_part_against_another(heard, listed, same):
    assert names.same_name_part(heard, listed) is same


# --- 2. the question is asked when the caller ID finds nobody, and only then ----------------------------------------


@pytest.mark.parametrize("from_number, status", [(UNKNOWN, "not_found"), (AMBIGUOUS_ID, "ambiguous")])
async def test_a_caller_the_caller_id_did_not_find_is_asked_for_a_last_name_first(make_call, db_session, from_number, status):
    call = make_call(f"ln-ask-{status}", from_number)
    await call.start()

    asked = await call.report()

    assert scripts.LAST_NAME_ASK in _said(asked) and "directory entry" not in _said(asked)
    session = await _session(db_session, call.call_id)
    assert session.collected["directory"]["status"] == status
    assert session.state == VoiceCallState.COLLECT_NAME and not session.collected.get("caller_name")


async def test_a_lookup_that_failed_still_asks_for_a_last_name(make_call, db_session, directory, monkeypatch):
    def broken(_key):
        raise RuntimeError("boom")

    monkeypatch.setattr(directory, "find_by_phone", broken)
    call = make_call("ln-ask-broken", NAIR_ID)
    await call.start()

    asked = await call.report()

    assert scripts.LAST_NAME_ASK in _said(asked)
    assert (await _session(db_session, "ln-ask-broken")).collected["directory"]["status"] == "unavailable"


async def test_a_caller_the_caller_id_found_is_proposed_that_person_and_not_asked_for_a_last_name(make_call, db_session, directory):
    calls = []
    real = directory.find_by_last_name
    directory.find_by_last_name = lambda key: calls.append(key) or real(key)
    call = make_call("ln-found", NAIR_ID)
    await call.start()

    asked = await call.report()

    assert "I found a directory entry for Ranga Nair. Am I speaking with Ranga Nair?" in _said(asked)
    assert scripts.LAST_NAME_ASK not in _said(asked) and calls == []


async def test_a_directory_that_cannot_be_searched_costs_the_caller_no_extra_question(make_call, db_session, monkeypatch):
    monkeypatch.setattr(app.state, "user_directory", UserDirectoryService(), raising=False)  # never loaded
    call = make_call("ln-unavailable")
    await call.start()

    asked = await call.report()

    session = await _session(db_session, "ln-unavailable")
    assert session.collected["directory"]["status"] == "unavailable" and "by_last_name" not in session.collected["directory"]
    assert scripts.LAST_NAME_ASK not in _said(asked) and session.state == VoiceCallState.COLLECT_NAME


# --- 3. one match: "Are you <name>?" through the confirm-identity step -------------------------------------------------


async def test_a_unique_last_name_is_confirmed_before_anything_about_the_person_is_said_or_kept(
    make_call, client, db_session, sent_emails
):
    call = make_call("ln-unique")
    await call.start()
    await call.report()

    proposal = await call.last_name("Nair")

    assert _said(proposal) == "Are you Ranga Nair?"
    session = await _session(db_session, "ln-unique")
    assert session.state == VoiceCallState.CONFIRM_IDENTITY
    assert not session.collected.get("caller_name") and not session.collected.get("identity_confirmed")
    kept = session.collected["directory"]
    assert kept["proposed_name"] == "Ranga Nair" and "entry" not in kept
    held = json.dumps(session.collected) + _agent_text(session)
    for detail in ("5101", "ranga.nair", "555-0501", "+18455550501", "Billing"):  # nothing but the name being asked
        assert detail not in held

    await call.turn("yes")

    session = await _session(db_session, "ln-unique")
    c = session.collected
    assert (c["caller_name"], c["identity_confirmed"], c["identity_assurance"]) == (
        "Ranga Nair", True, "caller_confirmed_directory_match"
    )
    who = c["directory_identity"]
    assert (who["extension"], who["email"], who["phone"]) == ("5101", "ranga.nair@example.org", "+18455550501")
    assert c["phone_number"] == UNKNOWN  # the callback number stays the one they are calling from
    assert "pending_row" not in c["directory"] and "last_name_heard" not in c["directory"]

    await call.department("Ranga Nair")
    filed = await call.skip_email()
    intake = (await _ticket(client, filed["ticket_id"]))["description"].split("--- Call transcript ---")[0]
    assert "Extension: 5101" in intake and "directory entry confirmed by the caller; not authenticated" in intake


async def test_a_caller_who_says_no_gets_the_usual_name_question_and_nothing_of_the_other_person_is_kept(
    make_call, client, db_session, sent_emails
):
    call = make_call("ln-no")
    await call.start()
    await call.report()
    await call.last_name("Nair")

    declined = await call.turn("no")

    assert scripts.IDENTITY_DECLINED in _said(declined)
    assert any(option in _said(declined) for option in scripts.NAME_ASK_OPTIONS)
    session = await _session(db_session, "ln-no")
    assert session.state == VoiceCallState.COLLECT_NAME
    assert session.collected["directory"] == {"status": "not_found", "rejected": True}
    assert not session.collected.get("caller_name") and not session.collected.get("identity_confirmed")
    assert "5101" not in json.dumps(session.collected)

    await call.name_and_department("Pat Real")  # not asked for a last name again
    filed = await call.skip_email()

    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["caller_name"] == "Pat Real"
    intake = ticket["description"].split("--- Call transcript ---")[0]
    for leaked in ("Nair", "5101", "Identity:"):
        assert leaked not in intake
    assert (await _session(db_session, "ln-no")).turns and _agent_text(session).count(scripts.LAST_NAME_ASK) == 1


async def test_a_caller_who_does_not_answer_the_confirmation_is_not_taken_for_that_person(make_call, db_session):
    call = make_call("ln-unclear")
    await call.start()
    await call.report()
    await call.last_name("Nair")

    await call.turn("hmm")  # neither yes nor no: asked once more
    gave_up = await call.turn("what?")

    session = await _session(db_session, "ln-unclear")
    assert not session.collected.get("caller_name") and not session.collected.get("identity_confirmed")
    assert any(option in _said(gave_up) for option in scripts.NAME_ASK_OPTIONS)


async def test_a_yes_for_someone_no_longer_in_the_directory_confirms_nobody(
    make_call, db_session, tmp_path, monkeypatch
):
    call = make_call("ln-reloaded")
    await call.start()
    await call.report()
    await call.last_name("Nair")
    # The directory is reloaded mid-call from a file without that person.
    monkeypatch.setattr(app.state, "user_directory", await _service(tmp_path, rows=[
        merged_row("Someone Else", display="Someone Else", phone="(845) 555-0700", ext="5700")
    ]), raising=False)

    asked = await call.turn("yes")

    session = await _session(db_session, "ln-reloaded")
    assert not session.collected.get("caller_name") and not session.collected.get("identity_confirmed")
    assert any(option in _said(asked) for option in scripts.NAME_ASK_OPTIONS)


@pytest.mark.parametrize("said, heard, expected", [
    ("Reily", "Reily", "Dana Reilly"),  # the recognizer dropped a letter
    ("Baker", "Baker", "Tom Baker Jr."),
    ("O'Brien", "OBrien", "Mary O'Brien"),
])
async def test_a_last_name_the_recognizer_got_slightly_wrong_still_finds_the_person(make_call, said, heard, expected):
    call = make_call(f"ln-slip-{said[:3]}")
    await call.start()
    await call.report()

    proposal = await call.last_name(said, heard=heard)

    assert _said(proposal) == f"Are you {expected}?"


async def test_a_first_name_given_with_the_last_narrows_the_list_and_a_wrong_one_matches_nobody(make_call, db_session):
    both = make_call("ln-full")
    await both.start()
    await both.report()
    assert _said(await both.last_name("Ranga Rao")) == "Are you Ranga Rao?"  # two Raos, one Ranga

    wrong = make_call("ln-wrong-first")
    await wrong.start()
    await wrong.report()
    asked = await wrong.last_name("Pat Nair")  # the only Nair is a Ranga: never offered to someone who said Pat

    assert any(option in _said(asked) for option in scripts.NAME_ASK_OPTIONS)
    assert "Ranga" not in _said(asked)


# --- 4. several people with that last name ----------------------------------------------------------------------------


@pytest.mark.parametrize("answer, expected, extension", [
    ("Ranga", "Ranga Rao", "5102"),
    ("Rani", "Rani Rao", "5103"),
    ("It's Rani.", "Rani Rao", "5103"),
    ("my extension is 5103", "Rani Rao", "5103"),
    ("5102", "Ranga Rao", "5102"),
    ("five one zero two", "Ranga Rao", "5102"),
])
async def test_several_people_with_that_last_name_are_told_apart_by_first_name_or_extension(
    make_call, db_session, answer, expected, extension
):
    call = make_call(f"ln-which-{answer[:6]}")
    await call.start()
    await call.report()

    which = await call.last_name("Rao")

    assert _said(which) == scripts.LAST_NAME_WHICH
    session = await _session(db_session, call.call_id)
    assert session.state == VoiceCallState.COLLECT_NAME and not session.collected.get("caller_name")
    for detail in ("Ranga", "Rani", "5102", "5103", "ranga.rao", "rani.rao"):  # no candidate is named or described
        assert detail not in json.dumps(session.collected) + _agent_text(session)

    call._say(interpret_name=nlu.TurnResult(value=answer.split()[-1].strip("."), confidence="high", unable_to_determine=False))
    proposal = await call.turn(answer)

    assert _said(proposal) == f"Are you {expected}?"
    await call.turn("yes")
    session = await _session(db_session, call.call_id)
    assert session.collected["caller_name"] == expected
    assert session.collected["directory_identity"]["extension"] == extension


@pytest.mark.parametrize("answer", ["Pat", "5999", "5191", "5193", "Ranga or Rani", ""])
async def test_if_the_list_cannot_be_narrowed_to_one_the_usual_name_question_follows(make_call, db_session, answer):
    # 5191 is a service line and 5193 a former employee: neither is a person to pick.
    call = make_call(f"ln-unsure-{answer or 'silence'}")
    await call.start()
    await call.report()
    await call.last_name("Rao")
    call._say(interpret_name=nlu.TurnResult(value=answer or None, confidence="high", unable_to_determine=not answer))

    asked = await call.turn(answer)

    session = await _session(db_session, call.call_id)
    assert any(option in _said(asked) for option in scripts.NAME_ASK_OPTIONS)
    assert scripts.LAST_NAME_WHICH not in _said(asked)  # asked once
    assert session.state == VoiceCallState.COLLECT_NAME and not session.collected.get("last_name_step")
    assert not session.collected.get("caller_name") and not session.collected.get("identity_confirmed")
    assert session.misunderstanding_count == 0
    assert "Ranga" not in _said(asked) and "Rani" not in _said(asked)


# --- 5. no match ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("last_name", ["Zzyzx", "Deskline", "Servline", "Testline"])
async def test_no_match_or_only_a_non_person_is_the_usual_name_question_and_the_last_name_is_asked_once(
    make_call, client, db_session, sent_emails, last_name
):
    call = make_call(f"ln-none-{last_name}")
    await call.start()
    await call.report()

    asked = await call.last_name(last_name)

    assert any(option in _said(asked) for option in scripts.NAME_ASK_OPTIONS)
    session = await _session(db_session, call.call_id)
    assert session.state == VoiceCallState.COLLECT_NAME and not session.collected.get("last_name_step")
    assert not session.collected.get("caller_name") and not session.collected.get("identity_confirmed")
    assert session.collected["directory"].get("proposed_name") is None

    await call.name_and_department("Pat Real")
    filed = await call.skip_email()
    assert (await _ticket(client, filed["ticket_id"]))["caller_name"] == "Pat Real"
    assert _agent_text(await _session(db_session, call.call_id)).count(scripts.LAST_NAME_ASK) == 1


async def test_silence_at_the_last_name_question_is_not_a_misunderstanding(make_call, db_session):
    call = make_call("ln-silence")
    await call.start()
    await call.report()

    asked = await call.turn("")

    session = await _session(db_session, "ln-silence")
    assert any(option in _said(asked) for option in scripts.NAME_ASK_OPTIONS)
    assert session.misunderstanding_count == 0 and not session.collected.get("last_name_step")


async def test_asking_for_a_person_at_the_last_name_question_escalates(make_call, db_session):
    call = make_call("ln-person")
    await call.start()
    await call.report()
    call._say(interpret_name=nlu.TurnResult(escalation_requested=True, unable_to_determine=False))

    await call.turn("Can I just talk to somebody?")

    assert (await _session(db_session, "ln-person")).state == VoiceCallState.ESCALATED


# --- 6. spelled-out names -----------------------------------------------------------------------------------------------


@pytest.fixture
def no_model(monkeypatch):
    """The model is never asked to hear spelled letters: a turn that does so fails."""
    async def forbidden(*args, **kwargs):
        raise AssertionError("spelled letters must be decoded, not sent to the model")

    monkeypatch.setattr(nlu, "interpret_name", forbidden)


@pytest.mark.parametrize("spelled", ["N-A-I-R", "N A I R", "n a i r", "It's N, A, I, R", "N as in November, A, I, R"])
async def test_a_last_name_spelled_out_is_decoded_without_the_model(make_call, no_model, spelled):
    call = make_call(f"ln-spell-{len(spelled)}")
    await call.start()
    await call.report()

    proposal = await call.turn(spelled)

    assert _said(proposal) == "Are you Ranga Nair?"


@pytest.mark.parametrize("spelled, expected", [
    ("R-A-N-G-A", "Ranga Rao"), ("R A N G A", "Ranga Rao"), ("R-A-N-I", "Rani Rao"), ("Rani, R A N I", "Rani Rao"),
])
async def test_a_first_name_spelled_out_picks_one_of_several(make_call, no_model, spelled, expected):
    call = make_call(f"ln-spell-first-{len(spelled)}-{spelled[-1]}")
    await call.start()
    await call.report()
    which = await call.turn("R-A-O")  # three letters, spelled: decoded without the model too

    assert _said(which) == scripts.LAST_NAME_WHICH

    proposal = await call.turn(spelled)

    assert _said(proposal) == f"Are you {expected}?"

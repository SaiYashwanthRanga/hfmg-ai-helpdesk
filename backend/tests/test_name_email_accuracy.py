"""Names and email addresses: what is heard, what is read back, and how a wrong one is fixed.

Synthetic callers and addresses only (example.org, 555 numbers). Flow tests drive the real
/start and /turn routes with the real email parser; only the language model is stubbed, and only
where a test needs it.
"""

import asyncio

import pytest

from app.core.config import get_settings
from app.main import app
from app.services.user_directory import UserDirectoryService
from app.speech import context as speech_context
from app.voice import directory_identity, names, nlu, scripts
from tests.test_directory_identity import AUTH, SIP, DirCall, _said
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
DANA, HAL, PIA, TOM, NED, UNA, ZED, STRANGER = (f"+1845555030{n}" for n in (1, 2, 3, 4, 5, 6, 7, 9))
STRANGER_TWO = "+18455550398"


# --- reading a spoken address ----------------------------------------------------------------------


@pytest.mark.parametrize("spoken, expected", [
    ("john dot smith at hfmg dot net", ("john.smith", "hfmg.net")),
    ("j smith underscore two at h f m g dot net", ("jsmith_2", "hfmg.net")),
    ("john hyphen smith at hfmg dot net", ("john-smith", "hfmg.net")),
    ("john dash smith at hfmg dot net", ("john-smith", "hfmg.net")),
    ("john smith at the rate of hfmg dot net", ("johnsmith", "hfmg.net")),
    ("maria period lopez at hfmg point net", ("maria.lopez", "hfmg.net")),
    ("m lopez at sign h f m g dot n e t", ("mlopez", "hfmg.net")),
    ("john two three at hfmg dot net", ("john23", "hfmg.net")),
    ("It's m lopez at h f m g dot net.", ("mlopez", "hfmg.net")),
    ("Uh, p shah, at, h f m g, dot net.", ("pshah", "hfmg.net")),
    ("John.Smith@HFMG.NET.", ("john.smith", "hfmg.net")),
    ("j-smith at hfmg dot net", ("j-smith", "hfmg.net")),
    ("john smith at hfmg dot net please", ("johnsmith", "hfmg.net")),
    ("my username is john dot smith and the domain is hfmg dot net", ("john.smith", "hfmg.net")),
    ("j o h n at gmail dot com", ("john", "gmail.com")),  # another domain is kept, never forced to hfmg.net
])
async def test_username_and_domain_are_read_as_spoken(spoken, expected):
    assert nlu.split_spoken_email(spoken) == expected


@pytest.mark.parametrize("spoken", [
    "john smith hfmg dot net",  # no "at": the missing sign is not guessed
    "john at hfmg net",  # no "dot": the domain is not completed
    "d nguyen that's n g u y e n at hfmg dot net",  # a self-correction is for the model, not a rule
    "I think it's john at hfmg dot net",
    "hello can you hear me",
    "",
])
async def test_what_cannot_be_read_is_left_unread_not_guessed(spoken):
    local, domain = nlu.split_spoken_email(spoken)
    assert not (local and domain)


async def test_only_the_part_that_was_said_is_returned():
    assert nlu.split_spoken_email("at hfmg dot net") == (None, "hfmg.net")
    assert nlu.split_spoken_email("john at hfmg net") == ("john", None)


@pytest.mark.parametrize("home, said", [("hfmg.net", "h f m g dot net"), ("hmfg.net", "h m f g dot net")])
async def test_hmfg_and_hfmg_are_never_turned_into_each_other(home, said):
    """Two different domains: whichever the caller said (or the directory has) is exactly what is kept."""
    assert nlu.split_spoken_email(f"john at {said}") == ("john", home)
    assert nlu.normalize_email(f"jsmith@{home}") == f"jsmith@{home}"
    assert nlu.clean_email(f"JSmith@{home.upper()}") == f"jsmith@{home}"
    assert nlu.parse_email_section(said, "domain") == home
    assert nlu.parse_email_section(f"the domain is {said}", "domain") == home


@pytest.mark.parametrize("utterance, kind, expected", [
    ("s a i", "local", "sai"),
    ("no it's s a i dot k", "local", "sai.k"),
    ("h f m g dot net", "domain", "hfmg.net"),
    ("the domain is h m f g dot net", "domain", "hmfg.net"),
    ("it's at h f m g dot net", "domain", "hfmg.net"),
    ("j smith at hfmg dot net", "local", "jsmith"),  # said the whole address: the part asked for is taken
    ("j smith at hfmg dot net", "domain", "hfmg.net"),
    ("hfmg", "domain", None),  # no ending: not a domain
    ("", "local", None),
])
async def test_one_part_of_an_address_can_be_said_on_its_own(utterance, kind, expected):
    assert nlu.parse_email_section(utterance, kind) == expected


async def test_a_dictated_address_needs_no_language_model():
    """The deterministic reader answers first; with no model configured the address is still found."""
    result = await nlu.interpret_email("john dot smith underscore two at h f m g dot net")

    assert (result.value, result.failed) == ("john.smith_2@hfmg.net", False)


async def test_an_address_that_cannot_be_read_fails_rather_than_being_invented():
    for utterance in ("john smith hfmg dot net", "john at hfmg net"):
        assert (await nlu.interpret_email(utterance)).failed


# --- what the caller hears back ------------------------------------------------------------------------


@pytest.mark.parametrize("email, spoken", [
    ("jsmith@hfmg.net", "J, S, M, I, T, H, at, H, F, M, G, dot, net"),
    ("john.smith@hfmg.net", "J, O, H, N, dot, S, M, I, T, H, at, H, F, M, G, dot, net"),
    ("j_smith-2@hfmg.net", "J, underscore, S, M, I, T, H, hyphen, 2, at, H, F, M, G, dot, net"),
    ("tom@hmfg.net", "T, O, M, at, H, M, F, G, dot, net"),  # spelled as it is: hmfg is not read as hfmg
    ("ranga.sai@hfmg.net", "R, A, N, G, A, dot, S, A, I, at, H, F, M, G, dot, net"),  # "A" the letter, not "a"
])
async def test_every_character_is_said_slowly_so_a_wrong_one_can_be_heard(email, spoken):
    assert scripts.spoken_email(email) == spoken
    # One character at a time: a pause (comma) after each, so the letters never run together.
    assert all(len(token) == 1 or token in ("at", "dot", "underscore", "hyphen", "net", "com", "org")
               for token in spoken.split(", "))


# --- an address already on file is offered only when it is safe to ----------------------------------------


@pytest.mark.parametrize("name, email, offered", [
    ("Dana Sample", "dana.sample@example.org", "dana.sample@example.org"),
    ("Dana Sample", "Dana.Sample@Example.ORG", "dana.sample@example.org"),  # as on file, in lower case
    ("Dana Sample", "dsample@example.org", "dsample@example.org"),  # first initial + last name
    ("Dana Sample", "sample.dana@example.org", "sample.dana@example.org"),
    ("Tom Typo", "tom.typo@hmfg.net", "tom.typo@hmfg.net"),  # a misspelled domain is offered as it is, never repaired
    ("Dana Sample", "someone.else@example.org", None),  # belongs to a different person
    ("Dana Sample", "sample@example.org", None),  # last name only: not enough to say it is hers
    ("Dana Sample", "dana.smith@example.org", None),  # same first name, a different person
    ("Dana Sample", "dsmith@example.org", None),  # same first initial, a different person
    ("Dana Sample", "0301user@example.org", None),  # a placeholder
    ("Dana Sample", "helpdesk@example.org", None),  # a shared mailbox
    ("Dana Sample", "callpark1@example.org", None),
    ("Dana Sample", "", None),
    ("Dana Sample", None, None),
    ("Dana", "dana@example.org", None),  # a one-word name is not a person we can match
    ("", "dana.sample@example.org", None),
])
async def test_only_an_address_that_belongs_to_the_confirmed_person_is_offered(name, email, offered):
    assert directory_identity.directory_email_for(name, email) == offered


# --- the speech hints ----------------------------------------------------------------------------------------


async def test_the_transcription_prompts_cover_names_addresses_and_corrections():
    assert "do not translate" in speech_context.stt_prompt("COLLECT_NAME")
    assert "dot, underscore, hyphen" in speech_context.stt_prompt("COLLECT_EMAIL")
    domain = speech_context.stt_prompt("COLLECT_EMAIL", {"email_fix": {"step": "domain"}})
    assert "after the at sign" in domain and "Vocabulary" not in domain  # letters, not words: a spelling prompt
    part = speech_context.stt_prompt("CONFIRM_EMAIL", {"email_fix": {"step": "which"}})
    assert "which part" in part and "username" in part
    assert speech_context.recognition_mode("CONFIRM_EMAIL", {}) == "CONFIRM_EMAIL"  # a plain yes or no
    assert speech_context.recognition_mode("COLLECT_EMAIL", {"email_fix": {"step": "local"}}) == "SPELL_EMAIL"
    assert speech_context.recognition_mode("COLLECT_EMAIL", {"email_fix": {"step": "domain"}}).startswith("SPELL_")


# --- names: when to ask for the spelling at once -------------------------------------------------------------------


@pytest.mark.parametrize("kwargs, expected", [
    (dict(stt_confidence=0.4), True),  # the recognizer itself was unsure
    (dict(stt_confidence=0.59), True),
    (dict(stt_confidence=0.7, model_confidence="low"), True),  # both doubt it
    (dict(stt_confidence=0.7, model_confidence="high"), False),  # an ordinary read-back is enough
    (dict(stt_confidence=0.9, model_confidence="low"), False),
    (dict(stt_confidence=None, model_confidence="low"), False),  # no score from the recognizer: never assume
    (dict(stt_confidence=0.3, spelled=True), False),  # already spelled
])
async def test_the_spelling_is_asked_for_at_once_only_on_evidence_of_a_poor_hearing(kwargs, expected):
    assert names.needs_spelling("Yash Want", **kwargs) is expected


# --- flows ---------------------------------------------------------------------------------------------------------------


@pytest.fixture
async def email_directory(tmp_path, monkeypatch):
    rows = [
        row("Dana Sample", phone="(845) 555-0301", email="dana.sample@example.org", location="Test Clinic"),
        row("Hal Mismatch", phone="(845) 555-0302", email="someone.else@example.org", location="Test Clinic"),
        row("Pia Placeholder", phone="(845) 555-0303", email="0303user@example.org", location="Test Clinic"),
        row("Tom Typo", phone="(845) 555-0304", email="tom.typo@hmfg.net", location="Test Clinic"),
        row("Ned Noemail", phone="(845) 555-0305", email="", location="Test Clinic"),
        row("Una Upper", phone="(845) 555-0306", email="Una.Upper@Example.ORG", location="Test Clinic"),
        row("Zed Reserved", phone="(845) 555-0307", email="zed.reserved@example.test", location="Test Clinic"),
    ]
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, rows))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)
    return service


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture, email_directory):
    def make(call_id, number=STRANGER):
        return DirCall(client, monkeypatch, call_id, number)

    return make


async def _at_email_question(call, name="Pat Real"):
    """A caller nobody proposed a name for: start, report, give name and department."""
    await call.start()
    await call.report()
    return await call.name_and_department(name)


async def _confirmed_at_email_step(call, name):
    """A caller whose directory entry matched and who said yes: on to the department, then the email step."""
    await call.start()
    await call.report()
    await call.turn("yes")
    return await call.department(name)


async def _says(client, call_id, utterance, confidence=None):
    body = {"call_id": call_id, "utterance": utterance, **({"confidence": confidence} if confidence is not None else {})}
    return (await client.post(f"{SIP}/turn", json=body, headers=AUTH)).json()


# -- read back, and an explicit yes -------------------------------------------------------------------------------


@pytest.mark.parametrize("spoken, email, read_back", [
    ("john dot smith at h f m g dot net", "john.smith@hfmg.net", "J, O, H, N, dot, S, M, I, T, H, at, H, F, M, G, dot, net"),
    ("j smith underscore two at hfmg dot net", "jsmith_2@hfmg.net", "J, S, M, I, T, H, underscore, 2, at, H, F, M, G, dot, net"),
    ("john hyphen smith at the rate of hfmg dot net", "john-smith@hfmg.net", "J, O, H, N, hyphen, S, M, I, T, H, at, H, F, M, G, dot, net"),
])
async def test_a_spoken_address_is_read_back_in_full_and_used_only_after_a_yes(
    make_call, client, db_session, sent_emails, spoken, email, read_back
):
    call = make_call("acc-readback")
    await _at_email_question(call)

    asked = await call.turn(spoken)

    assert f"I have {read_back}. Is that correct?" in _said(asked)
    session = await _session(db_session, "acc-readback")
    assert session.state.value == "CONFIRM_EMAIL" and asked["ticket_id"] is None  # nothing filed on a guess
    filed = await call.turn("yes")
    assert (await _ticket(client, filed["ticket_id"]))["email"] == email


async def test_an_address_the_caller_cannot_be_understood_on_is_never_invented(make_call, client, db_session, sent_emails):
    call = make_call("acc-nothing")
    await _at_email_question(call)

    first = await call.turn("john smith hfmg dot net")  # no "at": never completed with a domain for them
    second = await call.turn("john at hfmg net")  # no "dot": the domain is missing, so only that is asked for
    third = await call.turn("hfmg net")  # still not a domain
    filed = await call.turn("skip")  # offered the choice, the caller skips

    assert scripts.EMAIL_RETRY in _said(first)
    assert _said(second) == scripts.EMAIL_ASK_DOMAIN
    assert scripts.EMAIL_OFFER_SKIP in _said(third)  # asked, never just dropped
    assert filed["ticket_id"]  # the ticket is still filed, without an address nobody gave in full
    assert (await _ticket(client, filed["ticket_id"]))["email"] is None


# -- fixing one part --------------------------------------------------------------------------------------------------------


async def test_a_wrong_username_is_fixed_alone_and_the_domain_is_not_asked_again(make_call, client, db_session, sent_emails):
    call = make_call("acc-fix-local")
    await _at_email_question(call)
    await call.turn("m lopes at h f m g dot net")  # heard "lopes"

    which = await call.turn("no")
    assert scripts.EMAIL_WHICH_PART in _said(which)
    assert speech_context.recognition_mode("CONFIRM_EMAIL", (await _session(db_session, "acc-fix-local")).collected) == "CONFIRM_EMAIL_PART"

    ask_local = await call.turn("the part before the at sign")
    assert scripts.EMAIL_FIX_LOCAL in _said(ask_local) and "after the at sign" not in _said(ask_local)
    session = await _session(db_session, "acc-fix-local")
    assert speech_context.recognition_mode(session.state.value, session.collected) == "SPELL_EMAIL"

    readback = await call.turn("m l o p e z")
    assert "M, L, O, P, E, Z, at, H, F, M, G, dot, net" in _said(readback)  # the domain was kept as heard
    filed = await call.turn("yes")
    assert (await _ticket(client, filed["ticket_id"]))["email"] == "mlopez@hfmg.net"


async def test_a_wrong_domain_is_fixed_alone_and_the_username_is_kept(make_call, client, db_session, sent_emails):
    call = make_call("acc-fix-domain")
    await _at_email_question(call)
    await call.turn("m lopez at gmail dot com")  # heard "gmail"

    await call.turn("no")
    ask_domain = await call.turn("the domain")
    assert scripts.EMAIL_FIX_DOMAIN in _said(ask_domain)
    session = await _session(db_session, "acc-fix-domain")
    assert speech_context.recognition_mode(session.state.value, session.collected) == "SPELL_EMAIL_DOMAIN"

    readback = await call.turn("h f m g dot net")
    assert "M, L, O, P, E, Z, at, H, F, M, G, dot, net" in _said(readback)  # the username was kept
    filed = await call.turn("yes")
    assert (await _ticket(client, filed["ticket_id"]))["email"] == "mlopez@hfmg.net"


async def test_the_corrected_part_can_come_in_the_same_breath_as_the_no(make_call, client, db_session, sent_emails):
    call = make_call("acc-fix-inline")
    await _at_email_question(call)
    await call.turn("m lopes at gmail dot com")

    domain_fixed = await call.turn("no, the domain is h f m g dot net")
    assert "M, L, O, P, E, S, at, H, F, M, G, dot, net" in _said(domain_fixed)  # only the domain changed
    local_fixed = await call.turn("no it's m l o p e z")  # a spelled username
    assert "M, L, O, P, E, Z, at, H, F, M, G, dot, net" in _said(local_fixed)
    filed = await call.turn("yes")
    assert (await _ticket(client, filed["ticket_id"]))["email"] == "mlopez@hfmg.net"


async def test_when_both_parts_are_wrong_the_address_is_started_again(make_call, client, db_session, sent_emails):
    call = make_call("acc-fix-both")
    await _at_email_question(call)
    await call.turn("m lopes at gmail dot com")

    await call.turn("no")
    again = await call.turn("both")

    assert scripts.EMAIL_RETRY in _said(again)
    session = await _session(db_session, "acc-fix-both")
    assert session.state.value == "COLLECT_EMAIL" and not session.collected.get("email")


async def test_a_part_that_cannot_be_understood_twice_starts_the_address_over(make_call, client, db_session, sent_emails):
    call = make_call("acc-fix-unclear")
    await _at_email_question(call)
    await call.turn("m lopes at hfmg dot net")
    await call.turn("no")
    await call.turn("the username")

    first = await call.turn("umm I don't know hello can you")
    second = await call.turn("hello can you hear")

    assert scripts.EMAIL_FIX_AGAIN in _said(first) and scripts.EMAIL_FIX_LOCAL in _said(first)
    assert scripts.EMAIL_RETRY in _said(second)


async def test_corrections_are_bounded_and_the_ticket_is_still_filed_without_the_email(make_call, client, db_session, sent_emails):
    call = make_call("acc-fix-limit")
    await _at_email_question(call)
    await call.turn("m lopes at hfmg dot net")
    for guess in ("m l o p e t", "m l o p e c", "m l o p e d"):  # three corrections, each still wrong
        await call.turn("no")
        await call.turn("the part before the at sign")
        await call.turn(guess)

    offered = await call.turn("no")
    filed = await call.turn("skip it")

    assert scripts.EMAIL_OFFER_SKIP in _said(offered)  # the caller chooses; email is never just dropped
    assert scripts.EMAIL_SKIPPED in _said(filed) and filed["ticket_id"]
    assert (await _ticket(client, filed["ticket_id"]))["email"] is None  # never filed with an address nobody confirmed


# -- the address on file ----------------------------------------------------------------------------------------------------


async def test_a_confirmed_caller_is_offered_their_address_on_file_and_a_yes_uses_it(make_call, client, db_session, sent_emails):
    call = make_call("acc-dir-yes", DANA)

    offered = await _confirmed_at_email_step(call, "Dana Sample")

    assert scripts.EMAIL_DIRECTORY_CONFIRM.format(email=scripts.spoken_email("dana.sample@example.org")) in _said(offered)
    session = await _session(db_session, "acc-dir-yes")
    assert session.state.value == "CONFIRM_EMAIL" and not session.collected.get("email")  # an offer, not yet an address
    filed = await call.turn("yes")
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["email"] == "dana.sample@example.org" and ticket["caller_name"] == "Dana Sample"


async def test_declining_the_address_on_file_asks_the_usual_question_and_takes_what_the_caller_says(
    make_call, client, db_session, sent_emails
):
    call = make_call("acc-dir-no", DANA)
    await _confirmed_at_email_step(call, "Dana Sample")

    asked = await call.turn("no")
    assert "Okay." in _said(asked) and "say it naturally, spell it, or say skip" in _said(asked)
    assert (await _session(db_session, "acc-dir-no")).collected.get("email_candidate") is None
    await call.turn("d sample at h f m g dot net")
    filed = await call.turn("yes")

    assert (await _ticket(client, filed["ticket_id"]))["email"] == "dsample@hfmg.net"  # the caller's, not the file's


async def test_a_different_address_given_in_answer_to_the_offer_is_read_back_like_any_other(make_call, client, db_session, sent_emails):
    call = make_call("acc-dir-other", DANA)
    await _confirmed_at_email_step(call, "Dana Sample")

    readback = await call.turn("no it's d sample at h f m g dot net")

    assert "D, S, A, M, P, L, E, at, H, F, M, G, dot, net. Is that correct?" in _said(readback)
    filed = await call.turn("yes")
    assert (await _ticket(client, filed["ticket_id"]))["email"] == "dsample@hfmg.net"


@pytest.mark.parametrize("number, name", [
    (HAL, "Hal Mismatch"),  # the address on file is someone else's
    (PIA, "Pia Placeholder"),  # a placeholder address
    (NED, "Ned Noemail"),  # no address on file
])
async def test_an_address_that_is_missing_or_not_theirs_is_not_offered_and_the_caller_is_asked(
    make_call, client, db_session, sent_emails, number, name
):
    call = make_call(f"acc-dir-none-{number[-1]}", number)

    asked = await _confirmed_at_email_step(call, name)

    assert "on file" not in _said(asked) and "say it naturally, spell it, or say skip" in _said(asked)
    session = await _session(db_session, call.call_id)
    assert session.state.value == "COLLECT_EMAIL"
    await call.turn("skip")
    assert not (await _session(db_session, call.call_id)).collected.get("email")


async def test_an_address_the_ticket_form_would_reject_is_never_offered(make_call, client, db_session, sent_emails):
    """zed.reserved@example.test fits the pattern, but the ticket cannot hold a reserved domain: offering it
    and then failing to file the ticket would lose the call. The caller is asked instead."""
    call = make_call("acc-dir-reserved", ZED)

    asked = await _confirmed_at_email_step(call, "Zed Reserved")

    assert "on file" not in _said(asked) and "say it naturally, spell it, or say skip" in _said(asked)
    await call.turn("zed at h f m g dot net")
    filed = await call.turn("yes")
    assert (await _ticket(client, filed["ticket_id"]))["email"] == "zed@hfmg.net"


async def test_a_dictated_address_the_ticket_form_would_reject_is_treated_as_not_understood(make_call, client, db_session, sent_emails):
    call = make_call("acc-reserved-dictated")
    await _at_email_question(call)

    first = await call.turn("zed at h f m g dot test")

    assert scripts.EMAIL_RETRY in _said(first) and "I have" not in _said(first)  # never read back, never confirmable


async def test_an_address_is_never_offered_to_someone_who_did_not_confirm_who_they_are(make_call, client, db_session, sent_emails):
    call = make_call("acc-dir-unconfirmed", DANA)
    await call.start()
    await call.report()
    await call.turn("no")  # it is not Dana

    asked = await call.name_and_department("Pat Real")

    assert "on file" not in _said(asked) and "dana" not in _said(asked).lower()


async def test_a_misspelled_domain_on_file_is_offered_as_it_is_never_repaired_and_never_used_unless_confirmed(
    make_call, client, db_session, sent_emails
):
    call = make_call("acc-dir-typo", TOM)

    offered = await _confirmed_at_email_step(call, "Tom Typo")

    assert "T, O, M, dot, T, Y, P, O, at, H, M, F, G, dot, net on file" in _said(offered)  # hmfg, spelled out so it can be noticed
    assert "H, F, M, G" not in _said(offered)
    asked = await call.turn("no")
    readback = await call.turn("tom dot typo at h f m g dot net")  # what the caller says is what is kept
    assert "T, O, M, dot, T, Y, P, O, at, H, F, M, G, dot, net" in _said(readback)
    filed = await call.turn("yes")
    assert (await _ticket(client, filed["ticket_id"]))["email"] == "tom.typo@hfmg.net"


async def test_the_misspelled_domain_on_file_is_filed_unchanged_if_the_caller_confirms_it(make_call, client, db_session, sent_emails):
    call = make_call("acc-dir-typo-yes", TOM)
    await _confirmed_at_email_step(call, "Tom Typo")

    filed = await call.turn("yes")

    assert (await _ticket(client, filed["ticket_id"]))["email"] == "tom.typo@hmfg.net"


async def test_an_address_on_file_in_mixed_case_is_offered_and_filed_in_lower_case(make_call, client, db_session, sent_emails):
    call = make_call("acc-dir-case", UNA)
    offered = await _confirmed_at_email_step(call, "Una Upper")
    assert f"{scripts.spoken_email('una.upper@example.org')} on file" in _said(offered)

    filed = await call.turn("yes")

    assert (await _ticket(client, filed["ticket_id"]))["email"] == "una.upper@example.org"


# -- names --------------------------------------------------------------------------------------------------------------------


async def test_a_name_the_recognizer_was_unsure_of_is_spelled_before_it_is_trusted(
    make_call, client, db_session, monkeypatch, sent_emails
):
    monkeypatch.setattr(settings, "voice_confirm_name", True)
    call = make_call("acc-name-spell")
    await call.start()
    await call.report()
    await call.last_name("Want")  # asked first; nobody in the directory has it
    call._say(interpret_name=nlu.TurnResult(
        value="Yash Want", confidence="low", unable_to_determine=False, extras={"name_confidence": "low"}
    ))

    asked = await _says(call.client, call.call_id, "Yash Want", confidence=0.4)

    assert scripts.NAME_SPELL_LEAD in _said(asked) and scripts.NAME_SPELL_FIRST_ASK in _said(asked)
    session = await _session(db_session, "acc-name-spell")
    assert session.state.value == "CONFIRM_NAME" and session.collected["name_spell_step"] == "first"
    assert speech_context.recognition_mode(session.state.value, session.collected) == "SPELL_NAME"

    last = await call.turn("Y A S H")
    assert scripts.NAME_SPELL_LAST in _said(last)
    after = await call.turn("W A N T H")
    # Spelled letter by letter: taken as spelled, not read back and asked about again.
    assert _said(after).startswith(scripts.NAME_SPELLING_NOTED) and "Is that right" not in _said(after)
    assert (await _session(db_session, "acc-name-spell")).collected["caller_name"] == "Yash Wanth"


async def test_a_clearly_heard_ordinary_name_costs_the_caller_nothing(make_call, client, db_session, monkeypatch, sent_emails):
    monkeypatch.setattr(settings, "voice_confirm_name", True)
    call = make_call("acc-name-clear")
    await call.start()
    await call.report()
    await call.last_name("Lopez")  # asked first; nobody in the directory has it
    call._say(interpret_name=nlu.TurnResult(
        value="Maria Lopez", confidence="high", unable_to_determine=False, extras={"name_confidence": "high"}
    ))

    asked = await _says(call.client, call.call_id, "Maria Lopez", confidence=0.96)

    session = await _session(db_session, "acc-name-clear")
    assert session.state.value == "COLLECT_EMAIL"
    assert scripts.NAME_SPELL_LEAD not in _said(asked) and "your first name" not in _said(asked)  # no spelling asked of a name
    assert session.collected["caller_name"] == "Maria Lopez"


async def test_a_doubtful_name_that_the_recognizer_was_fairly_sure_of_gets_the_ordinary_read_back(
    make_call, client, db_session, monkeypatch, sent_emails
):
    monkeypatch.setattr(settings, "voice_confirm_name", True)
    call = make_call("acc-name-readback")
    await call.start()
    await call.report()
    await call.last_name("Rao")  # asked first; nobody in the directory has it
    call._say(interpret_name=nlu.TurnResult(
        value="Natakarani Rao", confidence="high", unable_to_determine=False, extras={"name_confidence": "high"}
    ))

    asked = await _says(call.client, call.call_id, "Natakarani Rao", confidence=0.9)

    assert scripts.spelled_name("Natakarani Rao") in _said(asked) and scripts.NAME_SPELL_LEAD not in _said(asked)
    assert (await _session(db_session, "acc-name-readback")).state.value == "CONFIRM_NAME"


async def test_a_name_is_never_replaced_by_the_directorys_without_the_caller_saying_yes(make_call, client, db_session, sent_emails):
    call = make_call("acc-name-nosub", DANA)
    await call.start()
    await call.report()

    await call.turn("no")  # not Dana
    await call.name_and_department("Pat Real")
    await call.turn("skip")

    session = await _session(db_session, "acc-name-nosub")
    assert session.collected["caller_name"] == "Pat Real" and not session.collected.get("identity_confirmed")


# -- calls never share what they hear --------------------------------------------------------------------------------------------


async def test_simultaneous_calls_keep_their_own_names_addresses_and_corrections(make_call, client, db_session, monkeypatch, sent_emails):
    async def name_from_utterance(utterance, **kwargs):  # one stub for every call: the answer follows the utterance
        return nlu.TurnResult(value=utterance.split(",")[0], confidence="high", unable_to_determine=False, extras={"department": "Billing"})

    monkeypatch.setattr(nlu, "interpret_name", name_from_utterance)
    first, second, third = make_call("acc-par-1", STRANGER), make_call("acc-par-2", STRANGER_TWO), make_call("acc-par-3", DANA)
    for call, name in ((first, "Pat Real"), (second, "Sam Other")):  # set up one at a time, then run them together
        await call.start()
        await call.report()
        await call.turn(name.split()[-1])  # the last name, asked first: nobody in the directory has it
        await call.turn(f"{name}, Billing")
    await third.start()
    await third.report()
    await third.turn("yes")
    await third.turn("Dana Sample, Billing")

    # The first dictates correctly, the second gets the domain wrong, the third takes the address on file.
    await asyncio.gather(first.turn("john dot smith at h f m g dot net"), second.turn("a b c at gmail dot com"), third.turn("yes"))
    _, fixed = await asyncio.gather(first.turn("yes"), second.turn("no, the domain is h f m g dot net"))
    assert "A, B, C, at, H, F, M, G, dot, net" in _said(fixed)
    await second.turn("yes")

    tickets = [await _ticket(client, (await _session(db_session, cid)).ticket_id) for cid in ("acc-par-1", "acc-par-2", "acc-par-3")]
    assert [t["email"] for t in tickets] == ["john.smith@hfmg.net", "abc@hfmg.net", "dana.sample@example.org"]
    assert [t["caller_name"] for t in tickets] == ["Pat Real", "Sam Other", "Dana Sample"]

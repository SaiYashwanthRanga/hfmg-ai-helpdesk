"""Collecting an email address: a small state machine that keeps what the caller has said across turns.

From Yashwanth's keyboard call: "Ranga dot S-I-S-1" was read back at once; "Actually, it's SAI dot..." restarted the
spelling; the caller said the agent wasn't listening; email was dropped without asking; and "A dot S-A-I-Y" landed on
the callback-number question. Now: a turn that stops mid-address is kept and the caller is asked to go on; a
correction edits the draft; frustration stops the prompts; skipping is always offered before email is given up; and
an unresolved address is never mistaken for a callback answer.

Real /start and /turn routes. The model is stubbed to fail if it is called where the rules alone must decide.
Synthetic callers and addresses only.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, VoiceCallState
from app.voice import nlu, scripts
from tests.test_directory_identity import DirCall
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550399"


@pytest.fixture(autouse=True)
async def _flow(monkeypatch, db_session):
    for name, value in (("voice_ask_caller_type", True), ("voice_ask_extension", True), ("voice_confirm_callback", True),
                        ("voice_confirm_summary", True), ("voice_confirm_name", True)):
        monkeypatch.setattr(settings, name, value)
    if (await db_session.execute(select(Category).where(Category.name == "Other"))).scalar_one_or_none() is None:
        db_session.add(Category(name="Other", default_priority=Priority.MEDIUM))
        await db_session.commit()


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):  # noqa: F811
    def make(call_id):
        call = DirCall(client, monkeypatch, call_id, CALLER)
        monkeypatch.setattr(nlu, "interpret_email", _never)  # the rules must read every address in these tests
        return call

    return make


async def _never(*args, **kwargs):
    raise AssertionError("the model must not be asked to read this address")


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


async def _at_email(call):
    await call.start()
    await call.turn("I'm an employee.")
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value="The caller's keyboard is not working.", confidence="high", unable_to_determine=False,
        category="Other", priority=Priority.LOW,
        extras={"short_issue": "your keyboard", "started": "this morning", "work_blocked": False},
    ))
    await call.turn("My keyboard is not working since this morning, but I can still work.")
    call._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False,
                                            extras={"department": "Billing", "name_confidence": "high"}))
    await call.turn("Maria Lopez, billing.")
    asked = await call.turn("No, I don't have one.")  # the extension
    assert "email address" in _text(asked) and "say it naturally" in _text(asked)
    return asked


async def _state(db_session, call_id):
    return await _session(db_session, call_id)


def _readback(email: str) -> str:
    return scripts.EMAIL_CONFIRM.format(email=scripts.spoken_email(email))


# --- said naturally, or spelled -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("said, email", [
    ("jim@hfmg.net", "jim@hfmg.net"),
    ("jim at hfmg dot net", "jim@hfmg.net"),
    ("ranga dot sai at hfmg dot net", "ranga.sai@hfmg.net"),
    ("ranga dot S-A-I at H-F-M-G dot net", "ranga.sai@hfmg.net"),
    ("R A N G A dot S A I", "ranga.sai@hfmg.net"),  # spelled, no domain: the HFMG one
    ("My email is ranga dot sai at hfmg dot net.", "ranga.sai@hfmg.net"),
])
async def test_an_address_said_naturally_or_spelled_is_read_back_once_and_saved_only_after_yes(
    make_call, client, db_session, sent_emails, said, email
):
    call = make_call("em-formats")
    await _at_email(call)

    out = await call.turn(said)

    assert _text(out) == _readback(email)
    assert not (await _state(db_session, "em-formats")).collected.get("email_confirmed")
    await call.turn("Yes.")  # the address
    c = (await _state(db_session, "em-formats")).collected
    assert c["email"] == email and c["email_confirmed"] is True


# --- corrections are edits ---------------------------------------------------------------------------------------------


async def test_a_corrected_segment_replaces_only_that_segment(make_call, db_session):
    call = make_call("em-segment")
    await _at_email(call)
    first = await call.turn("Ranga dot S-I-S-1.")
    assert _text(first) == _readback("ranga.sis1@hfmg.net")

    out = await call.turn("No, it's S A I.")

    assert _text(out) == _readback("ranga.sai@hfmg.net")


async def test_a_correction_can_correct_only_the_domain(make_call, db_session):
    call = make_call("em-domain")
    await _at_email(call)
    await call.turn("m lopez at gmail dot com")

    out = await call.turn("No, it's at h f m g dot net.")

    assert _text(out) == _readback("mlopez@hfmg.net")  # the username was kept


async def test_an_address_given_over_three_turns_is_put_together(make_call, db_session):
    call = make_call("em-pieces")
    await _at_email(call)

    first = await call.turn("Ranga dot")
    second = await call.turn("sai dot")
    third = await call.turn("yashwanth at hfmg dot net")

    assert _text(first) == scripts.EMAIL_CONTINUE and _text(second) == scripts.EMAIL_CONTINUE_AGAIN
    assert _text(third) == _readback("ranga.sai.yashwanth@hfmg.net")


async def test_a_missing_domain_is_asked_for_alone(make_call, db_session):
    call = make_call("em-missing-domain")
    await _at_email(call)

    asked = await call.turn("ranga dot sai at")
    out = await call.turn("h f m g dot net")

    assert _text(asked) == scripts.EMAIL_ASK_DOMAIN
    assert _text(out) == _readback("ranga.sai@hfmg.net")


# --- the caller is not finished, or is frustrated ------------------------------------------------------------------------


async def test_let_me_finish_stops_the_prompts_and_the_next_answer_is_still_the_address(make_call, db_session):
    call = make_call("em-finish")
    await _at_email(call)
    await call.turn("Ranga dot")

    waited = await call.turn("Wait, you're not listening, let me finish.")
    out = await call.turn("sai at hfmg dot net")

    assert _text(waited) == scripts.EMAIL_LET_ME_FINISH
    assert _text(out) == _readback("ranga.sai@hfmg.net")  # carried on from "Ranga dot"
    session = await _state(db_session, "em-finish")
    assert session.email_attempt_count == 0  # neither turn was a failed attempt


async def test_a_frustrated_caller_is_offered_the_skip_sooner(make_call, db_session):
    call = make_call("em-frustrated")
    call._say(interpret_email=nlu.TurnResult())  # the model cannot read these either
    await _at_email(call)
    await call.turn("You're not listening to me.")

    out = await call.turn("I said it right already")  # nothing readable, once

    assert _text(out) == scripts.EMAIL_OFFER_SKIP


# --- skipping ----------------------------------------------------------------------------------------------------------


async def test_skip_is_offered_after_two_unsuccessful_tries_and_the_ticket_is_filed_without_an_email(
    make_call, client, db_session, sent_emails
):
    call = make_call("em-skip")
    call._say(interpret_email=nlu.TurnResult())  # the model cannot read these either
    await _at_email(call)
    first = await call.turn("john smith hfmg dot net")  # no "at": never completed for them
    offered = await call.turn("I said it right already")

    skipped = await call.turn("No, skip it.")

    assert _text(first) == scripts.EMAIL_RETRY and _text(offered) == scripts.EMAIL_OFFER_SKIP
    assert _text(skipped).startswith(scripts.EMAIL_SKIPPED) and "callback number" in _text(skipped).lower()
    c = (await _state(db_session, "em-skip")).collected
    assert c["email_skipped"] == "caller" and not c.get("email")


async def test_try_again_after_the_offer_asks_once_more(make_call, db_session):
    call = make_call("em-again")
    call._say(interpret_email=nlu.TurnResult())  # the model cannot read these either
    await _at_email(call)
    await call.turn("john smith hfmg dot net")
    await call.turn("I said it right already")

    again = await call.turn("Let's try again.")
    out = await call.turn("jim at hfmg dot net")

    assert _text(again) == scripts.EMAIL_ASK_AGAIN and _text(out) == _readback("jim@hfmg.net")


async def test_skip_at_the_first_question_is_unchanged(make_call, db_session):
    call = make_call("em-skip-first")
    await _at_email(call)

    out = await call.turn("skip")

    assert "callback number" in _text(out).lower()
    assert (await _state(db_session, "em-skip-first")).collected["email_skipped"] == "caller"


# --- Yashwanth's call, and the callback question ---------------------------------------------------------------------


async def test_yashwanths_sequence_keeps_the_address_and_never_reaches_the_callback_question(make_call, db_session):
    call = make_call("em-yashwanth")
    await _at_email(call)

    first = await call.turn("Ranga dot S-I-S-1.")
    which = await call.turn("No.")
    carry_on = await call.turn("Actually, it's SAI dot…")
    waited = await call.turn("You're not listening.")
    out = await call.turn("A dot S-A-I-Y at HFMG dot net.")

    assert _text(first) == _readback("ranga.sis1@hfmg.net")
    assert scripts.EMAIL_WHICH_PART in _text(which)
    assert _text(carry_on) == scripts.EMAIL_CONTINUE  # kept, never "let's try once more"
    assert _text(waited) == scripts.EMAIL_LET_ME_FINISH
    assert _text(out) == _readback("sai.a.saiy@hfmg.net")  # what was said, read back for the caller to confirm
    session = await _state(db_session, "em-yashwanth")
    assert session.state == VoiceCallState.CONFIRM_EMAIL and session.email_attempt_count == 0
    assert "callback" not in " ".join(t["text"] for t in session.turns if t["role"] == "agent").lower()


async def test_an_address_fragment_at_the_callback_question_goes_back_to_the_email(make_call, db_session):
    """The agent gave up on the email (not the caller); the caller is still dictating at the callback question."""
    call = make_call("em-back")
    call._say(interpret_email=nlu.TurnResult())  # the model cannot read these either
    await _at_email(call)
    await call.turn("john smith hfmg dot net")
    await call.turn("I said it right already")
    await call.turn("hmm what")  # unreadable after the offer: the agent carries on without the email
    session = await _state(db_session, "em-back")
    assert session.state == VoiceCallState.CONFIRM_CALLBACK_NUMBER and session.collected["email_skipped"] == "agent"

    out = await call.turn("J O H N dot S at hfmg dot net")

    assert _text(out) == f"{scripts.EMAIL_BACK_TO_EMAIL} {_readback('john.s@hfmg.net')}"
    confirmed = await call.turn("Yes.")  # the address -- and only then the callback number again
    assert "callback number" in _text(confirmed).lower()


async def test_a_yes_at_the_callback_question_is_the_callback_answer(make_call, db_session):
    call = make_call("em-callback-yes")
    await _at_email(call)
    await call.turn("skip")

    out = await call.turn("Yes.")

    assert "callback number" not in _text(out).lower()  # the number was confirmed: on to the read-back
    assert (await _state(db_session, "em-callback-yes")).collected["callback_confirmed"] is True


async def test_an_email_and_an_extension_in_one_sentence_are_both_taken(make_call, db_session):
    call = make_call("em-and-extension")
    await call.start()
    await call.turn("I'm an employee.")
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value="The caller's keyboard is not working.", confidence="high", unable_to_determine=False,
        category="Other", priority=Priority.LOW,
        extras={"short_issue": "your keyboard", "started": "this morning", "work_blocked": False},
    ))
    await call.turn("My keyboard is not working.")
    call._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False,
                                            extras={"department": "Billing", "name_confidence": "high"}))
    await call.turn("Maria Lopez, billing.")
    await call.turn("It's 3000.")  # the extension, then corrected with the email

    out = await call.turn("ranga dot sai at hfmg dot net, and my extension is 3006")

    assert out["lines"] == [scripts.EXTENSION_NOTED.format(extension="3 0 0 6"), _readback("ranga.sai@hfmg.net")]


# --- the reader --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("said, local, domain, incomplete, correction", [
    ("Actually, it's SAI dot…", "sai.", None, True, True),
    ("ranga underscore", "ranga_", None, True, False),
    ("m lopez at gmail dot com", "mlopez", "gmail.com", False, False),
    ("I said it right", None, None, False, False),  # conversation, not an address
    ("the part before the at sign", None, None, False, False),
    ("john smith hfmg dot net", None, None, False, False),  # a domain with no "at": never completed
    ("gibberish", None, None, False, False),  # a lone word is left to the model, as before
])
async def test_what_is_read_from_one_turn(said, local, domain, incomplete, correction):
    fragment = nlu.read_email_fragment(said)
    assert (fragment.local, fragment.domain, fragment.incomplete, fragment.correction) == (local, domain, incomplete, correction)


async def test_a_lone_word_continues_an_unfinished_address():
    assert nlu.read_email_fragment("yashwanth", continuing=True).local == "yashwanth"

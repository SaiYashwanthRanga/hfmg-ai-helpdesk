"""Regression: the "Rakesh" password-expiration test call, turn by turn.

What went wrong on the call, and what must hold now:
  1. "it was working yesterday and expired today" -- the start time was never recorded;
  2. the caller ID matched a directory entry for Chandu Natakarani, and the caller said no -- that rejected entry
     must not shape who the caller is afterwards (never proposed again, never confirmed, never their name);
  3. the spelled read-back "R A K E S H" got an affirmative but imperfect answer -- a yes, under the same rules
     as a plain yes, never "Rakeshrakesh" and never the recognizer's spoken form;
  4. "my extension is 3006" while the email question was open was taken as a failed email and the caller was
     asked to spell their email again -- it is an extension correction, and the email question is asked again;
  5. "Yeah, I'm unable to log in." was recorded as the account or application -- it names nothing: the agent
     asks which one, then records it as not identified.

Edge cases closed after it: an email and an extension in one sentence are both taken; less usual wording of when it
started ("for two days", "since the update", "around 9 am") is read; and "somebody else" is a request for a person
only when the caller asks for one ("I'm somebody else" at the identity question is a no).

The exact transcript was not available (the call ran on the deployed server): the caller's words below are the
patterns described from it. Simulated through the real /start and /turn routes with the language model stubbed:
not a live SIP call, and no email is delivered. Every person and number here is synthetic.
"""

import re

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, VoiceCallState
from app.main import app
from app.services.user_directory import UserDirectoryService
from app.voice import diagnostics as dx
from app.voice import nlu, orchestrator, scripts
from tests.test_directory_identity import DirCall
from tests.test_multi_issue_calls import _async, _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550311"  # the directory lists "Chandu Natakarani", extension 3001, for this number
PROBLEM = "My login credentials expired. They were working yesterday and they expired today."
ACCOUNT_Q = next(q for q in dx.ACCOUNT.questions if q.key == "account")
_REAL_INTERPRET_EMAIL = nlu.interpret_email


@pytest.fixture(autouse=True)
async def _production_flow(monkeypatch, db_session, tmp_path):
    for name, value in (("voice_ask_caller_type", True), ("voice_diagnostic_questions", True),
                        ("voice_ask_extension", True), ("voice_confirm_callback", True),
                        ("voice_confirm_summary", True), ("voice_confirm_name", True)):
        monkeypatch.setattr(settings, name, value)
    for name in ("Other", "Password"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, [
        row("Chandu Natakarani", phone="(845) 555-0311", ext="3001", email="chandu.natakarani@example.org"),
    ]))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):  # noqa: F811
    def make(call_id):
        return DirCall(client, monkeypatch, call_id, CALLER)

    return make


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


def _never(name):
    async def called(*args, **kwargs):
        raise AssertionError(f"{name} must not be called for this answer")

    return called


async def _describe(call, words=PROBLEM, *, started=None, work_blocked=True):
    """Employee, then the problem. The model's reading has no start time unless one is given: on the call, the
    caller's words had one and the record did not."""
    await call.start()
    await call.turn("I'm an employee.")
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value="The caller's login credentials expired.", confidence="high", unable_to_determine=False,
        category="Password", priority=Priority.MEDIUM,
        extras={"short_issue": "your password", "started": started, "work_blocked": work_blocked},
    ))
    return await call.turn(words)


async def _to_identity_question(call):
    asked = await _describe(call)
    assert ACCOUNT_Q.prompt in _text(asked)
    await call.turn("My Windows login.")
    await call.turn("It says my password has expired.")
    proposal = await call.turn("Yes.")  # it was working before
    assert _text(proposal) == scripts.IDENTITY_CONFIRM.format(name="Chandu Natakarani")
    return proposal


async def _to_name_read_back(call):
    await _to_identity_question(call)
    await call.turn("No.")
    call._say(interpret_name=nlu.TurnResult(
        value="Rakesh", confidence="high", unable_to_determine=False,
        extras={"department": "IT", "name_confidence": "low"},
    ))
    read_back = await call.turn("Rakesh, IT department.")
    assert scripts.spelled_name("Rakesh") in _text(read_back)
    return read_back


# --- the whole call ------------------------------------------------------------------------------------------------


async def test_the_reported_call_end_to_end(make_call, client, db_session, monkeypatch, sent_emails):
    call = make_call("rakesh-call")
    heard = []

    async def say(utterance):
        out = await call.turn(utterance)
        heard.append(_text(out))
        return out

    asked = await _describe(call)
    heard.append(_text(asked))
    assert (await _session(db_session, "rakesh-call")).collected["started"] == "today"  # 1.
    assert ACCOUNT_Q.prompt in _text(asked)  # start and work impact known: straight to the account questions

    again = await say("Yeah, I'm unable to log in.")  # 5.
    assert _text(again) == ACCOUNT_Q.clarify
    await say("Yeah, I'm unable to log in.")
    await say("It says my password has expired.")
    proposal = await say("Yes.")
    assert "Chandu Natakarani" in _text(proposal)

    call._say(interpret_name=nlu.TurnResult(
        value="Rakesh", confidence="high", unable_to_determine=False, extras={"name_confidence": "low"},
    ))
    declined = await say("No, actually this is Rakesh.")  # 2.
    assert _text(declined) == f"{scripts.IDENTITY_DECLINED} {scripts.DEPARTMENT_ASK.format(first_name='Rakesh')}"
    call._say(interpret_name=nlu.TurnResult(value=None, unable_to_determine=False, extras={"department": "IT"}))
    read_back = await say("IT.")
    assert scripts.spelled_name("Rakesh") in _text(read_back)

    monkeypatch.setattr(nlu, "interpret_name_correction", _never("interpret_name_correction"))
    extension_q = await say("Yeah, yeah, that's right, Rakesh, R A K E S H.")  # 3.
    assert _text(extension_q) == scripts.EXTENSION_ASK

    email_q = await say("Yes, it's 3000.")
    assert "extension 3 0 0 0" in _text(email_q) and "email" in _text(email_q).lower()
    monkeypatch.setattr(nlu, "interpret_email", _never("interpret_email"))
    corrected = await say("Sorry, my extension is 3006, not 3000.")  # 4.
    assert _text(corrected) == f"{scripts.EXTENSION_NOTED.format(extension='3 0 0 6')} {scripts.EMAIL_ASK_NO_NAME}"
    assert scripts.EMAIL_RETRY not in _text(corrected)
    monkeypatch.setattr(nlu, "interpret_email", _REAL_INTERPRET_EMAIL)

    callback = await say("skip")
    assert "callback number" in _text(callback).lower()
    summary = await say("Yes.")
    assert "It started today." in _text(summary) and "Chandu" not in _text(summary)
    filed = await say("Yes.")
    assert filed["ticket_id"]

    session = await _session(db_session, "rakesh-call")
    c = session.collected
    assert c["caller_name"] == "Rakesh" and c["name_confirmed_as"] == "Rakesh"
    assert not c.get("identity_confirmed") and not c.get("directory_identity")
    assert c["directory"] == {"status": "matched", "rejected": True}  # nothing of the rejected entry is kept
    assert (c["extension"], c["extension_source"]) == ("3006", "caller")
    assert session.email_attempt_count == 0  # the correction was never counted as an email answer

    ticket = await _ticket(client, filed["ticket_id"])
    intake = ticket["description"].split("--- Call transcript ---")[0]
    assert ticket["caller_name"] == "Rakesh"
    assert "Started: today" in intake
    assert "Extension: 3006 (given by the caller; unverified: not found in the directory)" in intake
    assert 'Account or application: not identified (caller said: "Yeah, I\'m unable to log in.")' in intake
    assert "Identity:" not in intake and "Chandu" not in intake and "3001" not in intake
    assert "Department: IT" in intake
    # The helpdesk email -- which is what a Freshservice mailbox turns into its ticket -- carries the extension
    # just as it carries the email, with the note that says nobody checked it.
    body = sent_emails[-1]["body_html"]
    assert "Email:<br>\nNot provided<br>\n<br>\n" in body
    assert "Extension:<br>\n3006 (given by the caller; unverified: not found in the directory)<br>\n" in body
    assert body.index("Email:") < body.index("Extension:") < body.index("Issue:")
    # Never a request for a secret (the account question says not to give one).
    asks_for_secret = re.compile(
        r"(?<!don't )\b(?:tell me|give me|say|spell|read|confirm|what's|what is) your (?:\w+ )?(?:password|passcode|pin|code)\b",
        re.IGNORECASE,
    )
    assert not [line for line in heard if asks_for_secret.search(line)]
    assert "Please don't tell me your password." in heard[0]


# --- 1. the start time, from the caller's own words ------------------------------------------------------------------


@pytest.mark.parametrize("said, started", [
    ("My login credentials expired. They were working yesterday and they expired today.", "today"),
    ("It worked yesterday, it expired today.", "today"),
    ("Yesterday it expired, today I can't log in.", "yesterday"),
    ("It was fine yesterday and today it's expired.", "today"),
    ("It stopped working about two hours ago.", "about two hours ago"),
    ("Since this morning.", "since this morning"),
    # Less usual wording
    ("It's been like this for two days.", "for two days"),
    ("It's been going on for the past hour.", "for the past hour"),
    ("It's been down for a couple of days now.", "for a couple of days"),
    ("Ever since the update it won't let me in.", "since the update"),
    ("Since I came in this morning.", "since I came in"),
    ("since about 8:30", "since about 8:30"),
    ("It started around 9 am.", "around 9 am"),
    ("It crashed at 10:30.", "at 10:30"),
    ("It quit working after lunch.", "after lunch"),
    ("It began earlier today.", "earlier today"),
    ("Last Friday it stopped working.", "last Friday"),
    ("It broke a few days back.", "a few days back"),
    ("First thing this morning it locked me out.", "first thing this morning"),
    ("It stopped working on Monday morning.", "on Monday morning"),
    # Not a start time
    ("It worked yesterday.", None),  # when it last worked is not when it started
    ("It was fine yesterday, it expired.", None),
    ("It worked until 9 am.", None),
    ("My password expired.", None),
    ("I can't log in today.", None),
    ("I started a new job today.", None),  # about the caller, not the problem
    ("Since the printer is broken I can't print.", None),  # causal "since"
    ("Since I can't log in, I can't do anything.", None),
    ("I can't work, I need it by 3 pm.", None),
])
async def test_the_start_is_read_only_when_the_caller_says_it(said, started):
    assert nlu.quick_started(said) == started


@pytest.mark.parametrize("said, started", [
    ("This morning.", "this morning"), ("Around 9 am, I think.", "around 9 am"), ("Um, it was last Monday.", "last Monday"),
    ("Yesterday.", "yesterday"), ("I can't work today.", None), ("No.", None),
])
async def test_a_bare_time_counts_only_as_the_answer_to_when_it_started(said, started):
    assert nlu.quick_started(said, answers_when=True) == started
    assert nlu.quick_started(said) is None  # anywhere else, a time on its own says nothing about the start


async def test_a_bare_time_answering_the_start_question_is_recorded(make_call, db_session):
    call = make_call("rakesh-start-bare")
    asked = await _describe(call, "My password expired.", work_blocked=True)
    assert _text(asked).endswith(scripts.DETAILS_ASK_STARTED)
    call._say(interpret_details=nlu.TurnResult(value=None, unable_to_determine=True, extras={"started": None}))

    await call.turn("Around 9 am, I think.")

    assert (await _session(db_session, "rakesh-start-bare")).collected["started"] == "around 9 am"


async def test_a_bare_time_answering_another_question_is_not_taken_as_the_start(make_call, db_session):
    call = make_call("rakesh-start-bare-other")
    await _describe(call, "My password expired.", work_blocked=True)
    call._say(interpret_details=nlu.TurnResult(value=None, unable_to_determine=True, extras={"started": None}))
    await call.turn("I'm not sure.")  # the start question, unanswered

    await call.turn("This morning.")  # the account question: not when it started

    assert not (await _session(db_session, "rakesh-start-bare-other")).collected.get("started")


async def test_a_start_said_in_answer_to_the_details_question_is_recorded(make_call, db_session):
    call = make_call("rakesh-start-details")
    asked = await _describe(call, "My password expired.", work_blocked=None)
    assert "start" in _text(asked).lower()
    call._say(interpret_details=nlu.TurnResult(  # the model's reading missed the start again
        value=None, unable_to_determine=False, extras={"started": None, "work_blocked": True},
    ))

    await call.turn("It was working yesterday and it expired today, so I can't work.")

    assert (await _session(db_session, "rakesh-start-details")).collected["started"] == "today"


async def test_a_start_said_while_answering_a_diagnostic_question_is_recorded(make_call, db_session):
    call = make_call("rakesh-start-diag")
    asked = await _describe(call, "My password expired and I can't work.")
    assert "start" in _text(asked).lower()
    call._say(interpret_details=nlu.TurnResult(value=None, unable_to_determine=True, extras={"started": None}))
    for said in ("I don't really know.", "My Windows login.", "It says my password has expired."):
        await call.turn(said)

    await call.turn("Yes, it worked yesterday and it expired today.")  # "Was it working for you before?"

    collected = (await _session(db_session, "rakesh-start-diag")).collected
    assert collected["started"] == "today" and collected["diagnostics"]["values"]["worked_before"] == "yes"


async def test_a_start_time_already_recorded_is_never_replaced(make_call, db_session):
    call = make_call("rakesh-start-kept")
    await _describe(call, PROBLEM, started="this morning")

    assert (await _session(db_session, "rakesh-start-kept")).collected["started"] == "this morning"


# --- 2. a rejected directory match stays rejected ---------------------------------------------------------------------


@pytest.mark.parametrize("said", ["No, actually this is Rakesh.", "Nope, it's not me, I'm Rakesh from IT.",
                                  "Not Chandu, this is Rakesh."])
async def test_a_no_with_the_callers_own_name_is_a_no_and_the_name_is_kept(make_call, db_session, said):
    call = make_call("rakesh-reject")
    await _to_identity_question(call)
    call._say(interpret_name=nlu.TurnResult(
        value="Rakesh", confidence="high", unable_to_determine=False, extras={"name_confidence": "low"},
    ))

    out = await call.turn(said)

    assert "Chandu" not in _text(out)  # never proposed again
    collected = (await _session(db_session, "rakesh-reject")).collected
    assert collected["caller_name"] == "Rakesh" and not collected.get("identity_confirmed")
    assert collected["directory"].get("rejected") and "proposed_name" not in collected["directory"]


async def test_after_a_long_no_a_short_yes_can_never_confirm_the_rejected_name(make_call, db_session):
    """Before: the long no was "unclear", Chandu was proposed again, and "Yes, Rakesh here" confirmed Chandu."""
    call = make_call("rakesh-no-then-yes")
    await _to_identity_question(call)
    call._say(interpret_name=nlu.TurnResult(value=None, unable_to_determine=True))
    out = await call.turn("Nope, that's not me, this phone is shared by our whole team.")
    assert "Chandu" not in _text(out)
    call._say(interpret_name=nlu.TurnResult(
        value="Rakesh", confidence="high", unable_to_determine=False, extras={"department": "IT"},
    ))

    await call.turn("Yes, Rakesh here, IT.")

    collected = (await _session(db_session, "rakesh-no-then-yes")).collected
    assert collected["caller_name"] == "Rakesh" and not collected.get("identity_confirmed")


async def test_im_somebody_else_at_the_identity_question_is_a_no_not_a_request_for_a_person(make_call, db_session):
    """Before: "somebody else" anywhere was a request for a person, so this ended the intake in a callback."""
    call = make_call("rakesh-somebody-else")
    await _to_identity_question(call)
    call._say(interpret_name=nlu.TurnResult(value=None, unable_to_determine=True))

    out = await call.turn("Nope, that's not me, the phone is shared, I'm somebody else.")

    session = await _session(db_session, "rakesh-somebody-else")
    assert session.state == VoiceCallState.COLLECT_NAME and not session.escalated
    assert "Chandu" not in _text(out) and session.collected["directory"].get("rejected")


@pytest.mark.parametrize("said", ["Can I talk to somebody else?", "I'd rather speak with someone else.",
                                  "Put me through to somebody else, please."])
async def test_asking_for_somebody_else_still_hands_the_call_to_a_person(make_call, db_session, said):
    call = make_call("rakesh-wants-person")
    await _to_identity_question(call)

    out = await call.turn(said)

    session = await _session(db_session, "rakesh-wants-person")
    assert session.escalated and session.state == VoiceCallState.ESCALATED and out["ticket_id"]


@pytest.mark.parametrize("said, wants_person", [
    ("Can I talk to somebody else?", True), ("I need somebody else.", True), ("Can somebody else help me?", True),
    ("Is there anyone else I can talk to?", True), ("Could someone else take my call?", True),
    ("talk to someone", True), ("Transfer me", True),
    ("I'm somebody else.", False), ("This phone belongs to somebody else.", False),
    ("It's happening to someone else too.", False), ("Somebody else logged into my account.", False),
    ("No, that's somebody else's extension.", False), ("Is there anyone else affected?", False),
])
async def test_somebody_else_is_a_request_for_a_person_only_when_the_caller_asks_for_one(said, wants_person):
    assert nlu.mentions_escalation(said) is wants_person


async def test_a_model_reading_that_returns_the_rejected_name_is_dropped(make_call, db_session):
    call = make_call("rakesh-reject-misread")
    await _to_identity_question(call)
    call._say(interpret_name=nlu.TurnResult(value="Chandu Natakarani", confidence="high", unable_to_determine=False))

    out = await call.turn("No, I'm not Chandu Natakarani.")

    collected = (await _session(db_session, "rakesh-reject-misread")).collected
    assert not collected.get("caller_name") and not collected.get("identity_confirmed")
    assert collected.get("directory", {}).get("rejected")
    assert "Chandu" not in _text(out)
    assert (await _session(db_session, "rakesh-reject-misread")).state == VoiceCallState.COLLECT_NAME  # asked who they are


async def test_a_plain_no_needs_no_model_call(make_call, db_session, monkeypatch):
    call = make_call("rakesh-plain-no")
    await _to_identity_question(call)
    monkeypatch.setattr(nlu, "interpret_name", _never("interpret_name"))

    out = await call.turn("No.")

    assert _text(out).startswith(scripts.IDENTITY_DECLINED) and "Chandu" not in _text(out)


# --- 3. an affirmative but imperfect answer to the spelled read-back -------------------------------------------------


@pytest.mark.parametrize("said", [
    "Yeah, yeah, that's right, Rakesh, R A K E S H.",  # said, then spelled: not "Rakeshrakesh"
    "Yes, that's correct. My name is Rakesh.",
    "Yes, R A K E S H is correct.",
    "Yes, that's correct, Rakash.",  # the recognizer's spoken form never replaces the spelled read-back
    "Correct, just Rakesh.",
])
async def test_a_yes_with_the_name_said_again_confirms_the_read_back(make_call, db_session, monkeypatch, said):
    call = make_call("rakesh-yes")
    await _to_name_read_back(call)
    monkeypatch.setattr(nlu, "interpret_name_correction", _never("interpret_name_correction"))

    out = await call.turn(said)

    session = await _session(db_session, "rakesh-yes")
    assert session.collected["caller_name"] == "Rakesh" and session.collected["name_confirmed_as"] == "Rakesh"
    assert session.state == VoiceCallState.COLLECT_EXTENSION and _text(out) == scripts.EXTENSION_ASK
    assert session.misunderstanding_count == 0


async def test_a_yes_that_adds_to_the_name_still_goes_through_the_correction_and_is_read_back(make_call, db_session):
    call = make_call("rakesh-yes-more")
    await _to_name_read_back(call)
    call._say(interpret_name_correction=nlu.TurnResult(value="Rakesh Kumar", unable_to_determine=False))

    out = await call.turn("Yes, that's right, it's Rakesh Kumar.")

    assert scripts.NAME_RECONFIRM.format(spelled=scripts.spelled_name("Rakesh Kumar")) == _text(out)


async def test_a_yes_with_a_different_spelling_is_taken_as_the_spelling(make_call, db_session):
    """Unchanged rule: letters spelled at the read-back are the caller's own spelling."""
    call = make_call("rakesh-yes-spelled-other")
    await _to_name_read_back(call)

    await call.turn("Yes but it's R A K E S H H.")

    assert (await _session(db_session, "rakesh-yes-spelled-other")).collected["caller_name"] == "Rakeshh"


@pytest.mark.parametrize("heard, expected", [
    ("Yeah, yeah, that's right, Rakesh, R A K E S H.", "Rakesh"),
    ("R A K E S H, Rakesh", "Rakesh"),
    ("YASH W A N T H", "Yashwanth"),  # a fragment the recognizer joined is still part of the name
])
async def test_a_name_said_beside_its_spelling_is_one_name(heard, expected):
    assert nlu.decode_spelled(heard) == expected


# --- 4. an extension corrected while another question is open ---------------------------------------------------------


async def _to_email_question(call):
    await _to_name_read_back(call)
    await call.turn("Yes.")
    asked = await call.turn("Yes, it's 3000.")
    assert "email" in _text(asked).lower()


async def test_an_extension_correction_at_the_read_back_updates_only_the_extension(make_call, db_session):
    call = make_call("rakesh-ext-summary")
    await _to_email_question(call)
    await call.turn("skip")
    summary = await call.turn("Yes.")  # the callback number
    assert scripts.SUMMARY_UPDATED not in _text(summary)

    out = await call.turn("Oh, my extension is actually 3006.")

    assert _text(out) == f"{scripts.EXTENSION_NOTED.format(extension='3 0 0 6')} {scripts.SUMMARY_REST_CORRECT}"
    collected = (await _session(db_session, "rakesh-ext-summary")).collected
    assert (collected["extension"], collected["extension_note"]) == ("3006", "unverified: not found in the directory")
    assert (await call.turn("Yes."))["ticket_id"]


async def test_an_extension_correction_while_the_email_is_read_back_asks_that_question_again(make_call, db_session):
    call = make_call("rakesh-ext-confirm-email")
    await _to_email_question(call)
    confirm = await call.turn("R A K E S H")
    assert "Is that correct?" in _text(confirm)

    out = await call.turn("Wait, the extension is three zero zero six.")

    assert _text(out) == (f"{scripts.EXTENSION_NOTED.format(extension='3 0 0 6')} "
                          f"{scripts.EMAIL_CONFIRM.format(email=scripts.spoken_email('rakesh@hfmg.net'))}")
    collected = (await _session(db_session, "rakesh-ext-confirm-email")).collected
    assert collected["extension"] == "3006" and collected["email"] == "rakesh@hfmg.net"


@pytest.mark.parametrize("said, found", [
    ("Sorry, my extension is 3006, not 3000.", ("3006", "Sorry")),
    ("Not 3000, the extension is 3006.", ("3006", "")),
    ("Oh, ext. three zero zero six.", ("3006", "Oh")),
    ("R A K E S H", None),  # an email answer
    ("It's 3006.", None),  # no "extension": digits at the email question may be part of the address
    # Both in one sentence: the extension clause is taken out, and the rest answers the email question.
    ("rakesh at hfmg dot net, and my extension is 3006", ("3006", "rakesh at hfmg dot net")),
    ("My extension is 3006 and my email is R A K E S H", ("3006", "my email is R A K E S H")),
    ("Extension 3006, and skip the email.", ("3006", "skip the email")),
])
async def test_only_an_explicit_extension_is_taken_out_of_an_answer_at_the_email_question(said, found):
    from app.db.models import VoiceCallSession

    session = VoiceCallSession(call_id="x", from_number=CALLER, to_number="+1", state=VoiceCallState.COLLECT_EMAIL,
                               collected={"caller_type": "INTERNAL_IT"}, turns=[])
    assert orchestrator._late_extension(session, said) == found


async def test_an_email_and_an_extension_in_one_sentence_are_both_taken(make_call, db_session):
    call = make_call("rakesh-ext-and-email")
    await _to_email_question(call)

    out = await call.turn("rakesh at hfmg dot net, and my extension is 3006")

    noted = scripts.EXTENSION_NOTED.format(extension="3 0 0 6")
    read_back = scripts.EMAIL_CONFIRM.format(email=scripts.spoken_email("rakesh@hfmg.net"))
    assert out["lines"] == [noted, read_back]
    session = await _session(db_session, "rakesh-ext-and-email")
    c = session.collected
    assert (c["extension"], c["extension_source"]) == ("3006", "caller")
    assert c["extension_note"] == "unverified: not found in the directory"
    assert c["email"] == "rakesh@hfmg.net" and not c.get("email_confirmed")  # still read back before it is used
    assert session.state == VoiceCallState.CONFIRM_EMAIL
    assert session.turns[-1] == {**session.turns[-1], "role": "agent", "text": f"{noted} {read_back}"}


async def test_an_extension_given_first_then_a_spelled_email_are_both_taken(make_call, db_session):
    call = make_call("rakesh-ext-then-email")
    await _to_email_question(call)

    out = await call.turn("My extension is 3006 and my email is R A K E S H")

    assert out["lines"][0] == scripts.EXTENSION_NOTED.format(extension="3 0 0 6")
    c = (await _session(db_session, "rakesh-ext-then-email")).collected
    assert c["extension"] == "3006" and c["email"] == "rakesh@hfmg.net"


async def test_a_yes_and_an_extension_at_the_email_read_back_confirms_the_email(make_call, db_session):
    call = make_call("rakesh-yes-and-ext")
    await _to_email_question(call)
    await call.turn("R A K E S H")

    out = await call.turn("Yes, and my extension is 3006.")

    assert out["lines"][0] == scripts.EXTENSION_NOTED.format(extension="3 0 0 6")
    session = await _session(db_session, "rakesh-yes-and-ext")
    assert session.collected["email_confirmed"] is True and session.collected["extension"] == "3006"
    assert session.state == VoiceCallState.CONFIRM_CALLBACK_NUMBER


async def test_an_unreadable_email_with_an_extension_keeps_the_extension_and_retries_only_the_email(make_call, db_session):
    call = make_call("rakesh-ext-bad-email")
    await _to_email_question(call)
    call._say(interpret_email=nlu.TurnResult())  # the model could not read the address part

    out = await call.turn("It's mumble mumble, and my extension is 3006")

    assert out["lines"] == [scripts.EXTENSION_NOTED.format(extension="3 0 0 6"), scripts.EMAIL_RETRY]
    session = await _session(db_session, "rakesh-ext-bad-email")
    assert session.collected["extension"] == "3006" and session.email_attempt_count == 1


# --- 5. an account answer that names nothing ---------------------------------------------------------------------------


async def test_an_account_named_after_the_clarification_is_recorded(make_call, db_session):
    call = make_call("rakesh-account-named")
    await _describe(call)
    await call.turn("Yeah, I'm unable to log in.")

    await call.turn("eClinicalWorks.")

    values = (await _session(db_session, "rakesh-account-named")).collected["diagnostics"]["values"]
    assert values["account"] == "eClinicalWorks." and values["account_said"] == "eClinicalWorks."


async def test_a_clear_account_answer_is_taken_straight_away(make_call, db_session):
    call = make_call("rakesh-account-clear")
    await _describe(call)

    out = await call.turn("My Windows login.")

    assert ACCOUNT_Q.clarify not in _text(out)
    assert (await _session(db_session, "rakesh-account-clear")).collected["diagnostics"]["values"]["account"] == "My Windows login."


@pytest.mark.parametrize("said, names_nothing", [
    ("Yeah, I'm unable to log in.", True), ("I can't sign in to my account.", True), ("My password.", True),
    ("I'm not sure.", True), ("My Windows login.", False), ("Outlook.", False), ("My email password.", False),
    ("The computer.", False),
])
async def test_what_counts_as_naming_an_account(said, names_nothing):
    assert dx._names_nothing(ACCOUNT_Q, said) is names_nothing


# --- an extension said in the same breath as goodbye ------------------------------------------------------------------


async def test_an_extension_correction_with_goodbye_is_kept_on_the_ticket(make_call, client, db_session, sent_emails):
    """Before: the goodbye check came first, so the call ended and the corrected extension was lost."""
    call = make_call("rakesh-ext-bye")
    await _to_email_question(call)

    out = await call.turn("Sorry, my extension is 3006, thanks, bye.")

    assert out["lines"][0] == scripts.EXTENSION_NOTED.format(extension="3 0 0 6")
    assert out["ticket_id"] and out["expect_reply"] is False
    intake = (await _ticket(client, out["ticket_id"]))["description"].split("--- Call transcript ---")[0]
    assert "Extension: 3006 (given by the caller; unverified: not found in the directory)" in intake
    assert "Extension:<br>\n3006 (given by the caller" in sent_emails[-1]["body_html"]


async def test_an_extension_with_goodbye_at_the_extension_question_is_kept(make_call, client, db_session, sent_emails):
    call = make_call("rakesh-ext-q-bye")
    await _to_name_read_back(call)
    asked = await call.turn("Yes.")
    assert _text(asked) == scripts.EXTENSION_ASK

    out = await call.turn("3006, thanks, bye.")

    assert out["lines"][0] == scripts.EXTENSION_NOTED.format(extension="3 0 0 6") and out["ticket_id"]
    c = (await _session(db_session, "rakesh-ext-q-bye")).collected
    assert (c["extension"], c["extension_source"]) == ("3006", "caller")


async def test_a_long_extension_correction_ending_in_goodbye_ends_the_call_with_the_extension(make_call, client, db_session, sent_emails):
    call = make_call("rakesh-ext-long-bye")
    await _to_email_question(call)

    out = await call.turn("Oh sorry, I meant to say my extension is actually 3006, okay, thank you, bye bye")

    assert out["lines"][0] == scripts.EXTENSION_NOTED.format(extension="3 0 0 6")
    assert out["ticket_id"] and out["expect_reply"] is False
    assert (await _session(db_session, "rakesh-ext-long-bye")).collected["extension"] == "3006"


async def test_a_plain_goodbye_is_unchanged(make_call, db_session, sent_emails):
    call = make_call("rakesh-plain-bye")
    await _to_email_question(call)

    out = await call.turn("Thank you, bye.")

    assert scripts.EXTENSION_NOTED.split(",")[0] not in _text(out) and out["ticket_id"]
    assert (await _session(db_session, "rakesh-plain-bye")).collected["extension"] == "3000"  # as given earlier


# --- the extension in the helpdesk email ---------------------------------------------------------------------------


async def test_the_email_body_shows_the_extension_right_after_the_email():
    from app.notifications.email import _build_body

    body = _build_body("Rakesh", "+18455550311", "Password expired.", None, email="rakesh@hfmg.net",
                       extension="3006 (given by the caller; unverified: not found in the directory)",
                       with_extension=True, ticket_number="HFMG-2026-000001")

    assert body == (
        "Caller:\nRakesh\n\nCallback Number:\n+18455550311\n\nEmail:\nrakesh@hfmg.net\n\n"
        "Extension:\n3006 (given by the caller; unverified: not found in the directory)\n\n"
        "Issue:\nPassword expired.\n\nTicket Number:\nHFMG-2026-000001"
    )


@pytest.mark.parametrize("extension", [None, "", "  "])
async def test_a_missing_extension_reads_not_provided_like_the_email(extension):
    from app.notifications.email import _build_body

    body = _build_body("Rakesh", None, "Password expired.", None, extension=extension, with_extension=True)

    assert "Email:\nNot provided\n\nExtension:\nNot provided\n\nIssue:" in body


async def test_a_patient_request_email_has_no_extension_line():
    from app.notifications.email import _build_body

    assert "Extension" not in _build_body("Pat", None, "Reschedule my appointment.", None, with_extension=False)


@pytest.mark.parametrize("description, extension", [
    ("Printer jammed.\n\n--- Intake details ---\nDepartment: IT\nExtension: 3006 (given by the caller)\nStarted: today",
     "3006 (given by the caller)"),
    ("Printer jammed.\n\n--- Intake details ---\nExtension: none (the caller has no office extension)",
     "none (the caller has no office extension)"),
    ("Printer jammed.\n\n--- Intake details ---\nDepartment: IT", None),
    ("Printer jammed. My extension is 3006.", None),  # a web ticket: the caller's own words are never a field
    # Only the intake section: an "Extension:" the caller said in the transcript is not read.
    ("Printer jammed.\n\n--- Intake details ---\nDepartment: IT\n\n--- Call transcript ---\nCaller: Extension: 9999", None),
])
async def test_the_extension_is_read_only_from_the_intake_details(description, extension):
    from app.services.ticket_text import extract_intake_value

    assert extract_intake_value(description, "Extension") == extension

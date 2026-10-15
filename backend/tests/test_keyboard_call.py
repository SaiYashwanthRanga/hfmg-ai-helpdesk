"""Regression: the wired-keyboard test call that went down the printer questions.

What went wrong on the call, and what must hold now:
  1. a keyboard got the printer questions ("Which printer...", "a shared network printer?", "a paper jam?") and a
     print-queue next step -- a keyboard, mouse or headset now gets its own questions, whatever the category;
  2. "Why is the printer coming up, it's just the wired keyboard" / "You understood it wrong" were taken as answers --
     a correction now apologises once, says the corrected issue back, and resumes with the right questions;
  3. "Sorry, I didn't get you" was saved as the device and "Oh my God, you took the wrong..." as the error -- confusion
     is asked about again and never stored; a correction is never stored as an answer;
  4. "Hi, name" was read back as "N A M E" -- something that is not a name is never stored or read back, and "my name
     is ..." / "change my name" are name corrections wherever they come;
  5. extension 3000, listed in the directory for someone else, stays unverified and nothing else of that entry is used.

The exact transcript was not available: the caller's words are the patterns from the report. Simulated through the
real /start and /turn routes with the language model stubbed; not a live SIP call. Every person and number here is
synthetic.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, VoiceCallState
from app.main import app
from app.services.user_directory import UserDirectoryService
from app.voice import diagnostics as dx
from app.voice import nlu, scripts
from tests.test_directory_identity import DirCall
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550399"  # not in the directory
SYMPTOM_Q = next(q for q in dx.PERIPHERAL.questions if q.key == "symptom").prompt
PRINTER_Q = dx.PRINTER.questions[0].prompt
PRINTER_WORDS = ("printer", "paper jam", "print queue", "toner")
_REAL_YES_NO = nlu.interpret_yes_no


@pytest.fixture(autouse=True)
async def _production_flow(monkeypatch, db_session, tmp_path):
    for name, value in (("voice_ask_caller_type", True), ("voice_diagnostic_questions", True),
                        ("voice_ask_extension", True), ("voice_confirm_callback", True),
                        ("voice_confirm_summary", True), ("voice_confirm_name", True)):
        monkeypatch.setattr(settings, name, value)
    for name in ("Other", "Printer"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, [  # extension 3000 belongs to someone else
        row("Lee Other", phone="(845) 555-0302", ext="3000", email="lee.other@example.org", location="Other Clinic"),
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


async def _describe(call, said, *, description, category="Other", short_issue="your keyboard"):
    await call.start()
    await call.turn("I'm an employee.")
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value=description, confidence="high", unable_to_determine=False, category=category, priority=Priority.LOW,
        extras={"short_issue": short_issue, "started": "this morning", "work_blocked": False,
                "affected_scope": "one_person"},
    ))
    return await call.turn(said)


async def _into_printer_questions(call):
    """The call as it happened: the problem was read as a printer, so the printer questions began."""
    asked = await _describe(call, "My keyboard is not working, I want to replace it.",
                            description="The caller's printer is not working.", category="Printer",
                            short_issue="your printer")
    assert _text(asked).endswith(PRINTER_Q)
    return asked


# --- 1. a keyboard gets keyboard questions ---------------------------------------------------------------------------


async def test_a_keyboard_filed_under_printer_gets_keyboard_questions(make_call, db_session):
    call = make_call("kb-route")

    asked = await _describe(call, "My wired keyboard is not working properly.",
                            description="The caller's wired keyboard is not working properly.", category="Printer")

    assert _text(asked).endswith(SYMPTOM_Q)  # device and connection were in the description: not asked again
    assert not any(w in _text(asked).lower() for w in PRINTER_WORDS)
    values = (await _session(db_session, "kb-route")).collected["diagnostics"]["values"]
    assert (values["device"], values["connection"]) == ("keyboard", "wired")


# --- 2 and 3. the reported call, end to end ---------------------------------------------------------------------------


async def test_the_reported_call_end_to_end(make_call, client, db_session, monkeypatch, sent_emails):
    call = make_call("kb-call")
    heard = [_text(await _into_printer_questions(call))]

    async def say(utterance):
        out = await call.turn(utterance)
        heard.append(_text(out))
        return out

    again = await say("Sorry, I didn't get you.")  # 3. confusion: asked again, not saved as the device
    assert _text(again) == f"{dx.CLARIFY} {PRINTER_Q}"

    corrected = await say("Why the printer is coming up, it's just the wired keyboard.")  # 2.
    assert _text(corrected) == (
        "Sorry about that. I understand now that you're having a problem with your wired keyboard, not a printer. "
        f"{SYMPTOM_Q}"
    )
    session = await _session(db_session, "kb-call")
    assert session.collected["short_issue"] == "your wired keyboard" and session.collected["category"] == "Other"

    name_q = await say("Some keys aren't working, but I can still work.")
    assert "name" in _text(name_q).lower() or "speaking with" in _text(name_q).lower()

    call._say(interpret_name=nlu.TurnResult(value="Yashwant", confidence="high", unable_to_determine=False,
                                            extras={"department": "Development", "name_confidence": "low"}))
    read_back = await say("Yashwant, development.")
    assert scripts.spelled_name("Yashwant") in _text(read_back)

    call._say(interpret_name_correction=nlu.TurnResult(value="Name", unable_to_determine=False),  # the misreading
              interpret_yes_no=nlu.TurnResult(unable_to_determine=True))
    spell = await say("Hi, name.")  # 4.
    assert scripts.NAME_SPELL_FIRST in _text(spell) and "N A M E" not in _text(spell)
    monkeypatch.setattr(nlu, "interpret_yes_no", _REAL_YES_NO)  # that turn only
    extension_q = await say("Y A S H W A N T H")
    assert _text(extension_q).endswith(scripts.EXTENSION_ASK)

    await say("It's 3000.")  # 5.
    callback = await say("skip")
    assert "callback number" in _text(callback).lower()
    summary = await say("Yes.")
    assert "wired keyboard" in _text(summary) and "printer" not in _text(summary).lower()
    filed = await say("Yes.")
    assert filed["ticket_id"]

    c = (await _session(db_session, "kb-call")).collected
    assert c["caller_name"] == "Yashwanth"
    values = c["diagnostics"]["values"]
    assert (values["device"], values["connection"], values["symptom"]) == ("keyboard", "wired", "partial")
    assert "Sorry, I didn't get you" not in str(values)
    assert c["priority"] == "LOW"

    intake = (await _ticket(client, filed["ticket_id"]))["description"].split("--- Call transcript ---")[0]
    assert "Issue type: Keyboard / mouse / other device" in intake
    assert 'Issue corrected: the caller said the first questions (Printer / scanner) were wrong' in intake
    assert "Device: keyboard" in intake and "Connection: wired" in intake
    assert 'Symptom: partial (caller said: "Some keys aren\'t working, but I can still work.")' in intake
    assert "Suggested next step: Check the caller's keyboard (partly working" in intake
    for wrong in ("paper jam", "print queue", "Error shown", "shared network printer"):
        assert wrong not in intake
    assert "Department: Development (unverified: not on HFMG's department list)" in intake
    assert "Extension: 3000 (given by the caller; unverified: the directory lists this extension for a different entry)" in intake
    assert "Started: this morning" in intake and "Work blocked: no" in intake
    for theirs in ("Lee Other", "lee.other", "Other Clinic", "Identity:"):  # nothing of the other entry is used
        assert theirs not in intake


# --- 2. other corrections ----------------------------------------------------------------------------------------------


async def test_you_understood_it_wrong_asks_what_it_is_then_resumes(make_call, db_session):
    call = make_call("kb-wrong")
    await _into_printer_questions(call)

    asked = await call.turn("You understood it wrong.")
    assert _text(asked) == scripts.ISSUE_WHAT_IS_IT

    resumed = await call.turn("The wired keyboard.")

    assert _text(resumed) == (
        "Thanks. I understand now that you're having a problem with your wired keyboard, not a printer. "
        f"{SYMPTOM_Q}"
    )
    state = (await _session(db_session, "kb-wrong")).collected["diagnostics"]
    assert state["playbook"] == "peripheral" and not state.get("asking_issue")


@pytest.mark.parametrize("said", ["It's not a printer.", "Oh my God, you took the wrong thing."])
async def test_a_correction_is_never_saved_as_an_answer(make_call, db_session, said):
    call = make_call("kb-not-answer")
    await _into_printer_questions(call)

    out = await call.turn(said)

    assert _text(out) == scripts.ISSUE_WHAT_IS_IT
    values = (await _session(db_session, "kb-not-answer")).collected["diagnostics"]["values"]
    assert said not in str(values)


async def test_confusion_twice_leaves_the_question_unanswered_not_filled_in(make_call, db_session):
    call = make_call("kb-confused")
    await _describe(call, "My wired keyboard is not working.", description="The caller's wired keyboard is not working.")

    await call.turn("Sorry, I didn't get you.")
    out = await call.turn("What do you mean?")

    assert SYMPTOM_Q not in _text(out)  # moved on
    values = (await _session(db_session, "kb-confused")).collected["diagnostics"]["values"]
    assert "symptom" not in values and "symptom_said" not in values


async def test_corrections_are_bounded(make_call, db_session):
    call = make_call("kb-bounded")
    await _into_printer_questions(call)
    for _ in range(dx.MAX_ISSUE_CORRECTIONS):
        await call.turn("You understood it wrong.")
        await call.turn("Something else entirely.")  # answer to "what is it?": the Other questions

    out = await call.turn("You understood it wrong.")

    assert _text(out) != scripts.ISSUE_WHAT_IS_IT  # no more switching: the call carries on
    assert (await _session(db_session, "kb-bounded")).collected["diagnostics"]["corrections"] == dx.MAX_ISSUE_CORRECTIONS


@pytest.mark.parametrize("said, corrects", [
    ("Why the printer is coming up, it's just the wired keyboard", True), ("You understood it wrong", True),
    ("It's not a printer", True), ("I said keyboard", True), ("No, it's my keyboard, not a printer", True),
    ("It says something went wrong", False),  # an error message
    ("It is not printing at all", False),  # a printer symptom
    ("The one by the front desk", False), ("HP LaserJet in room 3", False),
])
async def test_what_counts_as_a_correction_to_the_issue(said, corrects):
    state = dx.begin("Printer", "The front desk printer is jammed.", None)
    assert (dx.correction(state, said) is not None) is corrects


@pytest.mark.parametrize("said, confused", [
    ("Sorry, I didn't get you", True), ("What do you mean?", True), ("Can you repeat that?", True),
    ("Which printer or device is it?", True), ("Pardon?", True),
    ("It shows an error, it didn't get the job", False), ("The one by the front desk", False), ("Sorry, it's FS01", False),
])
async def test_what_counts_as_not_following_the_question(said, confused):
    assert dx.is_confused(dx.PRINTER.questions[0], said) is confused


# --- 4. the name -----------------------------------------------------------------------------------------------------


async def _to_email(call):
    await _describe(call, "My wired keyboard is not working.", description="The caller's wired keyboard is not working.")
    await call.turn("Some keys aren't working.")
    call._say(interpret_name=nlu.TurnResult(value="Yashwanth", confidence="high", unable_to_determine=False,
                                            extras={"department": "IT", "name_confidence": "low"}))
    await call.turn("Yashwanth, IT.")
    await call.turn("Yes.")  # the read-back
    asked = await call.turn("No, I don't have one.")  # the extension
    assert "email" in _text(asked).lower()


async def test_my_name_is_at_a_later_question_is_read_back_then_the_question_resumes(make_call, db_session, monkeypatch):
    call = make_call("kb-name-later")
    await _to_email(call)
    monkeypatch.setattr(nlu, "interpret_email", _never("interpret_email"))

    out = await call.turn("Sorry, my name is Yashwant, not Yashwanth.")

    assert _text(out) == scripts.NAME_RECONFIRM.format(spelled=scripts.spelled_name("Yashwant"))
    resumed = await call.turn("Yes.")
    assert "email" in _text(resumed).lower()
    c = (await _session(db_session, "kb-name-later")).collected
    assert c["caller_name"] == "Yashwant" and c["name_confirmed_as"] == "Yashwant"


async def test_change_my_name_asks_for_the_spelling(make_call, db_session):
    call = make_call("kb-change-name")
    await _to_email(call)

    out = await call.turn("Can you change my name?")

    assert scripts.NAME_SPELL_FIRST in _text(out)
    await call.turn("Y A S H")
    assert (await _session(db_session, "kb-change-name")).collected["caller_name"] == "Yash"


async def test_saying_the_same_name_again_is_not_a_correction(make_call, db_session):
    call = make_call("kb-same-name")
    await _to_email(call)

    out = await call.turn("My name is Yashwanth and I'll skip the email.")

    assert scripts.NAME_SPELL_FIRST not in _text(out) and "So that's" not in _text(out)


async def test_a_name_that_is_not_a_name_is_never_stored(make_call, db_session):
    call = make_call("kb-not-a-name")
    await _describe(call, "My wired keyboard is not working.", description="The caller's wired keyboard is not working.")
    await call.turn("Some keys aren't working.")
    call._say(interpret_name=nlu.TurnResult(value="Name", confidence="high", unable_to_determine=False))

    out = await call.turn("Hi, name.")

    assert "N A M E" not in _text(out) and not (await _session(db_session, "kb-not-a-name")).collected.get("caller_name")
    assert _text(out) in scripts.NAME_RETRY  # the name is asked for again


@pytest.mark.parametrize("name, plausible", [
    ("Name", False), ("Hi Name", False), ("my name", False), ("Yes", False),
    ("Yashwanth", True), ("Rakesh Kumar", True), ("Natakarani", True), ("Will Smith", True),
])
async def test_what_counts_as_a_name(name, plausible):
    from app.voice import names

    assert names.plausible(name) is plausible


async def test_a_name_correction_during_the_email_read_back_never_keeps_the_unconfirmed_address(make_call, db_session):
    call = make_call("kb-name-at-confirm")
    await _to_email(call)
    confirm = await call.turn("Y A S H W A N T H")
    assert "Is that correct?" in _text(confirm)

    await call.turn("Wait, my name is Yashwant.")
    resumed = await call.turn("Yes.")  # the name read-back

    assert "email" in _text(resumed).lower()  # the address is asked for again
    c = (await _session(db_session, "kb-name-at-confirm")).collected
    assert c["caller_name"] == "Yashwant" and not c.get("email") and not c.get("email_confirmed")


# --- found replaying the call against the real model -------------------------------------------------------------------


async def test_i_can_still_work_said_later_outranks_a_reading_of_it_is_not_working(make_call, client, db_session):
    """The real model read "my printer ... it's not working" as "can't work"; the caller then said "I can still work",
    and the ticket was filed High because "you can't work"."""
    call = make_call("kb-can-work")
    await call.start()
    await call.turn("I'm an employee.")
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value="The caller's printer has not been working since this morning.", confidence="high",
        unable_to_determine=False, category="Printer", priority=Priority.HIGH,
        extras={"short_issue": "the printer", "started": "this morning", "work_blocked": True},  # the misreading
    ))
    await call.turn("I have a problem with my printer, it's not working since this morning.")
    await call.turn("Why the printer is coming up, it's just the wired keyboard.")

    await call.turn("Some keys aren't working, but I can still work.")

    c = (await _session(db_session, "kb-can-work")).collected
    assert c["work_blocked"] is False and c["priority"] != "HIGH"
    assert c["facts"]["work_blocked"]["source"] == "answer"  # the caller's own words, recorded as their answer
    assert c["diagnostics"]["values"]["device_said"] == "(from the caller's correction)"


@pytest.mark.parametrize("said, blocked", [
    ("Some keys aren't working, but I can still work.", False), ("This morning, and I can still work.", False),
    ("It's not stopping me.", False), ("I can't work at all.", True), ("I can't do my job.", True),
    ("I'm completely blocked.", True),
    ("It's not working since this morning.", None),  # the device, not the caller's work
    ("I can't work out why it does that.", None), ("I'm stuck at the login screen.", None),
    ("I can still work but I can't do my job properly.", None),  # both: not decided here
])
async def test_only_what_the_caller_says_about_their_own_work_counts(said, blocked):
    assert nlu.quick_work_impact(said) is blocked

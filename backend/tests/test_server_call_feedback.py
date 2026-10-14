"""Findings from a live server test call (the "server not connecting" call, run on the deployed backend).

  - who is affected came from the model's reading, not the caller: "your team can't work" was never said;
  - "Broken office." (a misheard answer) was recorded as the location;
  - a name spelled letter by letter was read back and asked about again, and the repeat got silence;
  - an unclear answer at the email / callback questions got the opening "what's going wrong?" question;
  - "The server name is 172.22.6.203." at the read-back repeated the whole summary.

Simulated through the real /start and /turn routes with the language model stubbed (not a live SIP call).
"""

import pytest

from app.core.config import get_settings
from app.db.models import Priority, VoiceCallState
from app.voice import diagnostics as dx
from app.voice import nlu, orchestrator, scripts
from tests.test_directory_identity import DirCall
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_transcript_server_access import CALLER, PROBLEM, _production_flow, _to_read_back  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
STRANGER = "+18455550399"
_REAL_INTERPRET_EMAIL = nlu.interpret_email


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):
    def make(call_id, number=STRANGER):
        return DirCall(client, monkeypatch, call_id, number)

    return make


async def _describe(call, problem, *, model_scope, work_blocked=None):
    await call.start()
    await call.turn("I'm an employee")
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value=problem, confidence="high", unable_to_determine=False, category="Other", priority=Priority.MEDIUM,
        extras={"short_issue": "the server", "started": "this morning", "work_blocked": work_blocked,
                "affected_scope": model_scope},
    ))
    return await call.turn(problem)


# --- 1. who is affected: only what the caller said -------------------------------------------------------------


async def test_several_people_is_not_recorded_when_the_caller_never_said_it(make_call, client, db_session):
    call = make_call("fb-scope")
    # The model reads "several people" into a sentence that says nothing about anyone else.
    first = await _describe(call, PROBLEM, model_scope="several_people", work_blocked=True)

    session = await _session(db_session, "fb-scope")
    assert session.collected.get("affected_scope") is None
    assert session.collected.get("priority_reason") != "your team can't work"
    assert "Is anyone else having the same problem?" not in _text(first)  # asked in turn, never assumed
    seen = [_text(first)]
    for said in ("FS01", "It times out.", "I'm in the office."):
        seen.append(_text(await call.turn(said)))
    assert any("anyone else" in s for s in seen)


async def test_several_people_is_kept_when_the_caller_says_so(make_call, db_session):
    call = make_call("fb-scope-said")

    await _describe(call, "The whole team can't reach the server 172.22.6.98 since this morning.",
                    model_scope="several_people", work_blocked=True)

    session = await _session(db_session, "fb-scope-said")
    assert session.collected["affected_scope"] == "several_people"
    assert session.collected["priority_reason"] == "your team can't work"


async def test_the_read_back_does_not_claim_the_team_cannot_work(client, monkeypatch, _call_fixture, db_session, sent_emails):
    call = DirCall(client, monkeypatch, "fb-readback-impact", CALLER)
    heard = await _to_read_back(call)

    read_back = heard[-1]
    assert "team" not in read_back.lower() and "several people" not in read_back.lower()


# --- 2. a misheard location is unclear, not a location -------------------------------------------------------------


async def test_a_misheard_location_is_asked_again_then_recorded_as_unclear(make_call, client, db_session):
    call = make_call("fb-location")
    await _describe(call, PROBLEM, model_scope=None, work_blocked=True)
    out = None
    for said in ("FS01", "It times out."):
        out = await call.turn(said)
    assert "office or working remotely" in _text(out)

    again = await call.turn("Broken office.")
    moved_on = await call.turn("Broken office.")

    assert _text(again) == f"{dx.CLARIFY} Are you in the office or working remotely?"
    assert "office or working remotely" not in _text(moved_on)
    values = (await _session(db_session, "fb-location")).collected["diagnostics"]["values"]
    assert values.get("location") is None and values["location_said"] == "Broken office."
    assert ("Location", 'unclear (caller said: "Broken office.")') in dx.ticket_lines(
        (await _session(db_session, "fb-location")).collected["diagnostics"])


@pytest.mark.parametrize("said, address", [
    ("The server name is 172.22.6.203.", ["172.22.6.203"]), ("It is 10.0.0.5, I think", ["10.0.0.5"]),
    ("at 192.168.1.1?", ["192.168.1.1"]), ("1.2.3.4.5", []), ("version 1.2.3", []),
])
async def test_an_address_at_the_end_of_a_sentence_is_still_read(said, address):
    assert dx.hostnames(said) == address


@pytest.mark.parametrize("said, location", [("I'm in the office.", "office"), ("Working from home today.", "remote"),
                                            ("Office.", "office"), ("Remote.", "remote")])
async def test_a_clear_location_is_taken_straight_away(said, location):
    question = next(q for q in dx.SERVER_ACCESS.questions if q.key == "location")
    assert dx._parse(question, said) == location


# --- 3. an unclear answer repeats the question that was asked ------------------------------------------------------


async def test_silence_at_the_email_question_repeats_the_email_question(make_call, db_session):
    call = make_call("fb-email-silence")
    await _describe(call, "My laptop won't turn on.", model_scope=None, work_blocked=True)
    for said in ("A laptop.", "No."):
        await call.turn(said)
    await call.name_and_department("Pat Real", "Billing")
    asked = await call.turn("No, I don't have one.")  # the extension
    assert "email" in _text(asked).lower()

    again = await call.turn("")

    assert "what's going wrong" not in _text(again).lower()
    assert _text(again).startswith(dx.CLARIFY) and "email" in _text(again).lower()


async def test_no_question_is_ever_answered_with_the_opening_question(make_call, db_session):
    """Whatever state an unclear answer lands in, the agent asks that state's question again."""
    from app.db.models import VoiceCallSession

    for state, asked in ((VoiceCallState.COLLECT_EMAIL, "What's your HFMG email? Just spell the part before the at sign, or say skip."),
                         (VoiceCallState.CONFIRM_EMAIL, "I have p r e a l at h f m g dot net. Is that correct?"),
                         (VoiceCallState.CONFIRM_CATEGORY, "Just to make sure I route this correctly, is this about Printer?")):
        session = VoiceCallSession(call_id=f"fb-{state.value}", from_number=STRANGER, to_number="+1", state=state,
                                   collected={}, turns=[{"role": "agent", "text": asked}], misunderstanding_count=0)
        out = await orchestrator._handle_failure(db_session, session)
        assert out.text == f"{dx.CLARIFY} {asked}", state


# --- 4. a server detail corrected at the read-back: that detail, then "is everything else correct?" ---------------


async def test_naming_the_server_at_the_read_back_updates_only_that(client, monkeypatch, _call_fixture, db_session, sent_emails):
    call = DirCall(client, monkeypatch, "fb-name-at-readback", CALLER)
    heard = await _to_read_back(call)  # 172.22.6.203 was given and the caller was not sure what it is

    out = await call.turn("The server name is 172.22.6.203.")

    assert _text(out) == f"{scripts.SUMMARY_SERVER_NOTED.format(server='172.22.6.203')} {scripts.SUMMARY_REST_CORRECT}"
    assert heard[-1] not in _text(out)  # the whole summary is not read again
    filed = await call.turn("Yes.")
    intake = (await _ticket(client, filed["ticket_id"]))["description"].split("--- Call transcript ---")[0]
    assert "Server: 172.22.6.98" in intake and "Server name: 172.22.6.203" in intake
    assert "Other address given" not in intake  # no longer an open question
    assert "Read-back: read to the caller" not in intake  # this time the caller did confirm


async def test_a_new_address_at_the_read_back_is_asked_about_then_only_the_rest_is_confirmed(
    client, monkeypatch, _call_fixture, db_session, sent_emails
):
    call = DirCall(client, monkeypatch, "fb-new-at-readback", CALLER)
    await _to_read_back(call)

    asked = await call.turn("Actually the server address is 172.22.6.50.")
    back = await call.turn("It's another server, also affected.")

    assert "Is 172.22.6.50 another server that's affected as well, or should it replace 172.22.6.98?" == _text(asked)
    assert _text(back) == f"{scripts.LATE_SERVER_ADDED} {scripts.SUMMARY_REST_CORRECT}"
    values = (await _session(db_session, "fb-new-at-readback")).collected["diagnostics"]["values"]
    assert values["server"] == "172.22.6.98" and "172.22.6.50" in values["additional_servers"]


async def test_read_back_corrections_stay_bounded(client, monkeypatch, _call_fixture, db_session, sent_emails):
    call = DirCall(client, monkeypatch, "fb-bounded", CALLER)
    await _to_read_back(call)

    for _ in range(orchestrator.MAX_SUMMARY_CORRECTIONS + 1):
        out = await call.turn("The server name is 172.22.6.203.")
        if out["ticket_id"]:
            break

    assert out["ticket_id"] is not None  # filed, not looping

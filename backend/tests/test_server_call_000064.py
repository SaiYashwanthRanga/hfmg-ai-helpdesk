"""Regression: call-test HFMG-2026-000064, a server problem with only an IP address known.

What went wrong, and what must hold now:
  1/2. the server name was asked again, with the literal "{server}" in the question -- a re-ask now names the address,
       an unfilled template field never reaches speech, and "Don't know" ends that question;
  3.   a drive/share question with nothing to say a share was involved -- what the server hosts is asked first, and
       only a file share gets share questions (a database gets database ones; unknown stays unknown);
  4.   "Adjust the timeout" was not clarified -- the agent asks whether it times out or a setting needs changing;
  5.   "access to Don't know" in the next step -- an unknown answer never becomes a resource name;
  6.   the department read as checked -- it is shown as the caller's word; extension 3000 stays unverified;
  7.   "Okay" at the callback question repeated the whole number -- a short yes/no question, and only "Yes" confirms;
  8.   filed under Other -- a server-access issue goes under the existing Network category.

The exact transcript was not available: the caller's words are the patterns from the findings. Real /start and
/turn routes, language model stubbed; not a live Nextiva/SIP call. Synthetic callers and numbers only.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, VoiceCallSession, VoiceCallState
from app.main import app
from app.services.user_directory import UserDirectoryService
from app.voice import diagnostics as dx
from app.voice import nlu, orchestrator, reply, scripts
from tests.test_directory_identity import DirCall
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550399"  # not in the directory
IP = "172.22.6.98"
Q = {q.key: q.prompt for q in dx.SERVER_ACCESS.questions}


@pytest.fixture(autouse=True)
async def _production_flow(monkeypatch, db_session, tmp_path):
    for name, value in (("voice_ask_caller_type", True), ("voice_diagnostic_questions", True),
                        ("voice_ask_extension", True), ("voice_confirm_callback", True),
                        ("voice_confirm_summary", True)):
        monkeypatch.setattr(settings, name, value)
    for name in ("Other", "Network"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, [row("Lee Other", phone="(845) 555-0302", ext="3000")]))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):  # noqa: F811
    def make(call_id):
        return DirCall(client, monkeypatch, call_id, CALLER)

    return make


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


async def _describe(call, said=f"I'm having a problem with my server, {IP}."):
    await call.start()
    await call.turn("I'm an employee.")
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value=f"The caller is having a problem with the server {IP}.", confidence="high", unable_to_determine=False,
        category="Other", priority=Priority.MEDIUM,
        extras={"short_issue": "the server", "started": "this morning", "work_blocked": False},
    ))
    return await call.turn(said)


# --- the reported call, end to end -------------------------------------------------------------------------------------


async def test_the_reported_call_end_to_end(make_call, client, db_session, sent_emails):
    call = make_call("c64")
    heard = [_text(await _describe(call))]

    async def say(utterance):
        out = await call.turn(utterance)
        heard.append(_text(out))
        return out

    assert heard[0].endswith(f"Do you know the server's name? I have the address {IP}.")
    again = await say("Hmm?")  # 1/2. the re-ask names the address, never "{server}"
    assert _text(again) == f"{dx.CLARIFY} Do you know the server's name? I have the address {IP}."
    hosts_q = await say("Don't know.")  # ends the name question
    assert _text(hosts_q) == Q["resource_type"]  # 3. what it hosts, before anything about drives or shares
    error_q = await say("I don't know.")
    assert _text(error_q) == Q["error"]
    timeout_q = await say("Adjust the timeout.")  # 4.
    assert _text(timeout_q) == Q["timeout_kind"]
    await say("The connection is timing out.")
    await call.last_name("Real")  # nobody in the directory has it: the usual name question follows
    call._say(interpret_name=nlu.TurnResult(value="Pat Real", confidence="high", unable_to_determine=False,
                                            extras={"department": "Development", "name_confidence": "high"}))
    await say("Pat Real, development.")
    await say("3000")  # the extension: the directory lists it for someone else
    callback = await say("skip")  # email
    assert "callback number" in _text(callback).lower()

    okay = await say("Okay.")  # 7. not a confirmation
    assert _text(okay) == scripts.CALLBACK_YES_NO and "8 4 5" not in _text(okay)
    assert not (await _session(db_session, "c64")).collected.get("callback_confirmed")
    summary = await say("Yes.")
    assert (await _session(db_session, "c64")).collected["callback_confirmed"] is True
    assert "Don't know" not in _text(summary)
    filed = await say("Yes.")
    assert filed["ticket_id"]

    for line in heard:  # nothing unfilled, and no question about a share, a drive or a database was asked
        assert "{" not in line and "}" not in line
        assert "shared drive or folder are you trying" not in line and "connects to the database" not in line
    assert sum("server's name" in line for line in heard) == 2  # the question and its one re-ask, never again

    c = (await _session(db_session, "c64")).collected
    values = c["diagnostics"]["values"]
    assert (values["server"], values["server_name"], values["resource_type"]) == (IP, "unknown", "unknown")
    assert values["timeout_kind"] == "connection_timeout" and c["category"] == "Network"  # 8.
    assert c["priority"] == "MEDIUM" and c["work_blocked"] is False

    ticket = await _ticket(client, filed["ticket_id"])
    intake = ticket["description"].split("--- Call transcript ---")[0]
    for expected in (
        f"Server: {IP}",
        'Server name: unknown (caller said: "Don\'t know.")',
        'What the server hosts: unknown (caller said: "I don\'t know.")',
        "What happens: Adjust the timeout.",  # the caller's own words, kept as said
        'Timeout: connection_timeout (caller said: "The connection is timing out.")',
        "Department: Development (given by the caller; unverified: not on HFMG's department list)",
        "Extension: 3000 (given by the caller; unverified: the directory lists this extension for a different entry)",
        "Started: this morning", "Work blocked: no",
        "establish which resource or service on it the caller needs",
        "Nothing was changed on the call.",
    ):
        assert expected in intake, expected
    next_step = intake.split("Suggested next step:")[1]
    for invented in ("Don't know", "database", "share", "drive", "access to"):  # 5. nothing unknown made concrete
        assert invented.lower() not in next_step.lower(), invented


# --- what the server hosts decides the questions -----------------------------------------------------------------------


def _questions(state, answers):
    """The questions asked, answering each in turn."""
    asked = []
    for said in answers:
        found = dx.next_prompt(state)
        if found is None:
            break
        state["pending"] = found[0]
        asked.append(found[0])
        state, _, reask = dx.record(state, said)
        assert reask is None, (found, said)
    return asked, state


async def test_a_remote_database_gets_database_questions_and_no_share_questions():
    state = dx.begin("Other", f"I can't connect to the remote database on {IP}.", None)

    asked, state = _questions(state, ["FS-DB01", "The billing system.", "It times out.", "I'm in the office."])

    assert state["values"]["resource_type"] == "database"
    assert "db_client" in asked and "resource" not in asked


async def test_a_file_share_still_gets_the_share_question():
    state = dx.begin("Other", f"The server {IP} is not connecting.", None)

    asked, state = _questions(state, ["I don't know.", "A shared drive.", "The finance share.", "It times out."])

    assert asked[:3] == ["server_name", "resource_type", "resource"]
    assert state["values"]["resource"] == "The finance share."


async def test_a_share_named_in_the_problem_is_not_asked_about_again():
    state = dx.begin("Other", f"The server {IP} is not connecting and I can't get to the DC drive.", None)
    assert (state["values"]["resource_type"], state["values"]["resource"]) == ("file_share", "the DC drive")


async def test_not_connecting_is_never_taken_for_a_timeout():
    state = dx.begin("Other", f"The server {IP} is not connecting.", None)
    assert "timeout_kind" not in state["values"] and not state["values"].get("timeout_ambiguous")


@pytest.mark.parametrize("said, kind", [
    ("The connection is timing out.", "connection_timeout"),
    ("I need the timeout setting increased.", "setting_change"),
    ("Change the setting, it's too short.", "setting_change"),
])
async def test_the_timeout_answer_is_kept_apart(said, kind):
    question = next(q for q in dx.SERVER_ACCESS.questions if q.key == "timeout_kind")
    assert dx._parse(question, said) == kind


@pytest.mark.parametrize("said, ambiguous", [
    ("Adjust the timeout.", True), ("Can you increase the time out?", True), ("The timeout setting is wrong.", True),
    ("It just times out.", False), ("It says it can't connect.", False),
])
async def test_what_needs_the_timeout_question(said, ambiguous):
    assert bool(dx._TIMEOUT_AMBIGUOUS.search(said)) is ambiguous


@pytest.mark.parametrize("said", ["Don't know.", "I don't know", "Not sure.", "No idea", "Um, I really don't know.",
                                  "dunno", "I can't remember"])
async def test_an_unknown_answer_is_recorded_as_unknown_and_never_as_a_name(said):
    question = next(q for q in dx.SERVER_ACCESS.questions if q.key == "server_name")
    assert dx._parse(question, said) == "unknown"
    assert dx._said({"resource": "unknown"}, "resource", "the resource they need") == "the resource they need"


async def test_no_error_is_still_none_not_unknown():
    question = next(q for q in dx.OTHER.questions if q.key == "error")
    assert dx._parse(question, "No error.") == "none"


# --- an unfilled template field is never spoken --------------------------------------------------------------------------


async def test_an_unfilled_placeholder_is_removed_before_it_is_spoken(db_session):
    session = VoiceCallSession(call_id="c64-guard", from_number=CALLER, to_number="+1",
                               state=VoiceCallState.COLLECT_DIAGNOSTICS, collected={}, turns=[])

    out = orchestrator._speak(session, reply.ask("Do you know the server's name? I have the address {server}."))

    assert out.lines == ["Do you know the server's name? I have the address."]
    assert "{" not in session.turns[-1]["text"]


async def test_every_reask_names_what_it_asks_about():
    state = dx.begin("Other", f"The server {IP} is not connecting.", None)
    key, prompt = dx.next_prompt(state)
    state["pending"] = key

    _, _, reask = dx.record(state, "Hmm?")

    assert "{" not in prompt and "{" not in reask and IP in reask


# --- the callback number ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("said", ["Okay.", "OK", "Alright.", "Hmm"])
async def test_an_acknowledgement_never_confirms_the_callback_number(said):
    assert orchestrator._ACKNOWLEDGEMENT_ONLY.match(said)
    assert not orchestrator._ACKNOWLEDGEMENT_ONLY.match("Yes, okay.")

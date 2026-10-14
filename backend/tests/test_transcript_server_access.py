"""Regression: the reported "server not connecting" call, turn by turn.

What went wrong on the call, and what must hold now:
  1. the caller's first "answer" was the agent's own greeting coming back -- not used as an answer;
  2. the server was reported as 172.22.6.98, then 172.22.6.203 was given for its name -- neither address is
     silently replaced: the caller is asked which it is, and both stay on the ticket until that is clear;
  3. "the server is not connecting ... the DC drive" -- no disk-space questions, no failing application, no
     "extend the volume" next step: nothing the caller did not say;
  4. the priority comes from what the caller confirmed (they cannot work), not from an invented failure;
  5. extension 3000 (someone else's in the directory) is kept as said and marked unverified;
  6. the final read-back got no answer -- the ticket is filed as read back, NOT confirmed;
  7. the one AI summary is written from those details only; one ticket, one email.

This is a simulated call through the real /start, /turn and /status routes with the language model stubbed:
it is not a live Nextiva/SIP call and no email is delivered (the mail API is mocked).
"""

import pytest
from sqlalchemy import func, select

from app.ai import summarizer
from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket, VoiceCallState
from app.main import app
from app.services.user_directory import UserDirectoryService
from app.voice import diagnostics as dx
from app.voice import nlu, orchestrator, scripts
from app.voice import priority as priority_rules
from tests.test_directory_identity import AUTH, SIP, DirCall
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_user_directory import row, write_csv

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
CALLER = "+18455550301"  # the directory lists "Dana Sample", extension 3001, for this number
# ...and extension 3000 belongs to someone else in the directory.
PROBLEM = "The server 172.22.6.98 is not connecting and I can't get to the DC drive."
_REAL_INTERPRET_EMAIL = nlu.interpret_email
INVENTED = ("disk space", "free space", "space needed", "extend the volume", "application failing",
            "an application is already failing", "escalation")


@pytest.fixture(autouse=True)
async def _production_flow(monkeypatch, db_session, tmp_path):
    for name, value in (("voice_ask_caller_type", True), ("voice_diagnostic_questions", True), ("voice_ask_extension", True),
                        ("voice_confirm_callback", True), ("voice_confirm_summary", True)):
        monkeypatch.setattr(settings, name, value)
    for name in ("Other", "Network"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()
    service = UserDirectoryService()
    await service.load(write_csv(tmp_path, [
        row("Dana Sample", phone="(845) 555-0301", ext="3001", email="dana.sample@example.org"),
        row("Lee Other", phone="(845) 555-0302", ext="3000", email="lee.other@example.org"),
    ]))
    monkeypatch.setattr(app.state, "user_directory", service, raising=False)


@pytest.fixture
def summaries(monkeypatch):
    prompts = []

    class Provider:
        is_configured = True

        async def structured(self, **kwargs):
            prompts.append(kwargs["user"])
            return {"summary": "Caller cannot connect to server 172.22.6.98 (another address, 172.22.6.203, was also "
                               "given) or reach the DC drive; cannot work."}

    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: Provider())
    return prompts


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


async def _to_read_back(call, *, second_address_answer="I'm not sure, I just know both numbers.",
                        identity_answers=("No, that's not me.",)):
    """Play the reported call up to the final read-back. Returns every agent reply."""
    heard = []

    async def say(utterance):
        out = await call.turn(utterance)
        heard.append(_text(out))
        return out

    await call.start()
    await say("Thank you for calling the helpdesk. May I know if you are a patient or an employee?")  # 1. echo
    await say("I'm an employee.")
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value=PROBLEM, confidence="high", unable_to_determine=False, category="Other", priority=Priority.MEDIUM,
        extras={"short_issue": "the server"},
    ))
    await say(PROBLEM)  # 2. -> "when did this start, and can you work?"
    call._say(interpret_details=nlu.TurnResult(
        value="this morning", unable_to_determine=False,
        extras={"started": "this morning", "work_blocked": True, "affected_scope": "one_person"},
    ))
    await say("Since this morning, and I can't do my work.")
    await say("172.22.6.203")  # asked for the server's name: a second address
    await say(second_address_answer)
    await say("It just says it can't connect.")
    await say("I'm in the office.")
    await say("No, I don't think so.")  # anyone else?
    for answer in identity_answers:  # "Am I speaking with Dana Sample?"
        await say(answer)
    call._say(interpret_name=nlu.TurnResult(value="Pat Real", confidence="high", unable_to_determine=False,
                                            extras={"department": "Billing"}))
    await say("Pat Real, billing.")
    await say("3000")  # 5. the extension
    call.monkeypatch.setattr(nlu, "interpret_email", _REAL_INTERPRET_EMAIL)
    await say("skip")  # email
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=True, unable_to_determine=False))
    await say("Yes.")  # callback number -> the read-back
    return heard


async def test_the_reported_call_is_recorded_from_what_the_caller_actually_said(
    client, monkeypatch, _call_fixture, db_session, sent_emails, summaries
):
    call = DirCall(client, monkeypatch, "srv-call", CALLER)
    heard = await _to_read_back(call)
    assert (await _session(db_session, "srv-call")).state == VoiceCallState.CONFIRM_SUMMARY

    filed = await call.turn("")  # 6. no answer to the read-back

    # 1. The echoed greeting was never used as an answer.
    assert heard[0] == scripts.IDENTIFY_CLARIFY
    session = await _session(db_session, "srv-call")
    caller_lines = [t["text"] for t in session.turns if t["role"] == "caller"]
    assert caller_lines[0].startswith(orchestrator.NOT_CALLER_SPEECH)

    # 2. The server's name was asked for knowing the address, and the second address was asked about.
    assert heard[3] == "Do you know the server's name? I have the address 172.22.6.98."
    assert heard[4] == ("Is 172.22.6.203 another server that's affected, a correction to 172.22.6.98, "
                        "or another address for the same server?")
    # 3. No storage or application questions were asked.
    asked = " ".join(heard).lower()
    assert "free space" not in asked and "application" not in asked and "disk" not in asked
    assert "Your ticket number is" in _text(filed)

    ticket = await _ticket(client, filed["ticket_id"])
    intake = ticket["description"].split("--- Call transcript ---")[0]
    for expected in ("Issue type: Server / system access",
                     "Server: 172.22.6.98 (from the problem description)",
                     "Other address given: 172.22.6.203 (not established whether it is another server, a correction, or the same server)",
                     "Drive, share or system: the DC drive (from the problem description)",
                     "What happens: It just says it can't connect.",
                     "Started: this morning", "Work blocked: yes",
                     "Extension: 3000 (given by the caller; unverified: the directory lists this extension for a different entry)",
                     "Read-back: read to the caller, no answer: not confirmed",
                     "Suggested next step: Check whether 172.22.6.98 can be reached"):
        assert expected in intake, expected
    for invented in INVENTED:
        assert invented not in intake.lower(), invented
    assert "help desk" not in intake.lower()

    # 4. Priority: the configured rule applied to the confirmed facts only.
    expected = priority_rules.assess(model_priority=Priority.MEDIUM, work_blocked=True, scope="one_person")
    assert ticket["priority"] == expected.priority.value == "HIGH"
    assert session.collected["priority_reason"] == "you can't work"
    assert session.collected.get("summary_confirmed") is False

    # 7. One summary, from those details only; one ticket; one email.
    assert len(summaries) == 1
    for fact in ("172.22.6.98", "172.22.6.203", "the DC drive"):
        assert fact in summaries[0], fact
    for invented in INVENTED:
        assert invented not in summaries[0].lower(), invented
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar_one() == 1
    assert len(sent_emails) == 1


async def test_an_extension_that_differs_from_the_callers_own_directory_entry_is_marked_unverified(db_session):
    """A safeguard: a declined or unclear identity forgets the caller-ID entry, and a confirmed one with an
    extension is never asked -- but if a confirmed caller's own entry ever disagrees, the caller's answer is kept
    and the disagreement is written down, never resolved silently either way."""
    from app.db.models import VoiceCallSession

    session = VoiceCallSession(
        call_id="srv-ext", from_number=CALLER, to_number="+1", state=VoiceCallState.COLLECT_EXTENSION, turns=[],
        collected={"caller_type": "INTERNAL_IT", "identity_confirmed": True, "directory_identity": {"extension": "3001"},
                   "caller_name": "Dana Sample", "department": "Billing", "description": PROBLEM, "started": "today",
                   "work_blocked": True, "phone_number": CALLER},
    )

    await orchestrator._extension_settled(db_session, session, "3000", "caller", None)

    assert session.collected["extension"] == "3000"  # as said, never replaced by the directory's 3001
    assert session.collected["extension_note"] == "unverified: the directory entry for this caller ID lists extension 3001"
    expected = "Extension: 3000 (given by the caller; unverified: the directory entry for this caller ID lists extension 3001)"
    assert expected in orchestrator._intake_details(session.collected)  # the caller's answer, never hidden
    session.collected["identity_confirmed"] = False
    assert expected in orchestrator._intake_details(session.collected)


async def test_hanging_up_at_the_read_back_also_leaves_it_unconfirmed(
    client, monkeypatch, _call_fixture, db_session, sent_emails
):
    call = DirCall(client, monkeypatch, "srv-hangup", CALLER)
    await _to_read_back(call)

    await client.post(f"{SIP}/status", json={"call_id": "srv-hangup", "reason": "hangup"}, headers=AUTH)

    db_session.expire_all()
    ticket = (await db_session.execute(select(Ticket))).scalars().one()
    assert "Read-back: read to the caller, no answer: not confirmed" in ticket.description
    assert "Other address given: 172.22.6.203" in ticket.description and "Server: 172.22.6.98" in ticket.description
    assert len(sent_emails) == 1


@pytest.mark.parametrize("answer, server, expected_line", [
    ("It's the same server, that's its other address.", "172.22.6.98",
     "Also known as: 172.22.6.203 (the caller said it is the same server)"),
    ("Sorry, that was wrong, the right one is 203.", "172.22.6.203",
     'Server (corrected): the caller corrected it from "(from the problem description)"'),
    ("It's another server, that one is down too.", "172.22.6.98", "Additional servers: 172.22.6.203"),
])
async def test_each_clarification_of_the_second_address_is_recorded_without_losing_the_first(
    answer, server, expected_line, client, monkeypatch, _call_fixture, db_session, sent_emails
):
    call = DirCall(client, monkeypatch, f"srv-{len(answer)}", CALLER)
    await _to_read_back(call, second_address_answer=answer)

    filed = await call.turn("Yes, that's right.")

    intake = (await _ticket(client, filed["ticket_id"]))["description"].split("--- Call transcript ---")[0]
    assert f"Server: {server}" in intake and expected_line in intake
    assert "172.22.6.98" in intake and "172.22.6.203" in intake  # neither address is ever lost


async def test_a_name_for_a_known_address_is_its_name_not_a_conflict():
    state = dx.begin("Other", PROBLEM, None)
    state["pending"] = "server_name"

    state, _, reask = dx.record(state, "It's FS01.")

    assert reask is None
    assert (state["values"]["server"], state["values"]["server_name"]) == ("172.22.6.98", "FS01")


@pytest.mark.parametrize("problem, playbook", [
    ("The server 172.22.6.98 is not connecting and I can't get to the DC drive.", "server_access"),
    ("I can't reach 10.1.2.3 from my desk.", "server_access"),
    ("The file server is running out of disk space.", "server_storage"),
    ("The file server is full and the billing app is failing to save.", "server_storage"),
])
async def test_a_server_mentioned_is_not_a_storage_problem_unless_the_caller_says_so(problem, playbook):
    assert dx.choose("Other", problem).name == playbook


async def test_a_server_that_is_not_connecting_is_never_given_a_failing_application_or_volume_advice():
    state = dx.begin("Other", PROBLEM, None)

    assert dx.urgent_reason(state) is None and dx.priority_floor(state) is None
    next_step = dict(dx.ticket_lines(state))["Suggested next step"].lower()
    assert "extend" not in next_step and "free" not in next_step and "space" not in next_step

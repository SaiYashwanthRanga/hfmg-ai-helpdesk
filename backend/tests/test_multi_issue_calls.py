"""One call, several issues: each issue gets its own ticket, summary and email.

After a ticket is filed the agent asks "anything else?". A "yes" starts the next
issue, which must not be mistaken for a replay of the turn that filed the last one.
"""

import pytest
from sqlalchemy import func, select

from app.ai import summarizer
from app.core.config import get_settings
from app.db.base import async_session_factory
from app.db.models import EscalationReason, Priority, Ticket, VoiceCallSession, VoiceCallState
from app.voice import nlu, orchestrator, scripts
from app.voice.session import get_or_create_session
from tests.test_ticket_details import _seed_categories
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_voice_simulator import _full_intake, _say, _simulator_on, categories  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
SIP = "/api/v1/voice/sip"
AUTH = {"Authorization": "Bearer multi-token"}
ISSUES = {
    "A": "My Bluetooth is not working.",
    "B": "The front desk printer is jammed.",
    "C": "My password expired.",
}


class PerIssueProvider:
    """A summarizer LLM that answers about whichever issue it was given, and counts requests."""

    is_configured = True

    def __init__(self):
        self.calls = []

    async def structured(self, **kwargs):
        self.calls.append(kwargs["user"])
        text = kwargs["user"]
        key = next(k for k, issue in ISSUES.items() if issue in text)
        return {"summary": f"Summary of issue {key}."}


async def _async(value):
    return value


class Call:
    """A SIP call driven turn by turn, with the language model stubbed."""

    def __init__(self, client, monkeypatch, call_id):
        self.client, self.monkeypatch, self.call_id = client, monkeypatch, call_id

    async def turn(self, utterance):
        response = await self.client.post(
            f"{SIP}/turn", json={"call_id": self.call_id, "utterance": utterance}, headers=AUTH
        )
        return response.json()

    def _say(self, **stubs):
        for name, value in stubs.items():
            self.monkeypatch.setattr(nlu, name, (lambda *a, **k: _async(value)))

    async def start(self):
        await self.client.post(
            f"{SIP}/start", json={"call_id": self.call_id, "from_number": "+18455550142"}, headers=AUTH
        )

    async def describe(self, key):
        """Report an issue. Start time and work status are volunteered, so no details question."""
        result = nlu.TurnResult(
            value=ISSUES[key], confidence="high", unable_to_determine=False, category="Other",
            priority=Priority.MEDIUM,
            extras={"short_issue": "your computer", "started": "today", "work_blocked": True},
        )
        self._say(interpret_description=result)
        return await self.turn(ISSUES[key])

    async def first_issue_to_ticket(self, key="A"):
        """Issue, then name and email, then the email confirmation files the ticket."""
        await self.start()
        await self.describe(key)
        self._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False))
        await self.turn("Maria Lopez")
        self._say(interpret_email=nlu.TurnResult(value="m@hfmg.net", confidence="high", unable_to_determine=False))
        await self.turn("m at hfmg dot net")
        outcome = await self.answer(True)  # the email confirmation
        if not outcome["ticket_id"]:  # with the read-back on, the caller confirms that too
            outcome = await self.answer(True)
        return outcome

    async def answer(self, yes):
        self._say(interpret_yes_no=nlu.TurnResult(yes_no=yes, unable_to_determine=False))
        return await self.turn("yes" if yes else "no")

    async def another_issue(self, key):
        """"Yes, anything else?" then the next issue; the caller's details are already known."""
        await self.answer(True)
        outcome = await self.describe(key)
        if not outcome["ticket_id"] and not settings.voice_confirm_summary:
            raise AssertionError("expected the issue to be filed straight away")
        return outcome


@pytest.fixture
def summaries(monkeypatch):
    provider = PerIssueProvider()
    monkeypatch.setattr(settings, "enable_ai_summary", True)
    monkeypatch.setattr(summarizer, "get_provider", lambda: provider)
    return provider


@pytest.fixture
async def call(client, monkeypatch, sent_emails):  # noqa: F811
    monkeypatch.setattr(settings, "voice_sip_gateway_token", "multi-token")
    async with async_session_factory() as db:
        await _seed_categories(db)
    return Call(client, monkeypatch, "multi-1")


async def _ticket(client, ticket_id) -> dict:
    return (await client.get(f"/api/v1/tickets/{ticket_id}")).json()


async def _session(db_session, call_id) -> VoiceCallSession:
    row = (await db_session.execute(select(VoiceCallSession).where(VoiceCallSession.call_id == call_id))).scalar_one()
    await db_session.refresh(row)
    return row


# --- one, two and three issues -------------------------------------------------------


async def test_one_issue_makes_one_ticket_and_no_closes_the_call(call, client, db_session, summaries, sent_emails):
    outcome = await call.first_issue_to_ticket("A")
    ticket = await _ticket(client, outcome["ticket_id"])

    goodbye = await call.answer(False)

    assert goodbye["expect_reply"] is False and goodbye["ticket_id"] is None
    assert (await _session(db_session, "multi-1")).state == VoiceCallState.COMPLETED
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar() == 1
    assert len(summaries.calls) == 1 and len(sent_emails) == 1
    assert ticket["ai_summary"] == "Summary of issue A."


async def test_two_issues_make_two_independent_tickets(call, client, db_session, summaries, sent_emails):
    first = await call.first_issue_to_ticket("A")
    ticket_a = await _ticket(client, first["ticket_id"])

    second = await call.another_issue("B")

    assert second["ticket_id"] and second["ticket_id"] != first["ticket_id"]
    ticket_b = await _ticket(client, second["ticket_id"])
    assert ticket_a["ticket_number"] != ticket_b["ticket_number"]
    # The caller is read B's number, not A's again.
    assert scripts.spoken_ticket_number(ticket_b["ticket_number"]) in " ".join(second["lines"])
    assert scripts.spoken_ticket_number(ticket_a["ticket_number"]) not in " ".join(second["lines"])

    # B is its own ticket about its own issue...
    assert ISSUES["B"] in ticket_b["description"] and ISSUES["A"] not in ticket_b["description"].split("--- Call transcript ---")[0]
    assert ticket_b["caller_name"] == "Maria Lopez" and ticket_b["email"] == "m@hfmg.net"
    # ...and A is exactly as it was.
    assert await _ticket(client, first["ticket_id"]) == ticket_a

    # Independent summaries and emails: one request and one email per ticket, each about its own issue.
    assert len(summaries.calls) == 2 and len(sent_emails) == 2
    assert ticket_a["ai_summary"] == "Summary of issue A." and ticket_b["ai_summary"] == "Summary of issue B."
    assert "Summary of issue A." in sent_emails[0]["body_html"] and "Summary of issue B." in sent_emails[1]["body_html"]
    assert ISSUES["B"].replace("'", "&#x27;") in sent_emails[1]["body_html"]

    row = await _session(db_session, "multi-1")
    assert str(row.ticket_id) == second["ticket_id"]  # the call points at the latest ticket
    assert row.collected["ticket_ids"] == [first["ticket_id"], second["ticket_id"]]  # and still knows A


async def test_three_issues_make_three_tickets(call, client, db_session, summaries, sent_emails):
    first = await call.first_issue_to_ticket("A")
    second = await call.another_issue("B")
    third = await call.another_issue("C")

    ids = [first["ticket_id"], second["ticket_id"], third["ticket_id"]]
    assert len(set(ids)) == 3
    tickets = [await _ticket(client, i) for i in ids]
    assert len({t["ticket_number"] for t in tickets}) == 3
    assert [t["ai_summary"] for t in tickets] == [f"Summary of issue {k}." for k in "ABC"]
    assert len(summaries.calls) == 3 and len(sent_emails) == 3
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar() == 3

    assert (await call.answer(False))["expect_reply"] is False  # "no" ends the call
    assert (await _session(db_session, "multi-1")).state == VoiceCallState.COMPLETED


# --- leaving while a later issue is still being taken ---------------------------------


async def test_hanging_up_during_the_second_issue_still_files_it(call, client, db_session, summaries, sent_emails, monkeypatch):
    monkeypatch.setattr(settings, "voice_confirm_summary", True)  # B waits at the read-back
    first = await call.first_issue_to_ticket("A")
    await call.another_issue("B")
    assert (await _session(db_session, "multi-1")).state == VoiceCallState.CONFIRM_SUMMARY

    await client.post(f"{SIP}/status", json={"call_id": "multi-1", "reason": "hangup"}, headers=AUTH)

    row = await _session(db_session, "multi-1")
    assert len(row.collected["ticket_ids"]) == 2 and str(row.ticket_id) != first["ticket_id"]
    salvaged = await _ticket(client, str(row.ticket_id))
    assert "INCOMPLETE VOICE INTAKE" in salvaged["description"] and ISSUES["B"] in salvaged["description"]
    assert len(summaries.calls) == 2 and len(sent_emails) == 2


async def test_saying_bye_during_the_second_issue_still_files_it(call, client, db_session, summaries, sent_emails, monkeypatch):
    monkeypatch.setattr(settings, "voice_confirm_summary", True)
    await call.first_issue_to_ticket("A")
    await call.another_issue("B")

    goodbye = await call.turn("thanks, bye")

    assert goodbye["ticket_id"] and goodbye["expect_reply"] is False
    row = await _session(db_session, "multi-1")
    assert len(row.collected["ticket_ids"]) == 2
    assert ISSUES["B"] in (await _ticket(client, goodbye["ticket_id"]))["description"]


async def test_a_callback_request_during_the_second_issue_gets_its_own_ticket(db_session, monkeypatch):
    await _seed_categories(db_session)
    session = await get_or_create_session(db_session, call_sid="multi-esc", from_number="+18455550142", to_number="+1")
    session.collected = {"caller_name": "Maria Lopez", "phone_number": "+18455550142", "description": ISSUES["A"], "category": "Other"}
    first = await orchestrator._create_ticket_and_read_back(db_session, session)
    session.state = VoiceCallState.ANYTHING_ELSE
    monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(nlu.TurnResult(yes_no=True, unable_to_determine=False)))
    await orchestrator.handle_turn(db_session, session, utterance="yes")
    orchestrator.update_collected(session, description=ISSUES["B"], category="Other")

    outcome = await orchestrator.escalate(db_session, session, EscalationReason.CALLER_REQUESTED)

    assert outcome.ticket_id not in (None, first.ticket_id)
    callback = await db_session.get(Ticket, outcome.ticket_id)
    assert "CALLBACK REQUESTED" in callback.description and ISSUES["B"] in callback.description


# --- replays are still not duplicates ---------------------------------------------------


async def test_a_replayed_filing_turn_does_not_create_a_second_ticket(db_session):
    await _seed_categories(db_session)
    session = await get_or_create_session(db_session, call_sid="multi-replay", from_number="+18455550142", to_number="+1")
    session.collected = {"caller_name": "Maria Lopez", "phone_number": "+18455550142", "description": ISSUES["A"], "category": "Other"}

    first = await orchestrator._create_ticket_and_read_back(db_session, session)
    replay = await orchestrator._create_ticket_and_read_back(db_session, session)

    assert replay.ticket_id == first.ticket_id and orchestrator.issue_filed(session)
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar() == 1


async def test_a_call_that_began_before_the_flag_existed_still_counts_its_ticket(db_session):
    await _seed_categories(db_session)
    session = await get_or_create_session(db_session, call_sid="multi-legacy", from_number="+18455550142", to_number="+1")
    assert not orchestrator.issue_filed(session)

    first = await orchestrator._create_ticket_and_read_back(db_session, session)
    session.collected = {k: v for k, v in session.collected.items() if k != "ticket_filed"}  # an older session row

    assert orchestrator.issue_filed(session) and session.ticket_id == first.ticket_id


# --- the simulator uses the same rule -----------------------------------------------------


async def test_simulator_end_during_the_second_issue_files_it(client, categories, monkeypatch, db_session):
    sid, body = await _full_intake(client)
    first = body["session"]["ticket"]["id"]
    await _say(client, sid, "yes, one more thing")
    await _say(client, sid, "My monitor keeps flickering")  # a second issue, not finished

    ended = (await client.post("/api/v1/voice-simulator/end", json={"session_id": sid})).json()

    assert ended["ticket"]["id"] != first
    assert (await db_session.execute(select(func.count()).select_from(Ticket))).scalar() == 2

"""The agent reads the callback number back before filing the ticket.

Ticket fields: `phone_number` is the number to call, `callback_number` the one the
caller confirmed (None if they never did), `caller_number` what the call came in
from. The email shows "Callback Number" and never the transcript or intake details.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import VoiceCallSession, VoiceCallState
from app.voice import nlu, orchestrator, scripts
from tests.test_multi_issue_calls import AUTH, SIP, Call, _async, _session
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401
from tests.test_voice_simulator import INTAKE_LINES, _say, _simulator_on, _start, categories  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()
ORIGINAL = "+18455550142"
ORIGINAL_SPOKEN = "8 4 5, 5 5 5, 0 1 4 2"
OTHER = "+12148859089"
OTHER_SPOKEN = "2 1 4, 8 8 5, 9 0 8 9"


@pytest.fixture(autouse=True)
def _callback_confirmation_on(monkeypatch):
    monkeypatch.setattr(settings, "voice_confirm_callback", True)


class CallbackCall(Call):
    """A SIP call whose caller ID can be chosen, driven to the callback question."""

    def __init__(self, client, monkeypatch, call_id, from_number=ORIGINAL):
        super().__init__(client, monkeypatch, call_id)
        self.from_number = from_number

    async def start(self):
        await self.client.post(
            f"{SIP}/start", json={"call_id": self.call_id, "from_number": self.from_number}, headers=AUTH
        )

    def phone_says(self, number):
        """What interpret_phone returns next: a number, or None for "could not make it out"."""
        result = nlu.TurnResult() if number is None else nlu.TurnResult(
            value=number, confidence="high", unable_to_determine=False
        )
        self.monkeypatch.setattr(nlu, "interpret_phone", lambda u: _async(result))

    async def to_callback_question(self, *, spoken_number=None):
        await self.start()
        await self.describe("A")
        self._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False))
        await self.turn("Maria Lopez")
        if spoken_number:  # caller ID withheld: the agent asks for a number first
            self.phone_says(spoken_number)
            await self.turn("my number is whatever")
        self._say(interpret_email=nlu.TurnResult(value="m@hfmg.net", confidence="high", unable_to_determine=False))
        await self.turn("m at hfmg dot net")
        return await self.answer(True)  # the email confirmation, which leads to the callback question


@pytest.fixture
def phone_call(client, monkeypatch, _call_fixture):
    def make(call_id, from_number=ORIGINAL):
        return CallbackCall(client, monkeypatch, call_id, from_number)

    return make


async def _ticket(client, ticket_id) -> dict:
    return (await client.get(f"/api/v1/tickets/{ticket_id}")).json()


# --- 1. the caller confirms the number we have --------------------------------------------


async def test_caller_confirms_the_number_on_file(phone_call, client, db_session, sent_emails):
    call = phone_call("cb-yes")
    asked = await call.to_callback_question()

    text = " ".join(asked["lines"])
    assert f"I have your callback number as {ORIGINAL_SPOKEN}" in text
    assert "they will call this number" in text and "Is this the best number to contact you on?" in text
    assert (await _session(db_session, "cb-yes")).state == VoiceCallState.CONFIRM_CALLBACK_NUMBER
    assert asked["ticket_id"] is None  # nothing filed until it is confirmed

    filed = await call.answer(True)

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"], ticket["caller_number"]) == (ORIGINAL, ORIGINAL, ORIGINAL)
    assert (await _session(db_session, "cb-yes")).state == VoiceCallState.ANYTHING_ELSE
    assert "Callback Number:<br>\n" + ORIGINAL in sent_emails[0]["body_html"]


# --- 2. the caller changes it ------------------------------------------------------------------


async def test_caller_changes_the_number(phone_call, client, db_session, sent_emails):
    call = phone_call("cb-change")
    await call.to_callback_question()

    asked_other = await call.answer(False)
    assert scripts.CALLBACK_ASK_OTHER in " ".join(asked_other["lines"])
    assert (await _session(db_session, "cb-change")).state == VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER

    call.phone_says(OTHER)
    confirm_new = await call.turn("two one four, eight eight five, nine oh eight nine")
    assert f"I have your callback number as {OTHER_SPOKEN}. Is that correct?" in " ".join(confirm_new["lines"])
    assert confirm_new["ticket_id"] is None

    filed = await call.answer(True)

    ticket = await _ticket(client, filed["ticket_id"])
    # Called on the new number; the inbound number is kept for audit.
    assert (ticket["phone_number"], ticket["callback_number"], ticket["caller_number"]) == (OTHER, OTHER, ORIGINAL)
    body = sent_emails[0]["body_html"]
    assert "Callback Number:<br>\n" + OTHER in body and ORIGINAL not in body


async def test_a_new_number_given_with_the_no(phone_call, client, db_session):
    call = phone_call("cb-inline")
    await call.to_callback_question()
    call.phone_says(OTHER)
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=False, unable_to_determine=False))

    confirm_new = await call.turn("No, it's 214 885 9089")  # no separate "what number?" turn

    assert f"I have your callback number as {OTHER_SPOKEN}" in " ".join(confirm_new["lines"])
    assert (await _session(db_session, "cb-inline")).state == VoiceCallState.CONFIRM_CALLBACK_NUMBER
    filed = await call.answer(True)
    assert (await _ticket(client, filed["ticket_id"]))["callback_number"] == OTHER


async def test_rejecting_the_new_number_asks_again(phone_call, client, db_session):
    call = phone_call("cb-reject")
    await call.to_callback_question()
    await call.answer(False)
    call.phone_says(OTHER)
    await call.turn("two one four eight eight five nine oh eight nine")

    again = await call.answer(False)  # "no, that's not what I said"

    assert scripts.CALLBACK_ASK_OTHER in " ".join(again["lines"])
    assert (await _session(db_session, "cb-reject")).state == VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER


# --- 3. an invalid number, then a good one --------------------------------------------------------


async def test_invalid_number_is_asked_for_again_then_accepted(phone_call, client, db_session):
    call = phone_call("cb-invalid")
    await call.to_callback_question()
    await call.answer(False)

    call.phone_says(None)
    retry = await call.turn("one one zero zero six")  # five digits
    assert "I only caught 5 digits" in " ".join(retry["lines"])
    assert (await _session(db_session, "cb-invalid")).state == VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER

    call.phone_says(OTHER)
    confirm_new = await call.turn("two one four eight eight five nine oh eight nine")
    assert OTHER_SPOKEN in " ".join(confirm_new["lines"])
    filed = await call.answer(True)

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"]) == (OTHER, OTHER)


async def test_two_invalid_numbers_fall_back_to_the_original(phone_call, client, db_session):
    call = phone_call("cb-invalid2")
    await call.to_callback_question()
    await call.answer(False)
    call.phone_says(None)

    await call.turn("one one zero zero six")
    last = await call.turn("one zero zero six")  # the second miss ends the asking

    assert f"we'll use {ORIGINAL_SPOKEN}" in " ".join(last["lines"])
    ticket = await _ticket(client, last["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"]) == (ORIGINAL, None)


# --- 4. the caller will not give another number ------------------------------------------------------


@pytest.mark.parametrize("refusal", ["I'd rather not", "no", "skip"])
async def test_refusing_another_number_keeps_the_caller_id(phone_call, client, db_session, sent_emails, refusal):
    call = phone_call(f"cb-refuse-{refusal[:3].strip()}")
    await call.to_callback_question()
    await call.answer(False)

    filed = await call.turn(refusal)

    assert f"we'll use {ORIGINAL_SPOKEN}" in " ".join(filed["lines"])
    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"], ticket["caller_number"]) == (ORIGINAL, None, ORIGINAL)
    assert "not confirmed by the caller; using the number on file" in ticket["description"]  # for the dashboard
    assert "Callback Number:<br>\n" + ORIGINAL in sent_emails[0]["body_html"]  # the email still has a number


async def test_an_unclear_answer_is_asked_once_more_then_the_number_stands(phone_call, client, db_session):
    call = phone_call("cb-unclear")
    await call.to_callback_question()
    unclear = nlu.TurnResult(unable_to_determine=True)
    call.monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(unclear))

    repeat = await call.turn("hmm, maybe")
    assert scripts.CALLBACK_REPEAT in " ".join(repeat["lines"])
    assert (await _session(db_session, "cb-unclear")).state == VoiceCallState.CONFIRM_CALLBACK_NUMBER

    filed = await call.turn("uh")
    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"]) == (ORIGINAL, None)


async def test_asking_for_a_person_at_the_callback_question_escalates(phone_call, db_session):
    call = phone_call("cb-human")
    await call.to_callback_question()
    wants = nlu.TurnResult(unable_to_determine=True, escalation_requested=True)
    call.monkeypatch.setattr(nlu, "interpret_yes_no", lambda q, u: _async(wants))

    await call.turn("let me talk to a person")

    assert (await _session(db_session, "cb-human")).state == VoiceCallState.ESCALATED


# --- 5. withheld caller ID ----------------------------------------------------------------------------


async def test_withheld_caller_id_confirms_the_number_the_caller_gave(phone_call, client, db_session, sent_emails):
    call = phone_call("cb-anon", from_number="anonymous")
    asked = await call.to_callback_question(spoken_number=OTHER)

    assert f"I have your callback number as {OTHER_SPOKEN}" in " ".join(asked["lines"])
    filed = await call.answer(True)

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"], ticket["caller_number"]) == (OTHER, OTHER, "anonymous")
    assert "Callback Number:<br>\n" + OTHER in sent_emails[0]["body_html"]


async def test_withheld_caller_id_and_no_number_given_asks_nothing_and_leaves_it_out(
    phone_call, client, db_session, sent_emails
):
    call = phone_call("cb-nonum", from_number="anonymous")
    await call.start()
    await call.describe("A")
    call._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False))
    await call.turn("Maria Lopez")
    call.phone_says(None)
    await call.turn("I don't know")
    await call.turn("I really don't")  # out of tries: carry on without a number
    call._say(interpret_email=nlu.TurnResult(value="m@hfmg.net", confidence="high", unable_to_determine=False))
    await call.turn("m at hfmg dot net")
    filed = await call.answer(True)

    assert filed["ticket_id"]  # filed, with no callback question in the way
    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"]) == ("unknown", None)
    assert "Callback Number" not in sent_emails[0]["body_html"]


# --- more than one issue, and a corrected number --------------------------------------------------------


async def test_a_second_issue_does_not_ask_for_the_number_again(phone_call, client, db_session):
    call = phone_call("cb-two")
    await call.to_callback_question()
    first = await call.answer(True)

    second = await call.another_issue("B")

    assert second["ticket_id"] and second["ticket_id"] != first["ticket_id"]
    assert (await _ticket(client, second["ticket_id"]))["callback_number"] == ORIGINAL


async def test_a_number_corrected_at_the_read_back_is_confirmed_again(db_session):
    session = VoiceCallSession(
        call_id="cb-fix", from_number=ORIGINAL, to_number="+1", state=VoiceCallState.CONFIRM_SUMMARY, turns=[],
        collected={"phone_number": ORIGINAL, "callback_confirmed": True, "callback_number": ORIGINAL},
    )

    orchestrator._apply_corrections(session, {"phone_number": OTHER})

    assert session.collected["phone_number"] == OTHER
    assert session.collected["callback_confirmed"] is False and session.collected["callback_number"] is None
    assert orchestrator._callback_number_pending(session)


async def test_the_setting_turns_the_step_off(phone_call, client, monkeypatch):
    monkeypatch.setattr(settings, "voice_confirm_callback", False)
    call = phone_call("cb-off")
    await call.start()
    await call.describe("A")
    call._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False))
    await call.turn("Maria Lopez")
    call._say(interpret_email=nlu.TurnResult(value="m@hfmg.net", confidence="high", unable_to_determine=False))
    await call.turn("m at hfmg dot net")

    filed = await call.answer(True)

    ticket = await _ticket(client, filed["ticket_id"])
    assert (ticket["phone_number"], ticket["callback_number"], ticket["caller_number"]) == (ORIGINAL, None, ORIGINAL)


# --- the simulator ----------------------------------------------------------------------------------------


async def test_simulator_asks_for_and_confirms_the_callback_number(client, categories, db_session):
    sid = (await _start(client, caller_id=ORIGINAL))["session"]["id"]
    lines = [line for line in INTAKE_LINES if line != "8 4 5 5 5 5 0 1 4 2"]  # caller ID: no phone question
    for line in lines[:-1]:
        await _say(client, sid, line)
    asked = (await _say(client, sid, lines[-1])).json()
    assert asked["session"]["state"] == "CONFIRM_CALLBACK_NUMBER"
    assert "callback number as" in asked["reply"]["text"] and asked["session"]["ticket"] is None

    changed = (await _say(client, sid, "no, it's 2 1 4 8 8 5 9 0 8 9")).json()
    assert changed["session"]["state"] == "CONFIRM_CALLBACK_NUMBER"
    assert OTHER_SPOKEN in changed["reply"]["text"]
    done = (await _say(client, sid, "yes")).json()

    ticket = (await client.get(f"/api/v1/tickets/{done['session']['ticket']['id']}")).json()
    assert (ticket["phone_number"], ticket["callback_number"], ticket["caller_number"]) == (OTHER, OTHER, ORIGINAL)
    assert ticket["source"] == "SIMULATOR"


# --- web / API tickets are not touched ------------------------------------------------------------------------


async def test_web_tickets_are_unaffected(client, category_id, sent_emails, monkeypatch):
    monkeypatch.setattr(settings, "email_provider", "hfmg_internal")
    created = (
        await client.post(
            "/api/v1/tickets",
            json={
                "caller_name": "Ann",
                "phone_number": "+15550001111",
                "category_id": str(category_id),
                "description": "Cannot connect to the clinic wifi.",
            },
        )
    ).json()

    assert created["phone_number"] == "+15550001111"
    assert created["caller_number"] is None and created["callback_number"] is None
    assert created["caller_type"] is None
    assert "Callback Number:<br>\n+15550001111" in sent_emails[0]["body_html"]  # the form's number, as the callback


async def test_the_ticket_api_keeps_every_existing_field(client, category_id):
    created = (
        await client.post(
            "/api/v1/tickets",
            json={"caller_name": "Ann", "phone_number": "+15550001111", "category_id": str(category_id), "description": "x"},
        )
    ).json()
    existing = {
        "id", "ticket_number", "caller_name", "phone_number", "email", "category", "priority", "description",
        "ai_summary", "ai_summary_status", "ai_summary_generated_at", "status", "source", "created_at", "updated_at",
    }
    assert existing <= set(created) and set(created) - existing == {"caller_number", "callback_number", "caller_type"}

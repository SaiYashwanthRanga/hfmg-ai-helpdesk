"""A real provider call: the name was read back and the caller said "That's close enough." A provider between patients
has no time to spell: a half yes is taken as a yes and the call moves on, with no spelling asked. The ticket notes that
the name may be slightly off, so IT knows.
"""

import pytest

from app.db.models import VoiceCallState
from app.voice import nlu, orchestrator, scripts
from tests.test_voice_spelling import _at_name_confirmation, confirm_names  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize("said", ["That's close enough.", "Yeah, almost.", "Close enough, yes."])
async def test_close_enough_takes_the_name_and_moves_on(said, db_session, monkeypatch, confirm_names):  # noqa: F811
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Chindun Natakarani")
    assert session.state == VoiceCallState.CONFIRM_NAME

    out = await orchestrator.handle_turn(db_session, session, utterance=said)

    assert session.state != VoiceCallState.CONFIRM_NAME
    assert scripts.NAME_SPELL_FIRST not in out.text
    assert session.collected["name_confirmed_as"] == "Chindun Natakarani"
    assert session.collected["name_close_enough"] is True


async def test_the_ticket_says_the_name_may_be_slightly_off(db_session, monkeypatch, confirm_names):  # noqa: F811
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Chindun Natakarani")
    await orchestrator.handle_turn(db_session, session, utterance="That's close enough.")

    assert "close enough" in orchestrator._intake_details(session.collected)


async def test_a_plain_yes_adds_no_note(db_session, monkeypatch, confirm_names):  # noqa: F811
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Chindun Natakarani")
    await orchestrator.handle_turn(db_session, session, utterance="Yes, that's right.")

    assert not session.collected.get("name_close_enough")
    assert "close enough" not in orchestrator._intake_details(session.collected)


@pytest.mark.parametrize("said, hedged", [
    ("That's close enough.", True), ("Almost.", True), ("Sort of.", True), ("Yes, that's right.", False),
    ("Yes.", False),
])
async def test_hedged(said, hedged):
    assert nlu.hedged(said) is hedged

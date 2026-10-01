"""speech_context.stt_prompt: the per-state transcription prompt sent to the SIP gateway."""

import pytest

from app.speech import context

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_description_prompt_carries_it_vocabulary():
    prompt = context.stt_prompt("COLLECT_DESCRIPTION")
    assert "describes an IT problem" in prompt
    assert "Outlook" in prompt and "VPN" in prompt


async def test_name_prompt_covers_name_and_department():
    prompt = context.stt_prompt("COLLECT_NAME")
    assert "first and last name" in prompt and "department" in prompt


async def test_email_step_uses_the_spelling_prompt():
    prompt = context.stt_prompt("COLLECT_EMAIL")
    assert prompt == context.transcription_prompt("SPELL_EMAIL")
    assert "at sign" in prompt
    assert "Vocabulary" not in prompt  # vocabulary would pull letters toward words


async def test_name_correction_uses_the_spelling_prompt():
    prompt = context.stt_prompt("CONFIRM_NAME", {"name_spell_step": "first"})
    assert prompt == context.transcription_prompt("SPELL_NAME")
    assert "one letter at a time" in prompt


async def test_name_confirmation_without_spelling_is_yes_no():
    assert "yes or no" in context.stt_prompt("CONFIRM_NAME", {})


@pytest.mark.parametrize(
    "state", ["CONFIRM_EMAIL", "CONFIRM_CATEGORY", "CONFIRM_SUMMARY", "ANYTHING_ELSE"]
)
async def test_confirmation_states_expect_yes_or_no(state):
    assert "yes or no" in context.stt_prompt(state)


async def test_phone_prompt():
    assert "ten-digit" in context.stt_prompt("COLLECT_PHONE")


@pytest.mark.parametrize("state", [None, "", "GREETING", "ESCALATED", "COMPLETED", "ABANDONED", "UNKNOWN"])
async def test_states_without_a_hint_return_none(state):
    assert context.stt_prompt(state) is None


async def test_matches_the_simulator_prompt():
    # The phone path and the simulator must hear the same hint for the same state.
    assert context.stt_prompt("COLLECT_DETAILS") == context.transcription_prompt("COLLECT_DETAILS")

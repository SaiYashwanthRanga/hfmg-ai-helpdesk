"""A noisy line: background talk mixed into the caller's answer.

In a loud office the gateway's recording picks up other people too. At a short question (yes/no, a number, an
extension) a long answer, or one the gateway cut off at its length limit, is not acted on: the caller is asked to move
somewhere quieter and asked again, and a line that stays noisy becomes a callback request. At an open question a long
answer is normal and always used. Caption text that Whisper-family recognizers make up from noise ("Thanks for
watching!") is removed, and an answer that is only that is a silence.
"""

import pytest

from app.core.config import get_settings
from app.db.models import EscalationReason, VoiceCallState
from app.voice import nlu, orchestrator, scripts
from tests.test_multi_issue_calls import AUTH, ISSUES, SIP, _async, _session
from tests.test_multi_issue_calls import call  # noqa: F401  (seeds categories, token)
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()

#: A "yes" with a coworker's conversation around it: far longer than any answer to a yes/no question.
CROSSTALK_YES = (
    "yes and then I told her the meeting was moved to three because the room upstairs was booked and nobody "
    "had the projector so we said we would just do it on Teams instead next week"
)


async def _turn(call, utterance, **extra):  # noqa: F811
    response = await call.client.post(
        f"{SIP}/turn", json={"call_id": call.call_id, "utterance": utterance, **extra}, headers=AUTH
    )
    assert response.status_code == 200
    return response.json()


async def _to_email_confirmation(call, db_session):  # noqa: F811
    await call.start()
    await call.describe("A")
    call._say(interpret_name=nlu.TurnResult(value="Maria Lopez", confidence="high", unable_to_determine=False))
    await call.turn("Maria Lopez")
    call._say(interpret_email=nlu.TurnResult(value="m@hfmg.net", confidence="high", unable_to_determine=False))
    await call.turn("m at hfmg dot net")
    assert (await _session(db_session, call.call_id)).state == VoiceCallState.CONFIRM_EMAIL
    # Had the answer been used, this is what it would have meant.
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=True, unable_to_determine=False))


# --- a short question ----------------------------------------------------------------------


async def test_a_long_answer_to_a_yes_no_question_is_asked_again(call, db_session):  # noqa: F811
    await _to_email_confirmation(call, db_session)

    outcome = await _turn(call, CROSSTALK_YES)

    session = await _session(db_session, call.call_id)
    assert outcome["lines"][0].startswith(scripts.NOISY_LINE)
    assert outcome["expect_reply"] is True and outcome["ticket_id"] is None
    assert session.state == VoiceCallState.CONFIRM_EMAIL  # the "yes" in it was not taken
    assert session.misunderstanding_count == 0  # the caller may have answered perfectly
    caller_lines = [t["text"] for t in session.turns if t["role"] == "caller"]
    assert caller_lines[-1] == f"{orchestrator.NOISY_ANSWER} {CROSSTALK_YES}"


async def test_the_question_asked_again_is_answered_normally(call, db_session):  # noqa: F811
    await _to_email_confirmation(call, db_session)
    await _turn(call, CROSSTALK_YES)

    await call.answer(True)

    assert (await _session(db_session, call.call_id)).state != VoiceCallState.CONFIRM_EMAIL


async def test_a_short_answer_cut_off_at_the_length_limit_is_asked_again(call, db_session):  # noqa: F811
    await _to_email_confirmation(call, db_session)

    outcome = await _turn(call, "yes", end_reason="max_length", speech_ms=12000)

    assert outcome["lines"][0].startswith(scripts.NOISY_LINE)
    assert (await _session(db_session, call.call_id)).state == VoiceCallState.CONFIRM_EMAIL


async def test_a_short_answer_that_ended_in_silence_is_used(call, db_session):  # noqa: F811
    await _to_email_confirmation(call, db_session)

    outcome = await _turn(call, "yes", end_reason="silence", speech_ms=600)

    assert not outcome["lines"][0].startswith(scripts.NOISY_LINE)
    assert (await _session(db_session, call.call_id)).state != VoiceCallState.CONFIRM_EMAIL


async def test_a_line_that_stays_noisy_becomes_a_callback_request(call, db_session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "voice_max_noisy_answers", 2)
    await _to_email_confirmation(call, db_session)
    await _turn(call, CROSSTALK_YES)

    outcome = await _turn(call, CROSSTALK_YES)

    session = await _session(db_session, call.call_id)
    assert session.state == VoiceCallState.ESCALATED
    assert session.escalation_reason == EscalationReason.REPEATED_MISUNDERSTANDING
    assert outcome["ticket_id"] is not None  # the caller still gets a ticket number


# --- an open question ----------------------------------------------------------------------


async def test_a_long_description_cut_off_at_the_length_limit_is_used(call, db_session):  # noqa: F811
    await call.start()
    heard = []
    result = nlu.TurnResult(
        value=ISSUES["A"], confidence="high", unable_to_determine=False, category="Other",
        extras={"short_issue": "your computer", "started": "today", "work_blocked": True},
    )

    def interpret_description(utterance, *args, **kwargs):
        heard.append(utterance)
        return _async(result)

    call.monkeypatch.setattr(nlu, "interpret_description", interpret_description)
    long_description = " ".join([ISSUES["A"]] * 8)

    outcome = await _turn(call, long_description, end_reason="max_length")

    assert not outcome["lines"][0].startswith(scripts.NOISY_LINE)
    assert heard == [long_description]
    assert (await _session(db_session, call.call_id)).state != VoiceCallState.COLLECT_DESCRIPTION


# --- recognizer caption text ---------------------------------------------------------------


async def test_caption_text_alone_is_a_silence(call, db_session):  # noqa: F811
    await call.start()

    outcome = await _turn(call, "Thank you for watching!")

    session = await _session(db_session, call.call_id)
    assert outcome["lines"] == [scripts.DESCRIPTION_RETRY[0]]
    caller_lines = [t["text"] for t in session.turns if t["role"] == "caller"]
    assert caller_lines[-1].startswith(orchestrator.NOT_CALLER_SPEECH)


async def test_caption_text_after_a_real_answer_is_removed(call, db_session):  # noqa: F811
    await call.start()
    heard = []
    result = nlu.TurnResult(
        value=ISSUES["A"], confidence="high", unable_to_determine=False, category="Other",
        extras={"short_issue": "your computer", "started": "today", "work_blocked": True},
    )

    def interpret_description(utterance, *args, **kwargs):
        heard.append(utterance)
        return _async(result)

    call.monkeypatch.setattr(nlu, "interpret_description", interpret_description)

    await _turn(call, f"{ISSUES['A']} Thanks for watching! Subtitles by the Amara.org community")

    assert heard == [ISSUES["A"]]
    session = await _session(db_session, call.call_id)
    assert [t["text"] for t in session.turns if t["role"] == "caller"][-1] == ISSUES["A"]


@pytest.mark.parametrize(
    "said, kept",
    [
        ("Thank you.", "Thank you."),  # a caller's own thanks is an answer
        ("thank you for calling me back so fast", "thank you for calling me back so fast"),
        ("yes. Thanks for watching!", "yes."),
        ("Please subscribe to my channel. no", "no"),
        ("Subtitles by the Amara.org community", ""),
        ("Transcription by CastingWords", ""),
        ("Thank you so much for watching.", ""),
    ],
)
async def test_which_words_are_caption_text(said, kept):
    assert orchestrator._without_recognizer_artifacts(said) == kept


# --- the gateway contract ------------------------------------------------------------------


async def test_unknown_end_reason_and_any_speech_length_are_accepted(call):  # noqa: F811
    await call.start()
    call._say(interpret_description=nlu.TurnResult(value=ISSUES["A"], confidence="high", unable_to_determine=False))

    await _turn(call, ISSUES["A"], end_reason="something_new", speech_ms=4321.5)

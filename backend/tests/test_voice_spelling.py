"""Names, spelled letters and emails -- the hardest part of voice intake.

Transcription turns unfamiliar names into other words ("saiyashwanth" ->
"sichuan") and merges spelled letters ("m, l, o, p, e, z" -> "MLOpec"), so:
- callers spell the part of their email before the @ (hfmg.net is known);
- names are read back spelled and corrected by spelling;
- spelled letters are decoded deterministically, never by a model;
- spelling turns use a transcriber that keeps letters literal;
- English only: non-Latin transcripts are re-transcribed, then dropped.
See eval/spelling_bench.py for the measurements behind each choice.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket, VoiceCallState
from app.speech import context
from app.speech.base import SpeechResult, Transcription
from app.speech.openai_speech import OpenAISpeechProvider, _non_latin
from app.voice import nlu, orchestrator, scripts
from app.voice.session import get_or_create_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

settings = get_settings()


# --- decoder -------------------------------------------------------------------


@pytest.mark.parametrize(
    "heard,expected",
    [
        ("Y-A-S-H-W-A-N-T-H", "Yashwanth"),
        ("Y A S H W A N T H", "Yashwanth"),
        ("why a s h w a n t h", "Yashwanth"),  # "Y" transcribed as "why"
        ("X, Y, A, S, H, W, A and T, H.", "Xyashwanth"),  # real call: "and" was the N
        ("It's Y as in yellow, A, S, H, W as in water, A, N, T as in Tom, H.", "Yashwanth"),
        ("H A double D A D", "Haddad"),
        ("are a n g a", "Ranga"),  # "R" transcribed as "are"
        ("sierra alpha india", "Sai"),  # NATO alphabet
        ("Maria Lopez", None),  # not spelled
        ("", None),
    ],
)
async def test_decode_spelled_name(heard, expected):
    assert nlu.decode_spelled(heard) == expected


@pytest.mark.parametrize(
    "heard,expected",
    [
        ("R A N G A dot S A I Y A S H W A N T H", "ranga.saiyashwanth"),
        ("R-A-N-G-A dot S-A-I", "ranga.sai"),
        ("P S H A H at H F M G dot net", "pshah"),
        ("j carter underscore two", "jcarter_2"),
        ("M L O P E Z", "mlopez"),
    ],
)
async def test_decode_email_local(heard, expected):
    assert nlu.decode_email_local(heard) == expected


async def test_spelled_email_gets_the_home_domain(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("spelled input is decoded, never sent to the model")

    monkeypatch.setattr(nlu, "_call_structured", boom)
    result = await nlu.interpret_email("R A N G A dot S A I Y A S H W A N T H")
    assert result.value == "ranga.saiyashwanth@hfmg.net"
    # A spelling-mode transcriber may spell "skip" too.
    assert (await nlu.interpret_email("S K I P")).extras.get("declined") is True


async def test_spelled_name_read_back():
    assert scripts.spelled_name("Maria Lopez") == "M A R I A, L O P E Z"
    assert scripts.spelled_name("Yashwanth") == "Y A S H W A N T H"


# --- recognition mode -------------------------------------------------------------


async def test_spelling_turns_get_the_spelling_prompt():
    assert context.recognition_mode("COLLECT_EMAIL", {}) == "SPELL_EMAIL"
    assert context.recognition_mode("CONFIRM_NAME", {"name_spell_step": "first"}) == "SPELL_NAME"
    assert context.recognition_mode("CONFIRM_NAME", {}) == "CONFIRM_NAME"  # yes/no, not spelling
    assert context.recognition_mode("COLLECT_NAME", {}) == "COLLECT_NAME"
    prompt = context.transcription_prompt("SPELL_NAME")
    assert "letter" in prompt and "Do not join" in prompt
    assert "eClinicalWorks" not in prompt  # vocabulary would pull letters toward words


# --- English only -------------------------------------------------------------------


async def test_non_latin_detection():
    assert _non_latin("నా మైండ్ అనేసి చదువు నాటకాలని.")
    assert not _non_latin("My name is Yashwanth.")
    assert not _non_latin("Naṭakarālu")  # Latin with diacritics is still English script
    assert not _non_latin("")


class _FakeTranscriptions:
    def __init__(self, texts):
        self.texts = list(texts)
        self.models = []

    async def create(self, **kwargs):
        self.models.append(kwargs["model"])
        return type("R", (), {"text": self.texts.pop(0), "model_dump": lambda self: {}})()


class _FakeClient:
    def __init__(self, texts):
        self.audio = type("A", (), {})()
        self.audio.transcriptions = _FakeTranscriptions(texts)


async def test_non_english_transcript_is_retranscribed_in_english(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    provider = OpenAISpeechProvider()
    client = _FakeClient(["చందు నాటకరాలు.", "Chandu Natakaralu."])
    monkeypatch.setattr(provider, "_get_client", lambda: client)
    result = await provider.transcribe(audio=b"x", mime_type="audio/wav", language="en-US")
    assert result.transcription.text == "Chandu Natakaralu."
    assert client.audio.transcriptions.models == [settings.speech_stt_model, settings.speech_stt_spelling_model]


async def test_still_non_english_becomes_an_unheard_turn(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    provider = OpenAISpeechProvider()
    monkeypatch.setattr(provider, "_get_client", lambda: _FakeClient(["చందు", "చందు"]))
    result = await provider.transcribe(audio=b"x", mime_type="audio/wav", language="en")
    assert result.transcription.text == ""
    assert result.transcription.raw["discarded_non_english"] == "చందు"


# --- name read-back flow ---------------------------------------------------------------


def _async(value):
    async def _coro(*args, **kwargs):
        return value

    return _coro


@pytest.fixture
def confirm_names(monkeypatch):
    monkeypatch.setattr(settings, "voice_confirm_name", True)


async def _seed(db):
    for name in ("Microsoft 365", "Other"):
        if (await db.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db.commit()


async def _at_name_confirmation(db, monkeypatch, name="Yashwandh", call_sid=None):
    await _seed(db)
    session = await get_or_create_session(
        db, call_sid=call_sid or f"CA-{uuid.uuid4().hex[:8]}", from_number="+12148859089", to_number="+18455559999"
    )
    await orchestrator.start_call(session)
    described = nlu.TurnResult(
        value="Outlook won't open.", confidence="high", unable_to_determine=False, category="Microsoft 365",
        priority=Priority.MEDIUM, extras={"short_issue": "Outlook", "started": "today", "work_blocked": False},
    )
    monkeypatch.setattr(nlu, "interpret_description", _async(described))
    await orchestrator.handle_turn(db, session, utterance="outlook won't open")
    monkeypatch.setattr(
        nlu, "interpret_name", _async(nlu.TurnResult(value=name, unable_to_determine=False, extras={"department": "IT"}))
    )
    out = await orchestrator.handle_turn(db, session, utterance=name)
    return session, out


async def test_unfamiliar_name_is_read_back_spelled(db_session, monkeypatch, confirm_names):
    session, out = await _at_name_confirmation(db_session, monkeypatch, name="Chindun Natakarani")
    assert session.state == VoiceCallState.CONFIRM_NAME
    assert "C H I N D U N, N A T A K A R A N I" in out.text
    assert session.collected["name_confidence"] == "low"

    await orchestrator.handle_turn(db_session, session, utterance="Yes, that's right.")
    assert session.collected["caller_name"] == "Chindun Natakarani"
    assert session.state == VoiceCallState.COLLECT_EMAIL  # caller ID present: phone skipped


async def test_common_name_is_not_read_back(db_session, monkeypatch, confirm_names):
    """Only doubtful names cost the caller a turn."""
    session, out = await _at_name_confirmation(db_session, monkeypatch, name="Maria Lopez")
    assert session.state == VoiceCallState.COLLECT_EMAIL
    assert session.collected["name_confidence"] == "high"
    assert "M A R I A" not in out.text


async def test_low_recognizer_confidence_forces_a_read_back(db_session, monkeypatch, confirm_names):
    """Even a common name is checked when the recognizer itself was unsure."""
    await _seed(db_session)
    session = await get_or_create_session(
        db_session, call_sid="CA-lowconf", from_number="+12148859089", to_number="+18455559999"
    )
    await orchestrator.start_call(session)
    described = nlu.TurnResult(
        value="Outlook won't open.", confidence="high", unable_to_determine=False, category="Microsoft 365",
        priority=Priority.MEDIUM, extras={"short_issue": "Outlook", "started": "today", "work_blocked": False},
    )
    monkeypatch.setattr(nlu, "interpret_description", _async(described))
    await orchestrator.handle_turn(db_session, session, utterance="outlook", confidence=0.95)
    monkeypatch.setattr(
        nlu, "interpret_name", _async(nlu.TurnResult(value="Maria Lopez", unable_to_determine=False, extras={"department": "Billing"}))
    )
    await orchestrator.handle_turn(db_session, session, utterance="Maria Lopez", confidence=0.61)
    assert session.state == VoiceCallState.CONFIRM_NAME


async def test_wrong_name_is_corrected_by_spelling_then_read_back(db_session, monkeypatch, confirm_names):
    """The real call: 'Yashwanth' heard as 'Yashwandh' -- and the fix must be repeated back."""
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Yashwandh Ranga")
    out = await orchestrator.handle_turn(db_session, session, utterance="No.")
    assert session.state == VoiceCallState.CONFIRM_NAME
    assert scripts.NAME_SPELL_FIRST in out.text

    out = await orchestrator.handle_turn(db_session, session, utterance="Y-A-S-H-W-A-N-T-H")
    assert scripts.NAME_SPELL_LAST in out.text
    out = await orchestrator.handle_turn(db_session, session, utterance="R A N G A")
    # The corrected name is read back before it is trusted.
    assert session.state == VoiceCallState.CONFIRM_NAME
    assert "So that's Y A S H W A N T H, R A N G A" in out.text
    assert session.collected["caller_name"] == "Yashwanth Ranga"

    await orchestrator.handle_turn(db_session, session, utterance="Yes, that's right.")
    assert session.state == VoiceCallState.COLLECT_EMAIL
    assert session.misunderstanding_count == 0


async def test_correction_spelled_in_the_same_breath_is_read_back(db_session, monkeypatch, confirm_names):
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Yashwandh Ranga")
    out = await orchestrator.handle_turn(db_session, session, utterance="No, it's Y A S H W A N T H.")
    assert session.collected["caller_name"] == "Yashwanth Ranga"
    assert session.state == VoiceCallState.CONFIRM_NAME
    assert "So that's Y A S H W A N T H, R A N G A" in out.text
    await orchestrator.handle_turn(db_session, session, utterance="Yes.")
    assert session.state == VoiceCallState.COLLECT_EMAIL


async def test_edit_instruction_is_applied_and_read_back(db_session, monkeypatch, confirm_names):
    """Real call: 'Can you add H in the last?' -- no spelling, an instruction."""
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Yashwant")
    monkeypatch.setattr(
        nlu, "interpret_name_correction", _async(nlu.TurnResult(value="Yashwanth", unable_to_determine=False))
    )
    out = await orchestrator.handle_turn(db_session, session, utterance="Can you add H in the last?")
    assert session.collected["caller_name"] == "Yashwanth"
    assert "So that's Y A S H W A N T H" in out.text
    await orchestrator.handle_turn(db_session, session, utterance="Yes.")
    assert session.state == VoiceCallState.COLLECT_EMAIL


async def test_unclear_edit_instruction_falls_back_to_spelling(db_session, monkeypatch, confirm_names):
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Chindun Natakarani")
    monkeypatch.setattr(nlu, "interpret_name_correction", _async(nlu.TurnResult()))
    monkeypatch.setattr(nlu, "interpret_yes_no", _async(nlu.TurnResult()))
    out = await orchestrator.handle_turn(db_session, session, utterance="it has some other letter in the middle somewhere")
    assert scripts.NAME_SPELL_FIRST in out.text
    assert session.collected["caller_name"] == "Chindun Natakarani"  # nothing guessed


async def test_second_wrong_spelling_is_accepted_and_flagged(db_session, monkeypatch, confirm_names):
    """No loops: after two read-backs the caller's own spelling stands, marked unverified."""
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Yashwandh")
    await orchestrator.handle_turn(db_session, session, utterance="No.")
    await orchestrator.handle_turn(db_session, session, utterance="Y A S H W A N T")  # round 1 read-back
    await orchestrator.handle_turn(db_session, session, utterance="No.")
    await orchestrator.handle_turn(db_session, session, utterance="Y A S H W A N T H")  # round 2 read-back
    out = await orchestrator.handle_turn(db_session, session, utterance="No.")
    assert session.collected["name_unverified"] is True
    assert session.collected["caller_name"] == "Yashwanth"
    assert scripts.NAME_ACCEPT_AS_SPOKEN in out.text
    assert session.state == VoiceCallState.COLLECT_EMAIL


async def test_silence_at_name_confirmation_moves_on(db_session, monkeypatch, confirm_names):
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Chindun Natakarani")
    await orchestrator.handle_turn(db_session, session, utterance="")
    assert session.state == VoiceCallState.COLLECT_EMAIL
    assert session.misunderstanding_count == 0

async def test_spelled_email_end_to_end(db_session, monkeypatch, confirm_names):
    session, _ = await _at_name_confirmation(db_session, monkeypatch, name="Yashwanth")
    await orchestrator.handle_turn(db_session, session, utterance="Yes.")
    out = await orchestrator.handle_turn(db_session, session, utterance="R A N G A dot S A I Y A S H W A N T H")
    assert session.collected["email"] == "ranga.saiyashwanth@hfmg.net"
    assert "r a n g a . s a i y a s h w a n t h at h f m g dot net" in out.text
    await orchestrator.handle_turn(db_session, session, utterance="Yes, correct.")
    await db_session.commit()
    ticket = await db_session.get(Ticket, session.ticket_id)
    assert ticket.email == "ranga.saiyashwanth@hfmg.net"
    assert ticket.caller_name == "Yashwanth"

"""Provider-agnostic speech-to-text and text-to-speech.

Used only by the AI Call Simulator. The production phone path gets STT from
the SIP gateway's own STT/TTS, so nothing here is on that path.

Like LLMProvider, failures return None rather than raising: an unheard
utterance is a normal conversational event (the orchestrator re-prompts),
not an exception.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

AudioFormat = Literal["mp3", "opus", "aac", "wav"]

AUDIO_MIME = {"mp3": "audio/mpeg", "opus": "audio/ogg", "aac": "audio/aac", "wav": "audio/wav"}

# What browsers' MediaRecorder produces, plus common upload formats the
# OpenAI transcription endpoint accepts. Parameters (";codecs=opus") are
# stripped before the lookup.
SUPPORTED_UPLOAD_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


def base_mime_type(mime_type: str) -> str:
    return (mime_type or "").split(";", 1)[0].strip().lower()


@dataclass
class Transcription:
    text: str
    confidence: float | None = None
    language: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpeechResult:
    """Outcome of one provider call, including the failure reason if any."""

    transcription: Transcription | None = None
    audio: bytes | None = None
    error: str | None = None


class SpeechStreamError(Exception):
    """Synthesis failed before any audio was produced."""


@runtime_checkable
class SpeechProvider(Protocol):
    name: str

    @property
    def is_configured(self) -> bool: ...

    async def transcribe(
        self,
        *,
        audio: bytes,
        mime_type: str,
        language: str,
        prompt: str | None = None,
        model: str | None = None,
    ) -> SpeechResult: ...

    async def synthesize(self, *, text: str, audio_format: AudioFormat = "mp3") -> SpeechResult: ...

    def synthesize_stream(self, *, text: str, audio_format: AudioFormat = "mp3") -> AsyncIterator[bytes]:
        """Yield audio as the provider produces it, so playback can start on
        the first chunk. Raises SpeechStreamError if nothing could be produced."""
        ...

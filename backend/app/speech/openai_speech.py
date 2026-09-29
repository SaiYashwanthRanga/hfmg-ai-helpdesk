"""OpenAI speech-to-text and text-to-speech for the AI Call Simulator."""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.llm.openai_provider import _describe
from app.speech.base import (
    SUPPORTED_UPLOAD_TYPES,
    AudioFormat,
    SpeechResult,
    SpeechStreamError,
    Transcription,
    base_mime_type,
)

logger = logging.getLogger("hfmg.speech.openai")

settings = get_settings()

_STREAM_CHUNK = 4096


def _non_latin(text: str) -> bool:
    """True when most letters are outside the Latin alphabets (e.g. Telugu)."""
    letters = [c for c in text or "" if c.isalpha()]
    if not letters:
        return False
    foreign = sum(1 for c in letters if ord(c) > 0x024F)
    return foreign / len(letters) > 0.3


class _AudioCache:
    """Exact-text LRU of synthesized replies.

    The greeting, retries and fixed questions repeat across every call; a hit
    costs nothing and plays instantly. Keyed by model, voice, format and text,
    so a settings change never serves stale audio.
    """

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._items: OrderedDict[tuple, bytes] = OrderedDict()

    def get(self, key: tuple) -> bytes | None:
        if key not in self._items:
            return None
        self._items.move_to_end(key)
        return self._items[key]

    def put(self, key: tuple, audio: bytes) -> None:
        if self.capacity <= 0:
            return
        self._items[key] = audio
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)


class OpenAISpeechProvider:
    name = "openai"

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        self.cache = _AudioCache(settings.speech_tts_cache_entries)

    @property
    def is_configured(self) -> bool:
        return bool(settings.openai_api_key)

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {"api_key": settings.openai_api_key, "max_retries": 0}
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def warm(self) -> None:
        """Open the speech client's pooled connection ahead of the first turn."""
        if not self.is_configured:
            return
        try:
            await asyncio.wait_for(self._get_client().models.retrieve(settings.speech_tts_model), timeout=5)
        except Exception:
            pass

    def _cache_key(self, text: str, audio_format: str) -> tuple:
        return (settings.speech_tts_model, settings.speech_tts_voice, audio_format, text)

    async def transcribe(
        self,
        *,
        audio: bytes,
        mime_type: str,
        language: str,
        prompt: str | None = None,
        model: str | None = None,
    ) -> SpeechResult:
        """English only: callers speak English, but a transcriber sometimes
        writes accented English in another script (seen in a real test call:
        Telugu script). Anything that comes back non-Latin is re-transcribed
        with the spelling model forced to English; if that is still not
        English, the turn is treated as unheard rather than guessed at."""
        if not self.is_configured:
            return SpeechResult(error="OPENAI_API_KEY is not set")

        # "en-US" -> "en": the transcription API takes ISO-639-1.
        iso_language = (language or "en").split("-", 1)[0]
        model = model or settings.speech_stt_model
        result = await self._transcribe_once(audio, mime_type, iso_language, prompt, model)
        if result.transcription and _non_latin(result.transcription.text):
            first = result.transcription.text
            fallback = settings.speech_stt_spelling_model
            logger.info("Transcript was not in English script; retrying with %s", fallback)
            result = await self._transcribe_once(audio, mime_type, iso_language, prompt, fallback)
            if result.transcription is not None:
                result.transcription.raw["first_attempt"] = first
                if _non_latin(result.transcription.text):
                    result.transcription.raw["discarded_non_english"] = result.transcription.text
                    result.transcription.text = ""
        return result

    async def _transcribe_once(
        self, audio: bytes, mime_type: str, iso_language: str, prompt: str | None, model: str
    ) -> SpeechResult:
        extension = SUPPORTED_UPLOAD_TYPES.get(base_mime_type(mime_type), "webm")
        extra = {"prompt": prompt} if prompt else {}
        try:
            response = await asyncio.wait_for(
                self._get_client().audio.transcriptions.create(
                    model=model,
                    file=(f"utterance.{extension}", audio, base_mime_type(mime_type)),
                    language=iso_language,
                    response_format="json",
                    **extra,
                ),
                timeout=settings.speech_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("Transcription exceeded %.1fs budget", settings.speech_timeout_seconds)
            return SpeechResult(error=f"timeout after {settings.speech_timeout_seconds:.1f}s")
        except Exception as exc:
            logger.warning("Transcription failed: %s", _describe(exc))
            return SpeechResult(error=_describe(exc))

        raw = response.model_dump() if hasattr(response, "model_dump") else {"text": getattr(response, "text", "")}
        return SpeechResult(
            transcription=Transcription(
                text=(getattr(response, "text", "") or "").strip(),
                # The OpenAI transcription API reports no utterance-level
                # confidence; Twilio's does. Left None rather than invented.
                confidence=None,
                language=iso_language,
                raw={"model": model, "prompt": prompt, **raw},
            )
        )

    async def synthesize(self, *, text: str, audio_format: AudioFormat = "mp3") -> SpeechResult:
        chunks: list[bytes] = []
        try:
            async for chunk in self.synthesize_stream(text=text, audio_format=audio_format):
                chunks.append(chunk)
        except SpeechStreamError as exc:
            return SpeechResult(error=str(exc))
        return SpeechResult(audio=b"".join(chunks))

    async def synthesize_stream(self, *, text: str, audio_format: AudioFormat = "mp3") -> AsyncIterator[bytes]:
        """Stream synthesized audio; cache complete results for repeat lines.

        The timeout applies to the *first* chunk -- the caller-visible wait --
        not to the whole reply, so a long read-back is never cut off mid-word.
        """
        if not self.is_configured:
            raise SpeechStreamError("OPENAI_API_KEY is not set")
        key = self._cache_key(text, audio_format)
        cached = self.cache.get(key)
        if cached is not None:
            yield cached
            return

        chunks: list[bytes] = []
        try:
            async with self._get_client().audio.speech.with_streaming_response.create(
                model=settings.speech_tts_model,
                voice=settings.speech_tts_voice,
                input=text[:4000],
                response_format=audio_format,
            ) as response:
                iterator = response.iter_bytes(_STREAM_CHUNK).__aiter__()
                try:
                    first = await asyncio.wait_for(iterator.__anext__(), timeout=settings.speech_timeout_seconds)
                except StopAsyncIteration:
                    raise SpeechStreamError("provider returned no audio") from None
                chunks.append(first)
                yield first
                async for chunk in iterator:
                    chunks.append(chunk)
                    yield chunk
        except SpeechStreamError:
            raise
        except asyncio.TimeoutError:
            logger.warning("Speech synthesis produced no audio within %.1fs", settings.speech_timeout_seconds)
            if not chunks:
                raise SpeechStreamError(f"no audio after {settings.speech_timeout_seconds:.1f}s") from None
            return
        except Exception as exc:
            logger.warning("Speech synthesis failed: %s", _describe(exc))
            if not chunks:
                raise SpeechStreamError(_describe(exc)) from exc
            return  # partial audio already sent; stop cleanly rather than cache it
        self.cache.put(key, b"".join(chunks))

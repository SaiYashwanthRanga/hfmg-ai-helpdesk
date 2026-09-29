"""Speech provider selection, mirroring app/llm/factory.py."""

import logging
from functools import lru_cache

from app.core.config import get_settings
from app.speech.base import SpeechProvider
from app.speech.openai_speech import OpenAISpeechProvider

logger = logging.getLogger("hfmg.speech.factory")

settings = get_settings()

_PROVIDERS = {
    "openai": OpenAISpeechProvider,
}


@lru_cache
def get_speech_provider() -> SpeechProvider:
    name = (settings.speech_provider or "openai").lower()
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        logger.error("Unknown SPEECH_PROVIDER %r; falling back to openai", name)
        provider_cls = OpenAISpeechProvider
    return provider_cls()

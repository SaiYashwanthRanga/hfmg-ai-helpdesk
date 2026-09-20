"""Provider selection.

Adding a provider means writing one module implementing LLMProvider and
registering it here; nothing above this layer changes.
"""

import logging
from functools import lru_cache

from app.core.config import get_settings
from app.llm.base import LLMProvider
from app.llm.openai_provider import OpenAIProvider

logger = logging.getLogger("hfmg.llm.factory")

settings = get_settings()

_PROVIDERS = {
    "openai": OpenAIProvider,
}


@lru_cache
def get_provider() -> LLMProvider:
    name = (settings.llm_provider or "openai").lower()
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        logger.error("Unknown LLM_PROVIDER %r; falling back to openai", name)
        provider_cls = OpenAIProvider
    return provider_cls()

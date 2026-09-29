"""Provider selection.

Adding a provider means writing one module implementing LLMProvider and
registering it here; nothing above this layer changes.
"""

import logging
from functools import lru_cache

from app.core.config import get_settings
from app.llm.base import LLMProvider
from app.llm.fake_provider import FakeProvider
from app.llm.openai_provider import OpenAIProvider

logger = logging.getLogger("hfmg.llm.factory")

settings = get_settings()

_PROVIDERS = {
    "openai": OpenAIProvider,
    # Keyword-rule stand-in for load tests and demos; never in production.
    "fake": FakeProvider,
}


@lru_cache
def get_provider() -> LLMProvider:
    name = (settings.llm_provider or "openai").lower()
    if name == "fake" and settings.environment.strip().lower() in ("production", "prod"):
        logger.error("LLM_PROVIDER=fake is not allowed in production; using openai")
        name = "openai"
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        logger.error("Unknown LLM_PROVIDER %r; falling back to openai", name)
        provider_cls = OpenAIProvider
    return provider_cls()

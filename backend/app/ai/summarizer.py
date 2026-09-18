import logging
import uuid

from app.core.config import get_settings
from app.db.base import async_session_factory
from app.services.ticket_service import get_ticket, set_ai_summary

logger = logging.getLogger("hfmg.ai.summarizer")

settings = get_settings()

_PROMPT_TEMPLATE = """You are triaging an IT help desk ticket for a medical group. \
Write a concise (2-3 sentence) summary for an IT agent: what's broken, likely \
impact, and any obvious next step. Do not restate the raw fields verbatim.

Category: {category}
Priority: {priority}
Description:
{description}
"""


async def generate_summary_for_ticket(ticket_id: uuid.UUID) -> None:
    """Best-effort AI summary generation, run as a FastAPI BackgroundTask.

    A no-op unless ENABLE_AI_SUMMARY=true and an API key is configured —
    the ticket workflow never depends on this succeeding or even running.
    """
    if not settings.enable_ai_summary:
        return
    if not settings.anthropic_api_key:
        logger.warning("ENABLE_AI_SUMMARY is true but ANTHROPIC_API_KEY is not set; skipping")
        return

    async with async_session_factory() as db:
        ticket = await get_ticket(db, ticket_id)
        prompt = _PROMPT_TEMPLATE.format(
            category=ticket.category.name,
            priority=ticket.priority.value,
            description=ticket.description,
        )

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            response = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = "".join(block.text for block in response.content if block.type == "text").strip()
            await set_ai_summary(db, ticket_id, summary=summary, failed=not summary)
        except Exception:
            logger.exception("AI summary generation failed for ticket %s", ticket_id)
            await set_ai_summary(db, ticket_id, summary=None, failed=True)

"""AI Insights aggregation (WIREFRAMES.md §11, BACKEND_GAP_ANALYSIS.md Tier 4).

Only Category Breakdown ships with real data -- it has one obvious,
uncontested definition (ticket count per category over a window), already
built for Tier 3's /analytics/tickets-by-category and reused here rather
than duplicated.

Every other section WIREFRAMES.md names for this page is deliberately left
unbuilt, per instruction not to fabricate a business definition:

- "Trending Issues" vs. "Most Common Problems": WIREFRAMES.md §11 says
  outright that these "sound identical without a stated distinction" and
  guesses at one ("time-windowed spike vs. all-time frequency, presumably").
  A guess is not a decision -- shipping a "trending" claim built on an
  unapproved spike/threshold definition risks presenting fabricated
  significance as fact on an executive-facing page.
- "Repeated Problems" (repeat callers): structurally blocked. No
  caller-identity concept exists anywhere in the schema -- tickets match by
  phone number informally only (VOICE_AGENT_DESIGN.md §8's noted limitation).
- "High Risk Alerts": needs a "normal rate" baseline to compare against,
  which doesn't exist and can't be derived without a product-defined
  threshold.
- "AI Recommendations": no defined output shape -- "recommend what, to
  whom" is an open product question (WIREFRAMES.md §11), not yet an
  engineering task.

See REMAINING_PRODUCT_DECISIONS.md for the decisions that would unblock each.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.analytics_service import get_tickets_by_category

_TRENDING_BLOCKED_REASON = (
    '"Trending Issues" and "Most Common Problems" are not distinguished by any '
    "decision on record (WIREFRAMES.md §11) -- building either risks asserting "
    "a spike/threshold definition nobody has approved."
)
_REPEATED_PROBLEMS_BLOCKED_REASON = (
    "No caller-identity concept exists in the schema -- tickets match by phone "
    "number informally only (VOICE_AGENT_DESIGN.md §8)."
)
_HIGH_RISK_ALERTS_BLOCKED_REASON = (
    'No defined "normal rate" baseline exists to compare against -- an alert '
    "threshold picked here would be fabricated, not derived."
)
_RECOMMENDATIONS_BLOCKED_REASON = (
    'No defined output shape -- "recommend what, to whom" is an open product '
    "question (WIREFRAMES.md §11), not yet an engineering task."
)


async def get_ai_insights(db: AsyncSession, *, days: int) -> dict:
    category_items = await get_tickets_by_category(db, days=days)
    return {
        "category_breakdown": {"items": category_items},
        "trending_issues": {"blocked_reason": _TRENDING_BLOCKED_REASON},
        "repeated_problems": {"blocked_reason": _REPEATED_PROBLEMS_BLOCKED_REASON},
        "high_risk_alerts": {"blocked_reason": _HIGH_RISK_ALERTS_BLOCKED_REASON},
        "recommendations": {"blocked_reason": _RECOMMENDATIONS_BLOCKED_REASON},
    }

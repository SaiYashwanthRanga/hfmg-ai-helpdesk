# HFMG AI Help Desk — Remaining Product Decisions

**Purpose:** every place backend implementation stopped specifically because completing it would have meant inventing a business definition nobody has approved. Each entry names the decision needed, why it matters, and exactly what unblocks once it's made. Nothing here is a backend task — these are all product/business calls.

---

## 1. "AI Resolution Rate" definition

**Where it's blocked:** `GET /analytics/kpis` (`ai_resolution_rate` field), `GET /analytics/*` (no equivalent chart endpoint built).

**The problem:** `DESIGN.md` §20 names three candidate definitions that produce different numbers and imply different things to the executive reading the Dashboard:
1. "Tickets closed without human escalation" (a containment metric)
2. "Tickets where the AI summary was used" (an adoption metric, not a resolution metric at all)
3. "Voice calls that completed without escalation" (a voice-channel-only metric, ignores web tickets entirely)

These aren't three ways of phrasing the same idea — they measure different things and would mislead if presented under one ambiguous label.

**Decision needed:** pick one (or name a fourth), and confirm whether it should be ticket-scoped, call-scoped, or both reported separately.

**Unblocks:** one field in `/analytics/kpis`, plus a corresponding chart if one is wanted on the Analytics page. Both are a single, small query once the definition exists — the backend work itself is trivial; the decision is the entire blocker.

---

## 2. "Trending Issues" vs. "Most Common Problems"

**Where it's blocked:** `GET /ai-insights` (`trending_issues` field — `repeated_problems`/`high_risk_alerts`/`recommendations` are separately blocked, see below).

**The problem:** `WIREFRAMES.md` §11 states outright: *"'Trending Issues' and 'Most Common Problems' sound identical without a stated distinction (time-windowed spike vs. all-time frequency, presumably...)"* — even the document proposing these features flags them as underspecified, and hedges its own guess with "presumably." A guess is not a decision.

**Decision needed:**
- Confirm "Most Common Problems" = ranked category frequency (this would just be `category_breakdown`, already built and returning real data under that name — if this is the intended meaning, "Most Common Problems" may not need to be a separate concept at all).
- If "Trending Issues" means something distinct (a spike detector), define: the comparison window (e.g. this week vs. last week), the significance threshold (what counts as "trending" — 20% increase? a fixed count delta? statistical significance?), and the minimum sample size below which a category shouldn't be flagged as trending (a category going from 1 ticket to 2 is a 100% increase and statistically meaningless).

**Unblocks:** a real `trending_issues` section on the AI Insights page. `category_breakdown` already ships today and may already satisfy "Most Common Problems" once that's confirmed.

---

## 3. Caller-identity concept (for "Repeated Problems" / repeat callers)

**Where it's blocked:** `GET /ai-insights` (`repeated_problems` field).

**The problem:** this one is structural, not just a missing threshold. No caller-identity concept exists anywhere in the schema — tickets match by phone number informally, with no deduplication, normalization, or linking (`VOICE_AGENT_DESIGN.md` §8's own documented limitation: "Repeat caller / duplicate ticket — Not deduplicated in v1. A human sees two tickets from one number.").

**Decision needed:** should HFMG invest in a caller-identity concept at all (a `callers` table keyed by normalized phone number, linking tickets across calls)? If yes, what counts as "the same caller" — exact phone number match, or something more forgiving (e.g. matching on name + extension)?

**Unblocks:** "Repeated Problems," and as a side effect, duplicate-ticket detection generally — this is worth scoping as its own small feature, not just an AI Insights sub-section, if approved.

---

## 4. "Normal rate" baseline (for "High Risk Alerts")

**Where it's blocked:** `GET /ai-insights` (`high_risk_alerts` field).

**The problem:** `WIREFRAMES.md` §8's example — *"3 escalated calls in the last hour — above normal rate"* — requires knowing what "normal" is. There is no historical baseline computed or stored anywhere, and picking an arbitrary threshold (e.g. "more than 2 escalations per hour is high risk") would be presenting a fabricated judgment as a system-derived fact.

**Decision needed:** what baseline should "normal" be measured against (e.g. a trailing 30-day average escalation rate for the same hour-of-day/day-of-week)? What margin above that baseline counts as "high risk" — and who is accountable for tuning that threshold as real traffic patterns emerge?

**Unblocks:** "High Risk Alerts." This is the most technically involved of the five decisions once made — it likely needs a rolling baseline computation, not just a single query.

---

## 5. AI Recommendations' output shape

**Where it's blocked:** `GET /ai-insights` (`recommendations` field).

**The problem:** `WIREFRAMES.md` §11 names this feature without specifying "recommendations to whom, about what" — is it a recommendation to IT leadership ("staff up the eClinicalWorks support queue"), to an individual agent ("this ticket looks like three others closed last week — here's how they were resolved"), or something else? Without an audience and a recommendation *type*, there is no defensible output shape to build a schema around.

**Decision needed:** define the audience, the trigger condition (when does a recommendation get generated), and at least one concrete example recommendation end-to-end, before this becomes an engineering task.

**Unblocks:** "AI Recommendations," the least-specified feature in the entire product per `WIREFRAMES.md` §11's own assessment — expect this to be the last of the five decided, and treat that as correct sequencing, not neglect.

---

## Disclosed assumptions (lower stakes — confirm, don't necessarily block on)

These shipped with a reasonable default rather than being left blocked, because the ambiguity is a narrow scope/window question, not a fundamentally contested definition. Listed here so they get product review rather than becoming permanent by default:

| Metric | Assumption shipped | Confirm |
|---|---|---|
| `calls_today` / `escalations` (`/analytics/kpis`) | Day-scoped (today only, UTC) | Should "Escalations" instead mean all-time, or currently-active escalations? |
| `escalation-rate` (`/analytics/escalation-rate`) | `escalated / terminal calls` (excludes in-progress calls) | Should the denominator instead be *all* calls including in-progress ones? |

See `DOCS_GAP_REPORT.md`'s "Disclosed assumptions" section for the full reasoning.

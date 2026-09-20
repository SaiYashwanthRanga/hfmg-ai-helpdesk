# HFMG AI Help Desk — Known Limitations

Written for stakeholders and end users, not engineers — what you'll notice, and why it's this way on purpose. See `TECHNICAL_DEBT.md` for the engineering-facing equivalent and `REMAINING_PRODUCT_DECISIONS.md` for anything here that's blocked on a business decision rather than a deliberate scope choice.

---

## Security & Access

- **There is no login.** Anyone who can reach the application on the network can view and act on every ticket, call, and (read-only) configuration status. This system is safe to run only on a trusted internal network, never exposed to the public internet, until authentication ships (a planned future phase).
- **Settings cannot be edited from the app.** You can see whether OpenAI, Twilio, and email are configured and working, but changing any of those requires editing server configuration directly. This is deliberate: editing live credentials with no login in front of the page would be a security hole, not a convenience.

## Voice & Calls

- **Calls are never recorded.** There is no audio playback anywhere in the product. Every call is captured as a text transcript instead — this was a deliberate compliance decision (consent requirements for recording weren't in place), not a missing feature.
- **The system doesn't recognize repeat callers.** If the same person calls twice, two separate, unlinked tickets are created. There's no "this caller has called before" detection yet.
- **There's no live view of an in-progress call.** You can see a call's full transcript and outcome after it updates, but there isn't yet a screen that shows a call unfolding turn-by-turn as it happens.
- **English only.** The voice agent doesn't support other languages; a non-English caller will be escalated to a callback after a few failed exchanges.

## AI Features

- **"AI Resolution Rate" isn't shown anywhere.** There are three different reasonable ways to define this number (tickets closed without escalation? tickets where AI summaries helped? calls that didn't need a human?) and picking one without confirming it with the team would risk showing you a number that means something different than you'd assume. It's intentionally left blank rather than guessed at.
- **The AI Insights page mostly shows "not yet available."** Only the category breakdown (which categories generate the most tickets) is real today. Trending issues, repeat-problem detection, risk alerts, and AI recommendations are all named on the page but not yet built — each needs a business decision about exactly what it should mean before it can be built honestly (see `REMAINING_PRODUCT_DECISIONS.md`).
- **AI never diagnoses or troubleshoots.** By design, the voice agent's job is intake only — it collects and routes, it doesn't suggest fixes.

## Tickets & Workflow

- **No SLA tracking or due dates.** Priority is tracked, but there's no "this ticket is overdue" indicator.
- **No ticket assignment.** Tickets aren't assigned to a specific staff member — there's no concept of "my queue" yet, since there's no login to have a "my" in the first place.
- **No comment thread on tickets.** Notes and back-and-forth with a caller aren't tracked in the app.
- **Search is a simple text match**, not a smart or fuzzy search — it looks for your exact search term appearing in the caller's name, ticket number, description, or AI summary.

## Analytics

- **Two numbers on the Analytics page (Calls Today, Escalation Rate) use a reasonable but not-yet-formally-confirmed definition** of their time window. The numbers are real and computed correctly against that definition; if the exact scope (e.g. "today" vs. "all time") matters for a decision you're making, double-check with the team first.

## General

- **This is a desktop-first tool.** It's fully usable on a tablet and works on a phone, but it wasn't designed mobile-first — expect a desk-based experience to feel the most natural.
- **No data export.** There's no CSV/PDF export of tickets or analytics yet.

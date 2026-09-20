# HFMG AI Help Desk — Wireframes

**Version:** 1.0
**Status:** Design Phase (companion to `DESIGN.md`)
**Audience:** Frontend engineers implementing the React UI

---

## 0. How to Read This Document

This is not a generic admin-panel spec. HFMG AI Help Desk is an **AI Operations Center**: a voice agent answers calls, an LLM classifies and summarizes, and IT staff triage from what the AI produced. Every wireframe below is built around that framing, not around "a table with a form."

**Source of truth:** `DESIGN.md` §3 is the authoritative build-status table. Every element in every wireframe here carries one of three tags, inherited directly from that table — nothing here upgrades or downgrades a status DESIGN.md already assigned:

| Tag | Meaning |
|---|---|
| ✅ **Built** | Backend endpoint and data exist today, exactly as drawn |
| 🔶 **Partial** | Some of it exists (data, or endpoint, or UI) but not all three |
| ⬜ **Vision** | Nothing exists yet — endpoint, data, and UI are all future work |

Per-element tags appear inline in each wireframe (`[✅]`, `[🔶]`, `[⬜]`) next to the region they describe, so a developer can tell at a glance which parts of a box are real today.

**Status note (final pre-production review pass):** the inline `[✅]`/`[🔶]`/`[⬜]` tags throughout this document were written at design time, before Frontend Phases 1–8 and Backend Tiers 0–5 shipped. They are now a **historical map of what was built in what order**, not a live status board — most `[⬜]` tags below (Dashboard, Calls, Analytics, System Status) are now ✅ Built, and several `[🔶]` tags (Tickets filters/search) are now fully ✅. **`DESIGN.md` §3 is the current, updated status table** — treat it as authoritative over any inline tag here that disagrees with it. The wireframe *layouts* themselves remain an accurate description of what was built; only the per-element status tags have drifted. See `DOCUMENTATION_AUDIT.md` for the full accounting.

**Hard constraints this document will not violate**, because they are architecture decisions made elsewhere in this repo, not UI preferences:
- **No call recording exists or is planned for MVP.** `TWILIO_ARCHITECTURE.md` §9 keeps recording off pending compliance sign-off. No wireframe below shows a play/pause/scrub audio control. Ever. Transcripts (text) are the only call record.
- **Settings has no write path.** `DESIGN.md` §12 / `IMPLEMENTATION_PLAN.md` MVP Scope Decision: there is no authentication in front of this system today, so a Settings page that can edit `OPENAI_API_KEY` / `TWILIO_AUTH_TOKEN` / SendGrid keys is a live vulnerability, not a rough edge. Settings is read-only until Phase 3 auth ships.
- **No live audio, no WebSocket push exists today.** The voice agent is a synchronous Twilio webhook state machine (`CALL_FLOW.md`) — state changes happen once per conversational turn, persisted to Postgres, not streamed. "Live Call Monitoring" in this document means **short-interval polling of `voice_call_sessions` rows**, not a live audio feed or push socket. Anything drawn as instant/streaming is marked ⬜.
- **Transcript today lives inside `tickets.description`** for phone-sourced tickets (appended by `app/voice/orchestrator.py`), not in a separate column. `voice_call_sessions.turns` (JSONB) *is* the real structured transcript, but no API exposes it yet. Every transcript-rendering wireframe element is tagged accordingly.
- **No aggregation/analytics endpoints exist.** `GET /api/v1/health` is liveness-only. Every KPI, chart, and status light in this document that isn't a direct list/detail call is new backend work, called out explicitly.

---

## 1. Global Shell

**Status:** ⬜ Vision — current frontend (`TicketListPage`, `TicketDetailPage`, `NewTicketPage`) has a single-line header, no sidebar, no design system. This shell does not exist; everything below assumes it's built first (per `DESIGN.md` §18, step 1).

```
┌────────────────────────────────────────────────────────────────────────┐
│ ● HFMG AI Help Desk        [env: production]     🟢 OpenAI 🟢 Twilio  │  Header [⬜]
│                                                    🟢 DB    🟢 Email   │  System Status strip [⬜]
├───────────────┬──────────────────────────────────────────────────────┤
│  Dashboard    ●│                                                      │
│  Tickets       │                                                      │
│  Calls         │              Main Content (scrolls)                 │
│  Analytics     │                                                      │
│  AI Insights   │                                                      │
│  Settings      │                                                      │
│               │                                                      │
└───────────────┴──────────────────────────────────────────────────────┘
```

- **Purpose:** persistent orientation — which page, whether the system is healthy, which environment.
- **Primary user:** everyone (IT staff, Jim/executive).
- **Business value:** prevents the two most common internal-tool mistakes: not knowing which environment you're looking at, and not knowing something's broken until a ticket queue backs up.
- **Data dependencies:** System Status strip needs a new aggregation of OpenAI/Twilio/DB/Email reachability (§9 below) — none exists today.
- **Backend endpoints required:** `GET /api/v1/health` ✅ exists (liveness only); **new** `GET /api/v1/health/dependencies` ⬜ needed for the four-way status strip.
- **Components:** `<AppShell>`, `<Sidebar>`, `<Header>`, `<SystemStatusStrip>`, `<EnvBadge>`, `<NavItem active>`.

---

## 2. Executive Dashboard

**Status:** ⬜ Vision — page does not exist; no aggregation endpoints exist.

- **Purpose:** the first screen Jim (or any exec) sees. Must answer "is the system healthy, is AI working, is anything urgent" in under 10 seconds — no legend, no click required (`DESIGN.md` §2.2).
- **Primary user:** executive / operations lead (non-technical, time-boxed attention).
- **Business value:** replaces "someone emails Jim that phones are down" with a self-serve status check; builds trust in the AI layer by making its activity visible, not hidden.
- **Data dependencies:** `tickets` (✅ exists) for ticket KPIs; `voice_call_sessions` (✅ exists, no API) for call KPIs; a **new, undefined** "AI Resolution Rate" metric — flagged as an open decision below, do not invent a definition in code before product picks one; a **new** aggregation for "Recent Activity" merging tickets + calls; a **new** "AI Insights" preview requiring the AI Insights aggregation (§8) to exist first.
- **Backend endpoints required:**
  | Endpoint | Status | Notes |
  |---|---|---|
  | `GET /api/v1/tickets?page_size=1&status=...` | ✅ exists | Open Tickets / Tickets Today via count |
  | `GET /api/v1/health/dependencies` | ⬜ new | System Status Bar |
  | `GET /api/v1/analytics/kpis` | ⬜ new | Open Tickets, Tickets Today, Calls Today, Escalations, AI Resolution Rate in one call |
  | `GET /api/v1/voice-calls?page_size=1` | ⬜ new (§5) | Calls Today, Active Calls |
  | `GET /api/v1/analytics/recent-activity` | ⬜ new | Merged ticket+call feed |
  | `GET /api/v1/ai-insights?limit=3` | ⬜ new (§8) | Dashboard's AI Insights preview |

```
┌────────────────────────────────────────────────────────────────────────┐
│ System Health                                                    [⬜]  │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐                   │
│ │ 🟢 OpenAI │ │ 🟢 Twilio │ │ 🟢 DB     │ │ 🟢 Email  │  last checked: │
│ │ operational│ │ operational│ │ operational│ │ operational│ 12s ago     │
│ └──────────┘ └──────────┘ └──────────┘ └──────────┘                   │
├────────────────────────────────────────────────────────────────────────┤
│ KPI Cards                                                        [⬜]  │
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────────┐        │
│ │ Open   │ │Tickets │ │ Calls  │ │Escala- │ │ AI Resolution   │        │
│ │Tickets │ │ Today  │ │ Today  │ │ tions  │ │ Rate            │        │
│ │  [🔶]  │ │  [🔶]  │ │  [⬜]  │ │  [⬜]  │ │   — [⬜]        │        │
│ │  42    │ │  7     │ │  3     │ │  1     │ │ definition TBD  │        │
│ └────────┘ └────────┘ └────────┘ └────────┘ └────────────────┘        │
├────────────────────────────────┬───────────────────────────────────────┤
│ Recent Activity            [⬜] │ AI Insights Preview             [⬜]  │
│ ─────────────────────────────  │ ─────────────────────────────────────│
│ 🎫 HFMG-2026-000482 · NEW      │ Top issue today:                     │
│    Maria Lopez · EHR · 2m ago  │  "eClinicalWorks login failures" (5) │
│ ☎ Call ended · escalated       │                                       │
│    +1 845-555-0142 · 5m ago    │ [ View full AI Insights → ]          │
│ 🎫 HFMG-2026-000481 · RESOLVED │                                       │
│    ...                         │                                       │
└────────────────────────────────┴───────────────────────────────────────┘
```

- **Components:** `<SystemStatusBar>` [⬜], `<KpiCard>` ×5 [🔶/⬜ per card], `<RecentActivityFeed>` [⬜], `<AiInsightsPreviewCard>` [⬜].
- **KPI honesty note (do not skip this in implementation):** "Open Tickets" and "Tickets Today" are one query away from `tickets` today (🔶 — data ready, endpoint not). "AI Resolution Rate" has **three plausible, mutually exclusive definitions** ("closed without escalation" / "AI summary was used" / "voice call completed without escalation") — `DESIGN.md` §20 flags this as an open decision. Render this card as `—` with a tooltip "Definition pending" until product decides; do not silently pick one in code.

---

## 3. Ticket Operations Screen

**Status:** 🔶 Partial — the most-built page today. List + status filter exist; this is additive work, not a rebuild.

- **Purpose:** where IT staff triage the queue all day.
- **Primary user:** IT agent.
- **Business value:** faster triage = shorter time-to-resolution; an AI summary preview lets an agent skip re-reading a caller's raw rambling.
- **Data dependencies:** `tickets` table ✅ fully has category/priority/status/source/ai_summary. Full-text index (`ix_tickets_fts`) ✅ already exists in schema, unused by the API.
- **Backend endpoints required:**
  | Capability | Status |
  |---|---|
  | `GET /api/v1/tickets?status=` | ✅ exists |
  | `GET /api/v1/tickets?q=` (search) | 🔶 schema index exists, endpoint param doesn't |
  | `GET /api/v1/tickets?priority=&category_id=&source=` | 🔶 columns exist, query params don't |
  | AI Summary excerpt in list response | 🔶 open decision — `TicketListItem` deliberately omits `ai_summary` today (`API_SPEC.md` §3); needs a decision to widen the schema or truncate server-side before this column ships |

```
┌────────────────────────────────────────────────────────────────────────┐
│ Tickets                                            [+ New Ticket] [✅]  │
│ ┌──────────────────────────────┐ [Status ▾][Priority ▾][Category ▾]   │
│ │ 🔍 Search tickets...     [🔶]│ [Source ▾]                    [🔶]  │
│ └──────────────────────────────┘                                      │
├────────────────────────────────────────────────────────────────────────┤
│ #             Caller       Category    AI Summary          Src Pri St │
│ HFMG-26-000482 Maria Lopez EHR/eCW     "Session expires on  📞 🔴 🟡 │
│                             [✅]         all exam-room..."[🔶]HIGH NEW│
│ ─────────────────────────────────────────────────────────────────────│
│ HFMG-26-000481 J. Chen     Network     "VPN drops after     🌐 🟠 🟢 │
│                                          ~10 min idle"       MED RESOLVED│
│ ─────────────────────────────────────────────────────────────────────│
│ HFMG-26-000480 Unknown     Password    "Locked out, MFA     📞 🔴 🟡 │
│                caller (voice escalation) code not arriving" URGENT OPEN│
└────────────────────────────────────────────────────────────────────────┘
   Src legend: 📞 PHONE · 🌐 WEB      Row click → Ticket Intelligence Drawer (§4)
```

- **Components:** `<TicketSearchBar>` [🔶], `<TicketFilterBar status priority category source>` [🔶 mixed], `<TicketTable>` [✅ base], `<SourceBadge>` [✅ — `source` column exists], `<PriorityBadge>` [✅], `<StatusBadge>` [✅], `<AiSummaryExcerpt>` [🔶 pending schema decision].
- **Interaction change from today:** row click opens a slide-over drawer (§4), not a route navigation. `TicketDetailPage.tsx` is currently a full page — this is the single biggest UX change on this page, per `DESIGN.md` §7, and should be prioritized over polish elsewhere on this screen.

---

## 4. Ticket Intelligence Drawer

**Status:** 🔶 Partial — all underlying data exists; today it renders as a full page (`TicketDetailPage.tsx`), not a drawer, and the transcript is not a separate field.

- **Purpose:** everything an agent needs to act on one ticket, without losing their place in the list.
- **Primary user:** IT agent.
- **Business value:** this is the core "AI-assisted triage" moment — AI Summary + Voice Transcript let an agent decide priority/ownership in seconds instead of reading a full call transcript top to bottom.
- **Data dependencies:** `Ticket` model ✅ has caller info, category/priority/status, description, ai_summary/ai_summary_status. Transcript ⬜/🔶 — see note below. Activity timeline needs `audit_log` (⬜ Phase 3, table not in MVP schema).
- **Backend endpoints required:**
  | Capability | Status |
  |---|---|
  | `GET /api/v1/tickets/{id}` | ✅ exists |
  | `POST /api/v1/tickets/{id}/status` | ✅ exists |
  | `POST /api/v1/tickets/{id}/regenerate-summary` | ✅ documented in API_SPEC, verify built |
  | Voice transcript as a rendered section | 🔶 — see below |
  | Activity Timeline (assignment/comments/audit) | ⬜ — `audit_log`/`ticket_comments` are Phase 3 tables, not in MVP schema |

```
                                          ┌─────────────────────────────┐
                                          │ HFMG-2026-000482        [x] │
                                          │ EHR/eClinicalWorks · HIGH   │
                                          ├─────────────────────────────┤
                                          │ Caller Information     [✅] │
                                          │  Maria Lopez                │
                                          │  📞 +1 845-555-0142          │
                                          │  ✉ mlopez@hfmg.net          │
                                          ├─────────────────────────────┤
                                          │ Issue Information       [✅]│
                                          │  Category: EHR/eCW           │
                                          │  Priority: HIGH  [change ▾] │
                                          │  Source: 📞 PHONE            │
                                          │  Created: Sep 18, 2:00pm     │
                                          ├─────────────────────────────┤
                                          │ AI Summary               [✅]│
                                          │  Status: COMPLETED           │
                                          │  "Multiple exam-room          │
                                          │  workstations losing eCW      │
                                          │  session after ~30s idle."    │
                                          │  [Regenerate summary]         │
                                          ├─────────────────────────────┤
                                          │ Voice Transcript          [🔶]│
                                          │  ⚠ Currently embedded in      │
                                          │  description text, not a      │
                                          │  separate field — see note.   │
                                          │  Agent: How can I help...     │
                                          │  Caller: none of us can...    │
                                          ├─────────────────────────────┤
                                          │ Activity Timeline         [⬜]│
                                          │  (needs audit_log — Phase 3)  │
                                          │  ○ Created · voice intake      │
                                          │  ○ Status → OPEN               │
                                          ├─────────────────────────────┤
                                          │ Status Controls           [✅]│
                                          │  [NEW][OPEN][IN_PROGRESS]     │
                                          │  [ON_HOLD][RESOLVED][CLOSED]  │
                                          ├─────────────────────────────┤
                                          │ AI Analysis                [🔶]│
                                          │  Model: gpt-5-nano             │
                                          │  Generated: Sep 18, 2:01pm     │
                                          └─────────────────────────────┘
```

- **Transcript decision, spelled out for the implementer:** today the voice orchestrator (`app/voice/orchestrator.py`, `_create_ticket`) appends the call transcript directly into `tickets.description` with a `--- Call transcript ---` marker. Rendering it as its own drawer section either means (a) parsing it back out of `description` in the frontend — fragile, not recommended — or (b) a migration adding `tickets.transcript` (nullable, phone-only) populated at write time — recommended, per `DESIGN.md` §8 and §20. **Do not build the frontend parser.** Build the drawer section to read a `transcript` field and treat its absence as "not a phone ticket," and let the migration land before this section goes live for phone tickets. Until the migration ships, this section should render `description` verbatim (labeled "Description," not "Transcript") — do not fake a transcript section.
- **Components:** `<TicketDrawer>` [🔶 — page→drawer conversion], `<CallerInfoCard>` [✅], `<IssueInfoCard>` [✅], `<AiSummaryPanel status=PENDING|COMPLETED|FAILED|DISABLED>` [✅ — carries over `AI_SUMMARY_COPY` handling unchanged], `<TranscriptPanel>` [🔶], `<ActivityTimeline>` [⬜], `<StatusControl>` [✅], `<AiAnalysisMeta>` [🔶 — `ai_model` column documented but not implemented in MVP schema per `DATABASE_DESIGN.md` §3.1].

---

## 5. Voice Operations Center

**Status:** 🔶 Partial (data) / ⬜ (page, API) — `voice_call_sessions` already stores everything this page needs; no endpoint exposes it, no page renders it. This is real, scoped backend work, not a frontend-only page.

- **Purpose:** operational home for the phone channel — what's happening on the phones right now and historically.
- **Primary user:** IT operations lead monitoring call volume/quality; IT agent following up on escalated calls.
- **Business value:** the voice agent is a new, unfamiliar channel — this page is what builds staff trust that it's working correctly, and where escalations get triaged first (they're already HIGH+ priority by design, per `CALL_FLOW.md` §6).
- **Data dependencies:** `voice_call_sessions` ✅ (`state`, `collected`, `turns`, `escalated`, `escalation_reason`, `ticket_id`, `ended_at`). "Live Agent Status" as drawn in the original brief (an operator's live queue/staffing view) has **no backing concept anywhere in the schema** — there is no human-agent-availability table. That box is relabeled below to what's actually buildable: **AI Agent Status**, meaning whether the voice orchestrator itself is healthy, not human staffing.
- **Backend endpoints required:**
  | Endpoint | Status | Notes |
  |---|---|---|
  | `GET /api/v1/voice-calls` (list, paginated, filter by state/escalated) | ⬜ new | Same pagination shape as `GET /api/v1/tickets` |
  | `GET /api/v1/voice-calls/{id}` (detail incl. `turns`) | ⬜ new | Needed for the transcript preview |
  | `GET /api/v1/voice-calls/summary` (counts by state) | ⬜ new | Active/Completed/Escalated cards |

```
┌────────────────────────────────────────────────────────────────────────┐
│ Voice Operations Center                                                │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────────────┐      │
│ │ Active   │ │Completed │ │Escalated │ │ AI Agent Status   [🔶] │      │
│ │ Calls    │ │  Calls   │ │  Calls   │ │ 🟢 Orchestrator healthy│      │
│ │   1 [🔶] │ │  47 [🔶] │ │   3 [🔶] │ │ Model: gpt-5-nano       │      │
│ └──────────┘ └──────────┘ └──────────┘ └───────────────────────┘      │
├────────────────────────────────────────────────────────────────────────┤
│ Recent Call Sessions                                              [⬜]  │
│ Caller           State       Escalated   Ticket         Duration      │
│ +1 845-555-0142  ANYTHING_   —           HFMG-26-000482 2m 14s        │
│                  ELSE (live)                                          │
│ ─────────────────────────────────────────────────────────────────────│
│ +1 917-555-0199  COMPLETED   —           HFMG-26-000481 1m 48s        │
│ ─────────────────────────────────────────────────────────────────────│
│ anonymous        ESCALATED   CALLER_     HFMG-26-000480 0m 52s        │
│                               REQUESTED                                │
├────────────────────────────────────────────────────────────────────────┤
│ Call Transcript Preview (selected row)                            [🔶] │
│  Agent: Thank you for calling Horizon Family Medical Group...          │
│  Caller: none of us can log into eClinicalWorks this morning...        │
│  Agent: I understand you're having an issue with eClinicalWorks...     │
│  [ Open full transcript in Live Call Monitoring → ]                    │
└────────────────────────────────────────────────────────────────────────┘
   No audio player anywhere on this page — recording is off (TWILIO_ARCHITECTURE.md §9).
```

- **Components:** `<CallCountCard active|completed|escalated>` [🔶], `<AiAgentStatusCard>` [🔶 — orchestrator health, not human staffing], `<CallSessionTable>` [⬜], `<CallStateBadge>` [⬜ — one badge per `voice_call_state_enum` value], `<EscalationReasonBadge>` [⬜], `<TranscriptPreviewPanel>` [🔶 — reads `turns` JSONB].
- **Explicitly excluded, and why:** live audio monitoring and call playback are `DESIGN.md` §19 Future Enhancements, not this page. Call Playback specifically requires reopening the recording-off decision in `TWILIO_ARCHITECTURE.md` §9 (consent language in the greeting, compliance sign-off) — it is a compliance decision wearing a feature-request costume, not a frontend task.

---

## 6. Live Call Monitoring

**Status:** ⬜ Vision, with a caveat: the *data* this page needs already exists per-turn in `voice_call_sessions` (✅), but nothing about this page can be truly "live" without new infrastructure. Read the caveat before building.

- **Purpose:** the single most operationally important screen — what's happening on an in-progress call, right now, and what the AI is deciding.
- **Primary user:** IT ops lead watching for a call about to go wrong; on-call agent deciding whether to intervene by calling the customer back.
- **Business value:** visibility into AI decision-making in real time is what makes staff trust (or catch problems with) an automated phone channel before it becomes a bad caller experience.
- **Data dependencies:** `voice_call_sessions.state`, `.collected` (category/priority-so-far), `.turns` (transcript), `.misunderstanding_count`, `.ticket_id` — all ✅ exist. **Confidence** is available per-turn only inside `turns` JSONB entries (`{role, text, confidence, at}` — `TWILIO_ARCHITECTURE.md` §6) if the orchestrator writes it there; verify this before wiring the UI, since `DATABASE_DESIGN.md` §3.8 documents the column shape but not that every field is populated today. **AI Decision Log** (why the agent chose a category/priority) is not a stored concept anywhere — the model's reasoning is not persisted, only its structured output.
- **Backend endpoints required:**
  | Endpoint | Status | Notes |
  |---|---|---|
  | `GET /api/v1/voice-calls/{id}` (poll target) | ⬜ new | Same endpoint as §5's detail call |
  | Real-time push (WebSocket/SSE) | ⬜ **not architected** | Current backend is a synchronous webhook handler with no persistent-connection story. "Live" here means the frontend polls this endpoint on an interval (2–5s) while a call is `state NOT IN (COMPLETED, ESCALATED, ABANDONED)`. Do not build this as a WebSocket without a separate architecture decision — it is out of scope for the documented backend. |

```
┌────────────────────────────────────────────────────────────────────────┐
│ Live Call Monitoring                          ● polling every 3s [⬜]  │
├───────────────────────────────┬────────────────────────────────────────┤
│ Caller                   [✅] │ Current State                    [✅]  │
│  +1 845-555-0142               │  COLLECT_EMAIL                        │
│  (caller ID present)           │  ────────●───────○───○───○  step 5/9  │
├───────────────────────────────┴────────────────────────────────────────┤
│ Current Transcript (live-appending)                                [✅]│
│  Agent: Thank you for calling Horizon Family Medical Group IT...       │
│  Caller: none of us can log into eClinicalWorks this morning...        │
│  Agent: I understand you're having an issue with eClinicalWorks.       │
│         May I have your name?                                          │
│  Caller: Maria Lopez.                                                  │
│  Agent: Thank you, Maria. What email address should we use...          │
├───────────────────────────────┬────────────────────────────────────────┤
│ Category Prediction      [✅] │ Priority Prediction              [✅]  │
│  eClinicalWorks                │  HIGH                                 │
│  confidence: high         [🔶]│  (multi-user impact detected)   [⬜]  │
├───────────────────────────────┴────────────────────────────────────────┤
│ Ticket Preview (drafted so far)                               [🔶]     │
│  Not yet created — CREATING_TICKET not reached.                        │
│  Caller: Maria Lopez · Category: eClinicalWorks · Priority: HIGH       │
├──────────────────────────────────────────────────────────────────────┤
│ AI Decision Log                                              [⬜]     │
│  ⚠ Not stored anywhere today — model reasoning is not persisted,      │
│  only its structured tool-call output (category/priority/confidence). │
│  This section can show *outputs*, not *why*, until a decision-log      │
│  concept is added to the schema.                                       │
├───────────────────────────────┬────────────────────────────────────────┤
│ Agent Status              [🔶]│ Misunderstanding Count           [✅]  │
│  🟢 In conversation, turn 5    │  0 / 3                                │
└───────────────────────────────┴────────────────────────────────────────┘
```

- **Components:** `<LiveCallHeader polling>` [⬜], `<CallStateProgress>` [✅ — maps `voice_call_state_enum` to a step list from `CALL_FLOW.md` §2], `<LiveTranscriptFeed>` [✅ data / ⬜ live-append mechanism], `<CategoryPredictionBadge confidence>` [🔶 — confidence field needs verification], `<PriorityPredictionBadge>` [✅ value / ⬜ "why" phrase], `<TicketDraftPreview>` [🔶], `<AiDecisionLog>` [⬜ — do not build against a field that doesn't exist; render as "not available" rather than fabricating reasoning text], `<MisunderstandingCounter max=3>` [✅].
- **Honesty note for whoever builds this:** this page can be built entirely from data that already exists in Postgres — it does not need new database work, only (a) the missing list/detail API and (b) an explicit decision to poll rather than push. Do not let the word "Live" in the page name imply a WebSocket exists; it does not, and building one is a separate architectural proposal outside this document's scope.

---

## 7. Analytics

**Status:** ⬜ Vision — no aggregation endpoints exist. The SQL in `OPERATIONS_RUNBOOK.md` §3.3 computes some of these today, but only as ad hoc queries an operator runs by hand.

- **Purpose:** trended operational metrics — the difference from the Dashboard is time-series depth vs. at-a-glance status.
- **Primary user:** IT operations lead reviewing weekly/monthly trends.
- **Business value:** surfaces whether ticket volume/category mix is shifting before it becomes a staffing problem; escalation rate is the single number that indicates whether the voice agent is serving callers well.
- **Data dependencies:** all charts are aggregations over `tickets` and `voice_call_sessions` — ✅ both tables exist, no new tables needed. **Do not invent unsupported KPIs** — every chart below is derivable from columns that exist today.
- **Backend endpoints required:** `GET /api/v1/analytics/tickets-by-category`, `.../tickets-by-priority`, `.../tickets-by-source`, `.../calls-by-day`, `.../escalation-rate`, `.../ai-summary-usage` — all ⬜ new, all pre-aggregated server-side (never ship a chart that fetches all tickets and aggregates client-side — `DESIGN.md` §10 is explicit that this will not survive real data volume).

```
┌────────────────────────────────────────────────────────────────────────┐
│ Analytics                                          [Last 30 days ▾]    │
├───────────────────────────────┬────────────────────────────────────────┤
│ Tickets by Category      [⬜] │ Tickets by Priority              [⬜]  │
│  █████ eCW           38%      │  ██ LOW        12%                    │
│  ███ Network         22%      │  █████ MEDIUM  41%                    │
│  ██ Password         18%      │  ███ HIGH      31%                    │
│  ██ M365             14%      │  █ URGENT      16%                    │
│  █ Other              8%      │                                        │
├───────────────────────────────┼────────────────────────────────────────┤
│ Tickets by Source        [⬜] │ Calls by Day                     [⬜]  │
│  🌐 WEB    62%                │  ▁▃▅▇▆▃▂ (7-day sparkline)             │
│  📞 PHONE  38%                │                                        │
├───────────────────────────────┴────────────────────────────────────────┤
│ Escalation Rate                                                  [⬜]  │
│  6.4% of calls escalated (target: low — spike means agent is failing)  │
├────────────────────────────────────────────────────────────────────────┤
│ AI Summary Usage                                                  [⬜] │
│  COMPLETED 88%  ·  PENDING 3%  ·  FAILED 2%  ·  DISABLED 7%            │
└────────────────────────────────────────────────────────────────────────┘
```

- **Components:** `<DateRangePicker>` [⬜], `<CategoryBreakdownChart>` [⬜], `<PriorityBreakdownChart>` [⬜], `<SourceBreakdownChart>` [⬜], `<CallsPerDaySparkline>` [⬜], `<EscalationRateStat>` [⬜], `<AiSummaryUsageBar>` [⬜ — buckets by `ai_summary_status`, a column that exists ✅].
- **Note:** "AI Resolution Rate" (from the Dashboard) does *not* appear here as a distinct chart until its definition is resolved (§20 open decision, shared with §2 above) — do not duplicate an undefined metric across two pages.

---

## 8. AI Insights

**Status:** ⬜ Vision — no endpoint, no aggregation, no stored "insight" concept anywhere in the schema. `DESIGN.md` §11 explicitly recommends building this page last.

- **Purpose:** the AI intelligence layer — patterns a human triaging one ticket at a time wouldn't notice.
- **Primary user:** IT operations lead / executive looking for systemic issues, not individual tickets.
- **Business value:** turns a pile of individual tickets into "here's what's actually breaking" — the difference between reactive support and proactive fixes.
- **Data dependencies:** Trending Issues / Most Common Categories are aggregations over `tickets.category_id` + `created_at` — ✅ data exists. Repeated Callers needs a caller-identity concept the schema doesn't have; tickets are matched by phone number informally today, and `VOICE_AGENT_DESIGN.md` §8 notes duplicate-caller detection isn't built. AI Recommendations has no defined output shape yet — "recommendations to whom, about what" is an open product question, not an engineering one.

```
┌────────────────────────────────────────────────────────────────────────┐
│ AI Insights                                                             │
├────────────────────────────────────────────────────────────────────────┤
│ Trending Issues (this week vs. last)                              [⬜] │
│  ▲ eClinicalWorks login failures    +180% (5 → 14 tickets)             │
│  ▲ Printer jams, 3rd floor           +40%                              │
├────────────────────────────────────────────────────────────────────────┤
│ Most Common Categories (all-time)                                 [⬜] │
│  1. eClinicalWorks   2. Password   3. Network                          │
├────────────────────────────────────────────────────────────────────────┤
│ Repeated Problems                                                  [⬜] │
│  ⚠ No caller-identity concept exists — tickets match by phone number   │
│  informally only. This section needs a schema decision before it's     │
│  more than a name (VOICE_AGENT_DESIGN.md §8).                          │
├────────────────────────────────────────────────────────────────────────┤
│ High Risk Alerts                                                   [⬜] │
│  🔴 3 escalated calls in the last hour — above normal rate              │
├────────────────────────────────────────────────────────────────────────┤
│ AI Recommendations                                                 [⬜] │
│  ⚠ No defined output shape yet — "recommend what, to whom" is an open  │
│  product decision (DESIGN.md §11), not implemented as a placeholder.   │
├────────────────────────────────────────────────────────────────────────┤
│ ⬜ VISION-ONLY: AI Copilot — conversational Q&A over this data.         │
│    Not scoped. Requires the aggregations above to exist first.         │
└────────────────────────────────────────────────────────────────────────┘
```

- **Components:** `<TrendingIssuesList>` [⬜], `<CategoryRankList>` [⬜], `<RepeatedProblemsPanel blocked>` [⬜ — render a "needs caller-identity design" state, not fabricated data], `<HighRiskAlertBanner>` [⬜], `<AiRecommendationsPanel blocked>` [⬜], `<CopilotVisionCallout>` [⬜ vision-only, clearly labeled, no functional entry point].
- **Build-order note:** per `DESIGN.md` §11's own recommendation, this page should be the last one implemented — every other page in this document has a concrete data source today; this one requires product decisions before it's more than a name.

---

## 9. Settings (Read-Only)

**Status:** ⬜ Vision, **and blocked on a security decision, not a scheduling one.** `DESIGN.md` §12: a Settings page that can view or edit `OPENAI_API_KEY` / `TWILIO_AUTH_TOKEN` / `SENDGRID_API_KEY` with no authentication in front of it is a live vulnerability the moment it exists (`DEPLOYMENT_GUIDE.md` §6: network-ACL-protected only). **This page must never render an edit control, a save button, or an unmasked secret, under any circumstance, until Phase 3 auth ships.**

- **Purpose:** visibility into what's configured, for debugging and confidence — not configuration management.
- **Primary user:** IT agent/lead diagnosing "why isn't AI summarizing" or "is email actually configured."
- **Business value:** turns "is the OpenAI key even set?" from a SSH-into-the-server question into a page load.
- **Data dependencies:** provider configuration status is derivable from environment/config state (`app/core/config.py`) — none of it should ever return the raw secret value, only masked (`sk-...a1b2`) + a reachability check.
- **Backend endpoints required:** `GET /api/v1/settings/status` ⬜ new — returns masked-key + configured/not + last-verified per provider. **No `PATCH`/`POST` endpoint should exist for this resource until Phase 3.**

```
┌────────────────────────────────────────────────────────────────────────┐
│ Settings                                          🔒 Read-only (MVP)   │
├────────────────────────────────────────────────────────────────────────┤
│ OpenAI Configuration                                              [⬜] │
│  Status: 🟢 Configured    Key: sk-...a1b2    Model: gpt-5-nano          │
│  Last verified: 2m ago                                                 │
├────────────────────────────────────────────────────────────────────────┤
│ Twilio Configuration                                               [⬜]│
│  Status: 🟢 Configured    Auth token: ****...9f2c   Number: +1855...   │
├────────────────────────────────────────────────────────────────────────┤
│ Email Provider (SendGrid)                                          [⬜]│
│  Status: 🟢 Configured    Key: SG...a91f    From: helpdesk@hfmg.net     │
├────────────────────────────────────────────────────────────────────────┤
│ Database                                                            [⬜]│
│  Status: 🟢 Connected     Postgres 15.4                                │
├────────────────────────────────────────────────────────────────────────┤
│ Environment                                                         [⬜]│
│  production · ENABLE_AI_SUMMARY=true                                   │
├────────────────────────────────────────────────────────────────────────┤
│ ⬜ VISION (Phase 3+, gated on auth): edit/save controls for the above. │
│    Do not build until JWT + RBAC land (ARCHITECTURE.md §8.2).          │
└────────────────────────────────────────────────────────────────────────┘
```

- **Components:** `<SettingsStatusCard provider readonly>` ×4 [⬜], `<MaskedSecret>` [⬜ — must never render full value, front or backend], `<EnvironmentSummary>` [⬜]. **No `<EditableField>`, no `<SaveButton>` component should exist on this page's component tree at all** — not disabled, not hidden behind a flag. Absence, not disablement, is the safety property here.

---

## 10. Responsive Design

Per `DESIGN.md` §16: this is a desk-first internal operations tool, not a mobile-first product. Optimize desktop → degrade gracefully on tablet → phone width is explicitly out of scope until requested.

### Desktop (≥1280px) — primary target
Full sidebar + content, as drawn in every wireframe above.

### Tablet (768–1279px)
```
┌──────────────────────────────────────┐
│ ☰  HFMG AI Help Desk    🟢🟢🟢🟢     │  Sidebar collapses to icon rail
├──┬────────────────────────────────────┤  or hamburger overlay [⬜]
│▤ │  KPI Cards (2-col wrap)             │
│▤ │  ┌────────┐┌────────┐               │
│▤ │  │        ││        │               │
│▤ │  └────────┘└────────┘               │
│▤ │  Table → horizontally scrollable    │  Tables do NOT reflow columns;
│  │  [◄────────scroll──────────►]       │  they scroll (DESIGN.md §16)
└──┴────────────────────────────────────┘
```

### Mobile (<768px) — out of scope for this pass
Per `DESIGN.md` §16, phone-width support is a distinct future item (`DESIGN.md` §19 "Mobile Application"), not an assumption baked into this design. If accessed on a phone, the app should remain usable (no horizontal page-break, readable text) but is not optimized — no bespoke mobile layouts are specified here. The one required mobile behavior: **the Ticket Drawer becomes full-screen**, not a partial slide-over, on narrow viewports (`DESIGN.md` §16).

---

## 11. Empty States

All empty states use the same visual language: an icon, one sentence of context, and — where actionable — a single primary action. No "0 results" with nothing else.

```
No Tickets                         No Calls                      No Analytics
┌─────────────────────┐            ┌─────────────────────┐       ┌─────────────────────┐
│        🎫            │            │        ☎            │       │        📊            │
│  No tickets yet       │            │  No calls yet        │       │  Not enough data yet │
│  Tickets from the web │            │  Calls will appear   │       │  Analytics populate   │
│  form or voice agent  │            │  here once the       │       │  once there's ticket  │
│  will appear here.    │            │  Twilio number       │       │  and call history to  │
│                       │            │  receives calls.     │       │  aggregate.           │
│  [+ New Ticket]       │            │                       │       │                       │
└─────────────────────┘            └─────────────────────┘       └─────────────────────┘
```

- **No Tickets:** [✅ buildable now] — `GET /api/v1/tickets` returning an empty `items` array is real today.
- **No Calls:** [🔶] — buildable once §5's list endpoint exists; the state itself needs no new backend work.
- **No Analytics:** [⬜] — depends on §7's aggregation endpoints existing; until then, don't render "no data" for a chart whose endpoint doesn't exist — render the whole page as "Analytics is not yet available" instead, so the empty state doesn't imply the feature is live but unused.

---

## 12. Error States

Per `DESIGN.md` §2.3, an error must read as "the system handled this gracefully," not "something broke." Each of the four dependencies gets a distinct, honest message — never a generic "Error 500."

```
OpenAI Offline                      Twilio Offline
┌─────────────────────────┐         ┌─────────────────────────┐
│ 🔴 OpenAI unreachable     │         │ 🔴 Twilio unreachable     │
│ AI summaries are paused.  │         │ Voice intake is affected. │
│ Tickets still work        │         │ Web ticket submission     │
│ normally — summaries will │         │ is unaffected.            │
│ backfill once restored.   │         │                            │
│ (ARCHITECTURE.md §10:     │         │                            │
│  ticket creation never    │         │                            │
│  blocks on AI)            │         │                            │
└─────────────────────────┘         └─────────────────────────┘

Database Offline                    Email Offline
┌─────────────────────────┐         ┌─────────────────────────┐
│ 🔴 Database unreachable   │         │ 🟡 Email delivery paused  │
│ The system cannot serve   │         │ Tickets are being        │
│ any pages right now.      │         │ created normally;        │
│ This is a full outage —   │         │ notifications to         │
│ retry shortly.            │         │ helpdesk@hfmg.net are     │
│                            │         │ queued/delayed.           │
└─────────────────────────┘         └─────────────────────────┘
```

- **Component:** `<DependencyErrorBanner dependency severity message>` [⬜ — depends on §1's `GET /api/v1/health/dependencies`]. Severity differs deliberately: DB down is full-outage red; OpenAI/Email down is degraded-but-functional (per `ARCHITECTURE.md` §10's backpressure design — tickets never depend on AI or email succeeding), Twilio down affects only the voice channel, not the web form.

---

## 13. New Backend Work — Consolidated

Every wireframe above calls out its own endpoint needs; this table exists so a backend engineer can scope the whole set in one place rather than hunting through prose.

| Endpoint | Needed by | Priority (per `DESIGN.md` §18) |
|---|---|---|
| `GET /api/v1/health/dependencies` | Shell §1, Dashboard §2, Error States §12 | High — small, unblocks the most-visible feature |
| `GET /api/v1/tickets?q=&priority=&category_id=&source=` | Tickets §3 | High — additive query params on an existing endpoint |
| `GET /api/v1/voice-calls` (list) | Voice Ops §5, Live Monitoring §6 | High — `voice_call_sessions` data is fully ready |
| `GET /api/v1/voice-calls/{id}` (detail) | Voice Ops §5, Live Monitoring §6 | High |
| `GET /api/v1/voice-calls/summary` | Voice Ops §5 | Medium |
| `GET /api/v1/analytics/kpis` | Dashboard §2 | Medium — blocked on AI Resolution Rate definition |
| `GET /api/v1/analytics/*` (6 chart endpoints) | Analytics §7 | Medium |
| `GET /api/v1/ai-insights` | AI Insights §8 | Low — blocked on product-level definitions, build last |
| `GET /api/v1/settings/status` | Settings §9 | Low — read-only, no urgency, but simple |
| `tickets.transcript` migration | Ticket Drawer §4 | High — unblocks the single biggest drawer feature |

---

## 14. Open Decisions Carried Forward

These are inherited verbatim from `DESIGN.md` §20 and repeated here because they block specific wireframe elements above — do not resolve them independently in frontend code:

1. **"AI Resolution Rate" definition** — blocks Dashboard §2 KPI card and Analytics §7.
2. **Transcript storage** (`tickets.transcript` migration vs. parsing `description`) — blocks Ticket Drawer §4's Voice Transcript section.
3. **`ai_summary` in the ticket list response** — blocks Tickets §3's AI Summary column.
4. **AI Insights' actual definitions** (trending vs. most-common, what a recommendation recommends) — blocks all of §8.
5. **Settings write-path timing** — confirmed blocked on Phase 3 auth; §9 ships read-only-only by design, not by omission.

## Changelog

- **1.0** (2026-09-20) — Initial version, wireframed against `DESIGN.md` v1.0 and current implementation status.

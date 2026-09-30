# Documentation Index

The complete map of this repository's documentation. Paths are relative to this file. If you are new, read section 1 and stop: those five documents are enough to become productive.

**Conventions.** Root documents are current and maintained. `docs/reviews/` holds dated audits (each states its date and method; re-check against the code before relying on one). `docs/integrations/` holds third-party integration verification. `docs/archive/` holds historical planning and build records. Inside `reviews/` and `archive/`, a bare filename such as `API_SPEC.md` means the file of that name at the repository root unless it sits in the same folder. When any document disagrees with the code or the tests, the code and tests win.

---

## 1. Start Here

**Purpose:** what the system is, its current status, how to run it, and the shortest path to understanding it.
**Primary audience:** every new engineer, reviewer, and AI coding agent.
**When to read it:** first, in this order.

| Document | What it gives you |
|---|---|
| [../README.md](../README.md) | What the system is, status, how to run it, documentation map |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | Components, data flow, why this stack |
| [../API_SPEC.md](../API_SPEC.md) | The API contract; section 0 is the verified implementation-status table |
| [../DATABASE_DESIGN.md](../DATABASE_DESIGN.md) | The schema |
| [../DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md) | How to deploy it |
| [../backend/README.md](../backend/README.md), [../frontend/README.md](../frontend/README.md) | Local setup for each half; read when you start running code |

## 2. Architecture

**Purpose:** how the system is structured and how the product is meant to behave.
**Primary audience:** engineers changing structure or behavior; reviewers judging a design change.
**When to read it:** before changing anything cross-cutting, or when a behavior surprises you.

| Document | What it gives you |
|---|---|
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | System architecture and the deliberate MVP scope decisions (no auth, no queue, no containers) |
| [../DESIGN.md](../DESIGN.md) | Product and UI design source of truth: navigation, pages, per-page build status |

## 3. API Documentation

**Purpose:** the HTTP contract between frontend, backend, and any integrator.
**Primary audience:** backend and frontend engineers, integrators, test authors.
**When to read it:** before adding or changing an endpoint or its schema, or when a frontend/backend mismatch appears.

- [../API_SPEC.md](../API_SPEC.md): endpoints, request/response shapes, query parameters, status table.

## 4. Database Documentation

**Purpose:** tables, columns, indexes, enums, and migration expectations.
**Primary audience:** backend engineers, anyone writing a migration or a query.
**When to read it:** before any schema change or non-trivial query. Note `TECHNICAL_DEBT.md` records which documented indexes were never built.

- [../DATABASE_DESIGN.md](../DATABASE_DESIGN.md)
- [../TECHNICAL_DEBT.md](../TECHNICAL_DEBT.md): accepted engineering tradeoffs, each with a trigger for fixing it

## 5. Deployment Documentation

**Purpose:** deploying, configuring, releasing, and operating the system in production.
**Primary audience:** whoever deploys and runs it (DevOps, on-call engineers).
**When to read it:** before a deployment, when configuring an external service, or during an incident.

| Document | Use it for |
|---|---|
| [../DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md) | Deploying to a server; the no-auth network constraint |
| [../RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md) | Pre-deploy checklist, classified by severity |
| [../OPERATIONS_RUNBOOK.md](../OPERATIONS_RUNBOOK.md) | Monitoring, backup and recovery, troubleshooting |
| [../EMAIL_INTEGRATION.md](../EMAIL_INTEGRATION.md) | How notification email works, and switching between SendGrid and the HFMG internal mail API |
| [../SENDGRID_SETUP.md](../SENDGRID_SETUP.md) | SendGrid account and key configuration |
| [../SIP_SETUP.md](../SIP_SETUP.md) | SIP gateway (Nextiva / SIPSorcery) configuration and the gateway-to-backend contract |
| [integrations/OPENAI_INTEGRATION_REPORT.md](integrations/OPENAI_INTEGRATION_REPORT.md) | How OpenAI configuration and summarization were verified (point-in-time; see the note at its top) |

## 6. Voice Agent Documentation

**Purpose:** how phone calls are answered, understood, escalated, and turned into tickets.
**Primary audience:** engineers changing voice behavior; whoever operates the SIP gateway.
**When to read it:** before touching `backend/app/voice/`, when debugging a call, and before go-live.

| Document | What it gives you |
|---|---|
| [../SIP_SETUP.md](../SIP_SETUP.md) | Voice integration architecture (Nextiva, SIPSorcery gateway, backend) and the endpoint contract |
| [../CALL_FLOW.md](../CALL_FLOW.md) | The conversation state machine |
| [../VOICE_AGENT_DESIGN.md](../VOICE_AGENT_DESIGN.md) | Prompts, understanding, classification |
| [../VOICE_SIMULATOR.md](../VOICE_SIMULATOR.md) | AI Call Simulator (test environments only): architecture, API, deployment, testing, operations, troubleshooting |
| [../VOICE_SIMULATOR_DESIGN.md](../VOICE_SIMULATOR_DESIGN.md) | The simulator's design, and the deviations made while building it (§11) |
| [reviews/TWILIO_READINESS_CHECK.md](reviews/TWILIO_READINESS_CHECK.md) | Historical (Twilio-era): superseded by the SIP migration |
| [reviews/TWILIO_READINESS_REPORT.md](reviews/TWILIO_READINESS_REPORT.md) | Historical (Twilio-era) readiness report |

## 7. Product Decisions

**Purpose:** what the product intentionally does not do yet, and which business questions are open.
**Primary audience:** product owners, stakeholders, engineers about to build a blocked feature.
**When to read it:** before building anything listed as blocked (for example AI Resolution Rate), and when someone asks "why doesn't it do X".

| Document | What it gives you |
|---|---|
| [../FINAL_PROJECT_STATUS.md](../FINAL_PROJECT_STATUS.md) | Features complete, open risks, technical debt, production readiness |
| [../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md) | Limitations written for stakeholders |
| [../REMAINING_PRODUCT_DECISIONS.md](../REMAINING_PRODUCT_DECISIONS.md) | Open business definitions; none is a bug |

## 8. Review Reports

**Purpose:** dated audits and assessments: security, performance, observability, test coverage, UX, AI quality, hardening, readiness.
**Primary audience:** reviewers, tech leads, whoever is prioritizing risk work.
**When to read it:** when planning hardening work or judging risk. Treat findings as of their date; some have since been fixed.

Start with [reviews/EXECUTIVE_RECOMMENDATIONS.md](reviews/EXECUTIVE_RECOMMENDATIONS.md) (latest), then [reviews/EXECUTIVE_SUMMARY.md](reviews/EXECUTIVE_SUMMARY.md).

| Area | Document |
|---|---|
| Summary | [EXECUTIVE_RECOMMENDATIONS](reviews/EXECUTIVE_RECOMMENDATIONS.md), [EXECUTIVE_SUMMARY](reviews/EXECUTIVE_SUMMARY.md) |
| AI | [AI_FAILURE_ANALYSIS](reviews/AI_FAILURE_ANALYSIS.md) (root cause and fix of summary failures), [OPENAI_PROMPT_REVIEW](reviews/OPENAI_PROMPT_REVIEW.md), [AI_QUALITY_REVIEW](reviews/AI_QUALITY_REVIEW.md) |
| Security | [SECURITY_REVIEW](reviews/SECURITY_REVIEW.md), [SECURITY_REVALIDATION](reviews/SECURITY_REVALIDATION.md) |
| Hardening, observability, performance | [PRODUCTION_HARDENING_REPORT](reviews/PRODUCTION_HARDENING_REPORT.md), [OBSERVABILITY_REVIEW](reviews/OBSERVABILITY_REVIEW.md), [PERFORMANCE_REVIEW](reviews/PERFORMANCE_REVIEW.md) |
| Testing and validation | [TEST_COVERAGE_REVIEW](reviews/TEST_COVERAGE_REVIEW.md), [SYSTEM_VALIDATION_REPORT](reviews/SYSTEM_VALIDATION_REPORT.md) |
| Frontend | [UX_REVIEW](reviews/UX_REVIEW.md), [UI_POLISH_REPORT](reviews/UI_POLISH_REPORT.md) |
| Voice | [TWILIO_READINESS_CHECK](reviews/TWILIO_READINESS_CHECK.md), [TWILIO_READINESS_REPORT](reviews/TWILIO_READINESS_REPORT.md) |
| Voice latency & understanding | [VOICE_LATENCY_ACCURACY_REPORT](reviews/VOICE_LATENCY_ACCURACY_REPORT.md) (root causes, fixes, before/after on a real-audio eval corpus; harness in `backend/eval/`) |
| Voice conversation quality | [VOICE_CONVERSATION_REVIEW](reviews/VOICE_CONVERSATION_REVIEW.md) (name/department confidence, read-back before ticket creation, priority reasoning, before/after transcripts of four real calls) |
| AI Call Simulator | [COMPLETION_REPORT](reviews/VOICE_SIMULATOR_COMPLETION_REPORT.md) (per-phase reports, production readiness), [SECURITY_REVIEW](reviews/VOICE_SIMULATOR_SECURITY_REVIEW.md), [PERFORMANCE](reviews/VOICE_SIMULATOR_PERFORMANCE.md) (load test at 1/5/10/25 callers; DB pool finding) |
| Email | [EMAIL_RELEASE_SUMMARY](reviews/EMAIL_RELEASE_SUMMARY.md) (branch, commit, tests), [EMAIL_TEST_REPORT](reviews/EMAIL_TEST_REPORT.md) (real sends and the Email-tile fix) |
| Documentation | [DOCUMENTATION_AUDIT_V2](reviews/DOCUMENTATION_AUDIT_V2.md) (why every file lives where it does) |

## 9. Historical Archive

**Purpose:** how the system was planned and built: implementation plans, gap analyses, work logs, wireframes, component and design-system specs.
**Primary audience:** maintainers researching why a decision was made; auditors.
**When to read it:** rarely, and only for context. Nothing here is current instruction; the running code supersedes it. Index: [archive/README.md](archive/README.md).

Contents: implementation plans, gap analyses and audit artifacts, backend and frontend work logs, and design and planning specifications (`DESIGN_SYSTEM`, `WIREFRAMES`, `COMPONENTS`), plus the plan for the first documentation cleanup. All keep their git history (`git log --follow <file>`).

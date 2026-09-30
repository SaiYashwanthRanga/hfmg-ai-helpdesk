# HFMG AI Help Desk — Release Checklist

Findings classified Critical / High / Medium / Low per the final production-readiness audit. **Critical and High items must be resolved before any deployment reachable beyond a trusted internal network.** This checklist summarizes and cross-references `DEPLOYMENT_GUIDE.md`, `docs/reviews/SECURITY_REVIEW.md`, and `OPERATIONS_RUNBOOK.md` rather than duplicating their detail — follow those documents for the how, this file for the what and why-it-matters.

---

## Critical — must fix or must explicitly accept before deploying beyond a trusted internal network

- [ ] **No authentication exists.** Confirm the deployment target is genuinely network-isolated (VPN/ACL, per `DEPLOYMENT_GUIDE.md` §1/§6) before any deployment. This is not a checklist item to "fix" quickly — it's Phase 3 backend work. If the deployment plan doesn't include real network isolation, **stop and escalate** rather than deploying anyway.
- [ ] **`CORS_ORIGINS` must be set to the real production frontend origin(s)**, not the `http://localhost:5173` default. Verify via `GET /api/v1/settings/status`-adjacent config review or a direct env check before go-live (`docs/reviews/SECURITY_REVIEW.md` §5).
- [ ] **`VOICE_SIP_GATEWAY_TOKEN` must be set to a long random value** in production and match the gateway's `Backend.GatewayToken`. Verify explicitly — it is the only authentication the three SIP endpoints have, and the backend port must be reachable only from the gateway host and the internal network.

## High — should fix before production traffic, workaround exists if deferred

- [ ] **No rate limiting is implemented anywhere** (`TECHNICAL_DEBT.md`). `POST /api/v1/tickets` and the SIP endpoints are the two most exposed. The SIP endpoints are protected by a bearer token and network isolation (acceptable without rate limiting); `POST /tickets` is not, and `API_SPEC.md` §15 documents the target limits (20/hour/IP) as not yet enforced. **Workaround if deferred:** rely on network ACL restricting who can reach the API at all.
- [ ] **Database backups configured with real retention**, matching HFMG's actual compliance retention window, not just a default backup-tool setting (`DEPLOYMENT_GUIDE.md` §9, `OPERATIONS_RUNBOOK.md`). Healthcare-adjacent ticket data often needs 6–7 year retention, which a default 30-day backup rotation will not satisfy.
- [ ] **BAAs signed with OpenAI, the SIP carrier (Nextiva), and the email provider** before any real caller/patient-adjacent data flows through them, if not already in place (`ARCHITECTURE.md` §8.1). Verify this is a completed legal/compliance step, not an engineering one to skip.
- [ ] **Point the orchestrator's readiness probe at `GET /api/v1/health/ready`** (real as of Backend Tier 0 — see `docs/archive/DOCUMENTATION_AUDIT.md`, this was previously undocumented as existing). Confirm your load balancer / container orchestrator config actually uses it, not just `/health`.

## Medium — should fix soon, no immediate production risk

- [ ] **Wire up alerting** per `OPERATIONS_RUNBOOK.md` §3.1's table (API down, DB unreachable, disk >85%, TLS expiring, 5xx rate, SIP gateway failures, email provider unconfigured, escalation rate spike). Verify each alert condition is actually configured in whatever monitoring stack is chosen, not just documented as a target.
- [ ] **Confirm `ENABLE_AI_SUMMARY` and email provider configuration match intent** for the production environment — both are safe when off (feature no-ops gracefully), but silently-off-when-you-meant-on is a real gap a stakeholder would notice.
- [ ] **Review `GET /health/dependencies`'s live status once in production** — confirm OpenAI/Email report `operational` and the SIP indicator reports `operational` with real production credentials configured, not just structurally correct responses (Backend Tier 1 built the check; it hasn't been exercised against real production credentials in this review, since none exist in the dev environment tested).

## Low — track, not urgent

- [ ] Frontend bundle code-splitting (`TECHNICAL_DEBT.md`) — purely a load-time optimization for an internal tool.
- [ ] Structured JSON logging — deferred to backend Phase 3 alongside the rest of the observability stack.
- [ ] SIP dependency-health check upgrade to a real gateway heartbeat — current configuration-only check (token set) is a reasonable interim signal.

---

## Pre-deploy smoke test (functional, not security)

Run against the target environment before declaring it live:

```bash
curl -fsS https://<host>/api/v1/health                    # {"status":"ok"}
curl -fsS https://<host>/api/v1/health/ready               # {"status":"ok"} — real DB check
curl -fsS https://<host>/api/v1/health/dependencies         # all four dependencies reported
curl -fsS https://<host>/api/v1/categories                  # non-empty if seeded
```
Then load the frontend and confirm: Dashboard renders (System Health + KPIs), Tickets list loads, a test ticket can be created and its status changed, Settings shows real (masked) provider status.

## Sign-off

This checklist, `docs/reviews/SECURITY_REVIEW.md`, `TECHNICAL_DEBT.md`, and `KNOWN_LIMITATIONS.md` together constitute the production-readiness record for this release. See `FINAL_PROJECT_STATUS.md` for the consolidated go/no-go assessment.

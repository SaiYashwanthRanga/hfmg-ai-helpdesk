import type { VoiceCallListItem } from "../types/voiceCall";

/**
 * Derives a human-readable outcome label from real, existing fields
 * (`state`, `escalated`, `escalation_reason`, `ticket_id`) — the exact
 * combinations CALL_FLOW.md §6 documents, not an invented category. This is
 * presentation only: no new business definition, just naming a state
 * combination the backend already returns.
 */
export function deriveCallOutcome(call: VoiceCallListItem): string {
  if (call.state === "COMPLETED") {
    return call.ticket_id ? "Completed — ticket created" : "Completed";
  }
  if (call.state === "ESCALATED") {
    switch (call.escalation_reason) {
      case "CALLER_REQUESTED":
        return "Escalated — caller requested";
      case "REPEATED_MISUNDERSTANDING":
        return "Escalated — misunderstood";
      case "SYSTEM_ERROR":
        return "Escalated — system error";
      default:
        return "Escalated";
    }
  }
  if (call.state === "ABANDONED") {
    return call.ticket_id ? "Abandoned — salvaged to ticket" : "Abandoned";
  }
  return "In progress";
}

/** `ended_at - created_at`, per WIREFRAMES.md §5: precise call-start time
 * isn't stored, and `created_at` is documented as "good enough for v1". */
export function formatCallDuration(createdAt: string, endedAt: string | null): string {
  if (!endedAt) return "In progress";
  const seconds = Math.max(0, Math.round((new Date(endedAt).getTime() - new Date(createdAt).getTime()) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}m ${remainder}s`;
}

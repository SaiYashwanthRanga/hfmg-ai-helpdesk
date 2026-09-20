import { Badge } from "../ui/Badge";
import type { EscalationReason } from "../../types/voiceCall";

const LABEL: Record<EscalationReason, string> = {
  CALLER_REQUESTED: "Caller requested",
  REPEATED_MISUNDERSTANDING: "Repeated misunderstanding",
  SYSTEM_ERROR: "System error",
};

/** Static escalation-reason pill (CALL_FLOW.md §6's three real reasons — never fabricated). */
export function EscalationReasonBadge({ reason }: { reason: EscalationReason }) {
  return <Badge label={LABEL[reason]} color="danger" />;
}

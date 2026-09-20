import type { SemanticColor } from "../ui/Badge";
import { Badge } from "../ui/Badge";
import type { VoiceCallState } from "../../types/voiceCall";

// DESIGN_SYSTEM.md §2.5, corrected for the real 11-state enum (no
// CREATING_TICKET/READ_BACK — see types/voiceCall.ts).
const STATE_COLOR: Record<VoiceCallState, SemanticColor> = {
  GREETING: "info",
  COLLECT_DESCRIPTION: "info",
  COLLECT_NAME: "info",
  COLLECT_PHONE: "info",
  COLLECT_EMAIL: "info",
  CONFIRM_EMAIL: "primary",
  CONFIRM_CATEGORY: "primary",
  ANYTHING_ELSE: "primary",
  ESCALATED: "danger",
  COMPLETED: "success",
  ABANDONED: "muted",
};

const STATE_LABEL: Record<VoiceCallState, string> = {
  GREETING: "Greeting",
  COLLECT_DESCRIPTION: "Collecting description",
  COLLECT_NAME: "Collecting name",
  COLLECT_PHONE: "Collecting phone",
  COLLECT_EMAIL: "Collecting email",
  CONFIRM_EMAIL: "Confirming email",
  CONFIRM_CATEGORY: "Confirming category",
  ANYTHING_ELSE: "Wrapping up",
  ESCALATED: "Escalated",
  COMPLETED: "Completed",
  ABANDONED: "Abandoned",
};

/** Static call-state pill (DESIGN_SYSTEM.md §9.3) — human-readable label, never the raw enum string. */
export function CallStateBadge({ state }: { state: VoiceCallState }) {
  return <Badge label={STATE_LABEL[state]} color={STATE_COLOR[state]} />;
}

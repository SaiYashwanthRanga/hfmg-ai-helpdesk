import type { SemanticColor } from "../ui/Badge";
import { Badge } from "../ui/Badge";
import type { VoiceCallState } from "../../types/voiceCall";

// DESIGN_SYSTEM.md §2.5, corrected for the real 11-state enum (no
// CREATING_TICKET/READ_BACK — see types/voiceCall.ts).
const STATE_COLOR: Record<VoiceCallState, SemanticColor> = {
  GREETING: "info",
  IDENTIFY_CALLER: "info",
  PATIENT_INTENT: "info",
  PATIENT_DETAILS: "info",
  COLLECT_DESCRIPTION: "info",
  CLASSIFY_CALLER_TYPE: "info",
  COLLECT_DETAILS: "info",
  CONFIRM_IDENTITY: "primary",
  COLLECT_NAME: "info",
  CONFIRM_NAME: "primary",
  COLLECT_PHONE: "info",
  CONFIRM_CALLBACK_NUMBER: "primary",
  COLLECT_ALTERNATE_CALLBACK_NUMBER: "info",
  COLLECT_EXTENSION: "info",
  COLLECT_DIAGNOSTICS: "info",
  COLLECT_EMAIL: "info",
  CONFIRM_EMAIL: "primary",
  CONFIRM_CATEGORY: "primary",
  CONFIRM_SUMMARY: "primary",
  ANYTHING_ELSE: "primary",
  ESCALATED: "danger",
  COMPLETED: "success",
  ABANDONED: "muted",
};

const STATE_LABEL: Record<VoiceCallState, string> = {
  GREETING: "Greeting",
  IDENTIFY_CALLER: "Asking who is calling",
  PATIENT_INTENT: "Patient: what they need",
  PATIENT_DETAILS: "Patient: request details",
  COLLECT_DESCRIPTION: "Collecting description",
  CLASSIFY_CALLER_TYPE: "Classifying caller",
  COLLECT_DETAILS: "Collecting details",
  CONFIRM_IDENTITY: "Confirming caller",
  COLLECT_NAME: "Collecting name",
  CONFIRM_NAME: "Confirming name",
  COLLECT_PHONE: "Collecting phone",
  CONFIRM_CALLBACK_NUMBER: "Confirming callback number",
  COLLECT_ALTERNATE_CALLBACK_NUMBER: "Collecting another number",
  COLLECT_EXTENSION: "Collecting extension",
  COLLECT_DIAGNOSTICS: "Asking about the issue",
  COLLECT_EMAIL: "Collecting email",
  CONFIRM_EMAIL: "Confirming email",
  CONFIRM_CATEGORY: "Confirming category",
  CONFIRM_SUMMARY: "Confirming summary",
  ANYTHING_ELSE: "Wrapping up",
  ESCALATED: "Escalated",
  COMPLETED: "Completed",
  ABANDONED: "Abandoned",
};

/** Static call-state pill (DESIGN_SYSTEM.md §9.3) — human-readable label, never the raw enum string. */
export function CallStateBadge({ state }: { state: VoiceCallState }) {
  return <Badge label={STATE_LABEL[state]} color={STATE_COLOR[state]} />;
}

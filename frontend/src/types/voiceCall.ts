import type { Priority } from "./ticket";

// Matches backend/app/db/models.py's VoiceCallState exactly — 11 values.
// CALL_FLOW.md/TWILIO_ARCHITECTURE.md previously documented 13 (including
// CREATING_TICKET/READ_BACK); those were corrected in Backend Tier 5 — they
// are narrative steps inline within another transition, never a distinct
// persisted state, and the API will never return them.
export type VoiceCallState =
  | "GREETING"
  | "COLLECT_DESCRIPTION"
  | "COLLECT_NAME"
  | "COLLECT_PHONE"
  | "COLLECT_EMAIL"
  | "CONFIRM_EMAIL"
  | "CONFIRM_CATEGORY"
  | "ANYTHING_ELSE"
  | "ESCALATED"
  | "COMPLETED"
  | "ABANDONED";

export type EscalationReason = "CALLER_REQUESTED" | "REPEATED_MISUNDERSTANDING" | "SYSTEM_ERROR";

export interface VoiceCallTurn {
  role: string;
  text: string;
  confidence: number | null;
  at: string;
}

export interface VoiceCallListItem {
  id: string;
  twilio_call_sid: string;
  from_number: string;
  state: VoiceCallState;
  escalated: boolean;
  escalation_reason: EscalationReason | null;
  ticket_id: string | null;
  created_at: string;
  ended_at: string | null;
  caller_name: string | null;
  category: string | null;
  priority: Priority | null;
}

export interface VoiceCallDetail extends VoiceCallListItem {
  to_number: string;
  misunderstanding_count: number;
  email_attempt_count: number;
  collected: Record<string, unknown>;
  turns: VoiceCallTurn[];
  updated_at: string;
}

export interface VoiceCallPage {
  items: VoiceCallListItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface VoiceCallSummary {
  active: number;
  completed: number;
  escalated: number;
  abandoned: number;
}

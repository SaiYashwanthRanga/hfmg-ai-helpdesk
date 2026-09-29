/**
 * AI Call Simulator types. Mirrors backend/app/simulator/schemas.py field
 * for field — change both together.
 */
import type { AISummaryStatus, Priority, TicketStatus } from "./ticket";

export type SimulatorStatus =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "disconnected"
  | "error";

export type AgentState =
  | "GREETING"
  | "COLLECT_DESCRIPTION"
  | "COLLECT_DETAILS"
  | "COLLECT_NAME"
  | "CONFIRM_NAME"
  | "COLLECT_PHONE"
  | "COLLECT_EMAIL"
  | "CONFIRM_EMAIL"
  | "CONFIRM_CATEGORY"
  | "CONFIRM_SUMMARY"
  | "ANYTHING_ELSE"
  | "ESCALATED"
  | "COMPLETED"
  | "ABANDONED";

export type InputMode = "voice" | "text" | "mock" | "system";
export type ListenMode = "push_to_talk" | "continuous";
export type EndReason = "user_hangup" | "agent_hangup" | "error" | "cleared" | "idle";
export type EscalationReason = "CALLER_REQUESTED" | "REPEATED_MISUNDERSTANDING" | "SYSTEM_ERROR";

export interface CollectedSlots {
  description: string | null;
  short_issue: string | null;
  caller_name: string | null;
  phone_number: string | null;
  email: string | null;
  category: string | null;
  category_confidence: string | null;
  priority: Priority | null;
  impact: string | null;
  department: string | null;
  /** False when the department isn't on HFMG's list (read back, marked on the ticket). */
  department_verified: boolean | null;
  started: string | null;
  work_blocked: boolean | null;
  /** Why the priority is what it is ("you can't work"): the text the agent speaks. */
  priority_reason: string | null;
  /** "low" = unfamiliar name, read back spelled before it is trusted. */
  name_confidence: "high" | "low" | null;
}

export interface SimulatorTicket {
  id: string;
  ticket_number: string;
  status: TicketStatus;
  category: string;
  priority: Priority;
  ai_summary_status: AISummaryStatus;
  ai_summary: string | null;
}

export interface SimulatorSession {
  id: string;
  label: string | null;
  state: AgentState;
  collected: CollectedSlots;
  misunderstanding_count: number;
  escalated: boolean;
  escalation_reason: EscalationReason | null;
  ticket: SimulatorTicket | null;
  started_at: string;
  ended_at: string | null;
  end_reason: EndReason | null;
  tts_enabled: boolean;
  send_notifications: boolean;
  caller_id: string | null;
}

export interface ReplyAudio {
  base64: string;
  mime_type: string;
}

export interface AgentReply {
  text: string;
  call_ended: boolean;
  /** Deprecated; always null. */
  audio: ReplyAudio | null;
  /** Stream the spoken reply from API base + this path; null when speech is off. */
  speech_path: string | null;
}

export interface TurnTimings {
  turn_index: number;
  stt_ms: number | null;
  llm_ms: number | null;
  tts_ms: number | null;
  ticket_create_ms: number | null;
  queue_wait_ms: number | null;
  server_total_ms: number | null;
  utterance_ms: number | null;
  capture_ms: number | null;
  playback_start_ms: number | null;
  playback_duration_ms: number | null;
  turn_total_ms: number | null;
}

export interface LLMCallTrace {
  /** "rule" = answered by a deterministic rule, no model call. */
  kind: "structured" | "text" | "rule";
  schema_name: string | null;
  started_ms: number;
  duration_ms: number;
  system: string;
  user: string;
  output: Record<string, unknown> | string | null;
  error: string | null;
  attempts: number;
}

export interface TurnError {
  stage: "stt" | "llm" | "tts" | "agent" | "db";
  type: string;
  message: string;
}

export interface SimulatorTurn {
  id: string;
  turn_client_id: string;
  turn_index: number;
  status: string;
  input_mode: InputMode;
  utterance: string | null;
  stt_confidence: number | null;
  stt_raw: Record<string, unknown> | null;
  state_before: AgentState | null;
  state_after: AgentState | null;
  intent: string | null;
  agent_text: string | null;
  call_ended: boolean;
  collected_after: CollectedSlots | null;
  ticket_payload: Record<string, unknown> | null;
  llm_trace: LLMCallTrace[];
  errors: TurnError[];
  timings: TurnTimings;
  created_at: string;
}

export interface StartRequest {
  caller_id?: string | null;
  send_notifications?: boolean;
  tts?: boolean;
  label?: string | null;
}

export interface StartResponse {
  session: SimulatorSession;
  greeting: AgentReply;
  turn: SimulatorTurn;
}

export interface AudioResponse {
  turn_client_id: string;
  transcript: string;
  confidence: number | null;
  empty: boolean;
  error: string | null;
  raw: Record<string, unknown> | null;
  timings: { stt_ms: number | null; server_ms: number };
}

export interface ProcessRequest {
  session_id: string;
  turn_client_id: string;
  utterance?: string | null;
  input_mode: "voice" | "text" | "mock";
}

export interface ProcessResponse {
  turn: SimulatorTurn;
  reply: AgentReply;
  session: SimulatorSession;
}

export interface SessionDetail {
  session: SimulatorSession;
  turns: SimulatorTurn[];
}

export interface StageStats {
  current: number | null;
  avg: number | null;
  max: number | null;
  p95: number | null;
  n: number;
}

export type LatencyStage =
  | "capture_ms"
  | "stt_ms"
  | "llm_ms"
  | "tts_ms"
  | "playback_start_ms"
  | "turn_total_ms"
  | "server_total_ms"
  | "queue_wait_ms"
  | "ticket_create_ms";

export interface SimulatorMetrics {
  session_id: string;
  turns: TurnTimings[];
  stats: Record<LatencyStage, StageStats>;
}

export interface ClientMetrics {
  turn_client_id: string;
  utterance_ms?: number | null;
  capture_ms?: number | null;
  playback_start_ms?: number | null;
  playback_duration_ms?: number | null;
  turn_total_ms?: number | null;
}

export interface SimulatorStats {
  window_hours: number;
  sessions_total: number;
  sessions_active: number;
  sessions_escalated: number;
  tickets_created: number;
  turns_total: number;
  turns_failed: number;
  failure_rate: number;
  stages: Record<LatencyStage, StageStats>;
  generated_at: string;
}

export interface SimulatorConfig {
  enabled: boolean;
  speech_configured: boolean;
  llm_configured: boolean;
  stt_model: string;
  tts_model: string;
  tts_voice: string;
  max_audio_bytes: number;
  max_turns_per_session: number;
  max_concurrent_sessions: number;
  /** SIMULATOR_ALLOW_NOTIFICATIONS: whether a session may send the real ticket email. */
  notifications_allowed: boolean;
  voice_categories: string[];
}

export interface MockCaller {
  seed: number;
  persona: string;
  caller_name: string;
  phone_number: string;
  email_spoken: string;
  issue: string;
  /** Keyed by the agent state being answered. */
  answers: Partial<Record<AgentState, string>>;
  expect: {
    category: string | null;
    priority: Priority | null;
    escalated: boolean;
    ticket_created: boolean;
    note: string | null;
  };
}

export interface RandomIssue {
  seed: number;
  category: string;
  priority: Priority;
  utterance: string;
  note: string | null;
}

/** One row in the conversation timeline. Local-only entries carry no turn id. */
export type TimelineEntry =
  | {
      kind: "caller";
      key: string;
      turnClientId: string;
      text: string;
      at: string;
      pending: boolean;
      inputMode: InputMode;
    }
  | { kind: "agent"; key: string; turnClientId: string; text: string; at: string }
  | { kind: "system"; key: string; text: string; at: string; tone: "info" | "warning" | "error" };

/** One request made by the page, for the inspector's API-calls view. */
export interface ApiCallRecord {
  id: string;
  method: string;
  path: string;
  status: number | null;
  durationMs: number;
  at: string;
  error: string | null;
}

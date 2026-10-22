/** Mirrors backend/app/schemas/ticket_report.py and the saved analysis built in ticket_report_service.py. */

export type ReportStatus = "UPLOADED" | "ANALYZING" | "COMPLETED" | "FAILED";
export type EmailStatus = "NOT_SENT" | "SENDING" | "SENT" | "FAILED";
export type AiPriority = "Critical" | "High" | "Medium" | "Low";

export interface ReportConfig {
  enabled: boolean;
  accepted_extensions: string[];
  max_upload_bytes: number;
  max_rows: number;
  ai_configured: boolean;
  email: { configured: boolean; recipient: string | null; problem: string | null };
}

export interface ValidationIssue {
  code: string;
  message: string;
  detail?: Record<string, unknown> | null;
}

export interface DetectedColumn {
  name: string;
  field: string | null;
  used: boolean;
  note?: string | null;
}

export interface ReportProgress {
  stage: string;
  done: number;
  total: number;
  message: string;
}

export interface ReportEmail {
  status: EmailStatus;
  recipient: string | null;
  attempts: number;
  sent_at: string | null;
  last_attempt_at: string | null;
  error: string | null;
  can_send: boolean;
  blocked_reason: string | null;
}

/** The AI's recommendation for one ticket. A recommendation for a person to review; Freshworks is never changed. */
export interface AiTicketResult {
  status: "analyzed" | "unavailable" | "not_analyzed";
  priority: AiPriority | null;
  /** One short sentence. */
  reason: string | null;
  next_action: string | null;
  issue_label: string | null;
  /** The ticket said too little to prioritize with confidence; the reason says what is missing. */
  limited_information: boolean;
}

export interface ReportTicket {
  ticket_id: string;
  subject: string;
  status: string | null;
  state: "open" | "unknown";
  priority: string | null;
  priority_level: "critical" | "high" | "medium" | "low" | null;
  assignee: string | null;
  group: string | null;
  category: string | null;
  created: string | null;
  due: string | null;
  age_days: number | null;
  overdue: boolean | null;
  ai: AiTicketResult;
}

export interface PriorityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
  unrecognized: number;
  not_specified: number;
}

/** A number that is `null` could not be established from the export ("Not available"), and is never shown as 0. */
export interface ReportSummary {
  total: number;
  status_available: boolean;
  /** Not resolved: open plus the tickets whose status could not be read. */
  unresolved: number | null;
  open: number | null;
  resolved: number | null;
  unknown_status: number;
  by_priority: PriorityCounts | null;
  by_priority_unresolved: PriorityCounts | null;
  critical_high_unresolved: number | null;
  overdue: number | null;
  sla_breaches: number | null;
  first_response_breaches: number | null;
  unassigned: number | null;
  unassigned_ticket_ids: string[];
  top_categories: { name: string; count: number }[] | null;
  oldest_unresolved_age_days: number | null;
  median_unresolved_age_days: number | null;
  /** From the AI's validated answers; null when it analyzed nothing. */
  ai_analyzed: number;
  ai_critical: number | null;
  ai_high: number | null;
}

/** One row of the employee table. `unassigned` marks the "Unassigned" row: it is not a person. */
export interface EmployeeWorkload {
  name: string;
  unassigned: boolean;
  total: number;
  overdue: number;
  critical: number;
  high: number;
  remaining: number;
  unknown_status: number;
  /** For the email's workload table; reports saved before it existed do not have them. */
  oldest_age_days?: number | null;
  share_percent?: number;
  observation?: string;
  /** One short AI sentence about this workload; empty when the AI gave none. */
  ai_follow_up?: string;
}

export interface RecurringIssue {
  title: string;
  ticket_ids: string[];
  /** One short sentence: a possible cause or fix, worded as a possibility. */
  note: string;
}

/** Fields every weekly follow-up entry carries: the ticket, its owner, and how long it has been open. */
export interface FollowupBase {
  ticket_id: string;
  subject: string;
  assignee: string | null;
  /** Days since the ticket was created; null when the file has no created time. */
  days_open: number | null;
}

/** Waiting on a vendor, or Needs Review when the ticket hints at a vendor but does not show it is waiting on one. */
export interface VendorItem extends FollowupBase {
  status: "waiting_on_vendor" | "needs_review";
  evidence: string;
  next_action: string;
}

export interface ProviderItem extends FollowupBase {
  issue: string;
  /** The business impact the ticket describes, or "Impact not stated". */
  impact: string;
  next_action: string;
  priority: AiPriority | null;
  freshworks_priority: string | null;
}

/** A ticket the AI recommended as Critical or High: it affects daily work. */
export interface DailyWorkItem extends FollowupBase {
  priority: AiPriority;
  freshworks_priority: string | null;
  overdue: boolean | null;
  reason: string | null;
  next_action: string | null;
}

export interface Followup {
  vendor: VendorItem[];
  provider: ProviderItem[];
  daily_work: DailyWorkItem[];
  /** "unavailable" / "partial": the vendor and provider pass failed in whole or in part; the priorities are unaffected. */
  status: "complete" | "partial" | "unavailable" | "skipped";
  classified: number;
  failed_batches: number;
  dropped_answers: number;
}

/** The layout of the analysis this page renders. A report saved with another one is not shown (upload the file again). */
export const ANALYSIS_VERSION = 2;

export interface Analysis {
  version: number;
  as_of: string;
  source: { filename: string; file_type: string; rows_in_file: number; rows_used: number };
  summary_fields: string[];
  summary: ReportSummary;
  /** At most three short statements written from the report's own numbers. */
  highlights: string[];
  employees: EmployeeWorkload[];
  /** Open tickets in the order to work them: Critical to Low, overdue and oldest first. */
  tickets: ReportTicket[];
  /** The short list: every Critical and High ticket plus a few Medium ones. The rest are behind "show all". */
  action_ids: string[];
  /** The weekly follow-up groups. Absent in a report saved before they existed. */
  followup?: Followup;
  recurring_issues: RecurringIssue[];
  recurring_note: string;
  ai: {
    provider: string;
    tickets_analyzed: number;
    tickets_unavailable: number;
    tickets_not_analyzed: number;
    batches: number;
    failed_batches: number;
    dropped_answers: number;
    synthesis: "complete" | "unavailable" | "skipped";
  };
  data_quality: { warnings: string[]; unavailable: string[]; assumptions: string[]; limitations: string[] };
}

export interface TicketReportRecord {
  id: string;
  filename: string;
  file_type: string;
  status: ReportStatus;
  created_at: string;
  source_row_count: number;
  row_count: number;
  columns: DetectedColumn[];
  validation: { valid: boolean; errors: ValidationIssue[]; warnings: ValidationIssue[]; fields_present: string[] };
  can_analyze: boolean;
  progress: ReportProgress | null;
  error: string | null;
  analysis: Analysis | null;
  analysis_completed_at: string | null;
  email: ReportEmail;
}

export interface SendResult {
  report: TicketReportRecord;
  already_sent: boolean;
  message: string;
}

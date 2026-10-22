/**
 * What the Ticket Analysis page lets the user do, decided in one place and with no React in it.
 *
 * The server is the source of truth for a report's state (uploaded / analyzing / completed / failed, sent or not).
 * This module turns that state, plus what the page itself is doing right now (an upload, a start, a send in
 * flight), into "is this button enabled, and if not, why" -- and keeps a click from being acted on twice.
 */

import type { ReportConfig, TicketReportRecord } from "../../types/ticketReport.ts";

export interface Availability {
  enabled: boolean;
  /** Why it is disabled, in words the user can act on. Empty when enabled. */
  reason: string;
}

const yes: Availability = { enabled: true, reason: "" };
const no = (reason: string): Availability => ({ enabled: false, reason });

/** Decimal units, like the server's own messages: the 5,000,000-byte limit is "5.0 MB", not "4.8 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1)} KB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

/** The checks the browser can make before uploading; the server repeats them. Returns an error message or null. */
export function validateFileClient(
  file: { name: string; size: number },
  config: Pick<ReportConfig, "accepted_extensions" | "max_upload_bytes">,
): string | null {
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".xls")) return "Old Excel (.xls) files are not supported. Save the export as .xlsx or .csv.";
  if (!config.accepted_extensions.some((ext) => lower.endsWith(ext))) {
    return `Unsupported file type. Upload a Freshworks export as ${config.accepted_extensions.join(" or ")}.`;
  }
  if (file.size === 0) return "The file is empty.";
  if (file.size > config.max_upload_bytes) {
    return `The file is ${formatBytes(file.size)}; the limit is ${formatBytes(config.max_upload_bytes)}.`;
  }
  return null;
}

export function analyzeAvailability(report: TicketReportRecord | null, busy: { starting: boolean; uploading: boolean }): Availability {
  if (busy.uploading) return no("The file is still uploading.");
  if (!report) return no("Upload a Freshworks export first.");
  if (busy.starting || report.status === "ANALYZING") return no("The analysis is running.");
  if (report.status === "COMPLETED") return no("This file has been analyzed. Upload another file to analyze it again.");
  if (!report.validation.valid) return no("Fix the file errors above and upload it again.");
  if (!report.can_analyze) return no("This file cannot be analyzed.");
  return yes;
}

export function sendAvailability(report: TicketReportRecord | null, config: ReportConfig | undefined, sending: boolean): Availability {
  if (!report) return no("Upload and analyze a file first.");
  if (sending || report.email.status === "SENDING") return no("The report is being sent.");
  if (report.status === "ANALYZING") return no("Wait for the analysis to finish.");
  if (report.status !== "COMPLETED" || !report.analysis) return no("Analyze the file successfully before sending.");
  if (report.email.status === "SENT") return no("This report has already been sent.");
  if (config && !config.email.configured) return no(config.email.problem ?? "Email is not configured.");
  if (!report.email.can_send) return no(report.email.blocked_reason ?? "The report cannot be sent right now.");
  return yes;
}

/** The facts shown in the "send this report?" dialog: what the email will contain (team summary, employee table, vendor and OM/user lists). */
export function confirmationSummary(report: TicketReportRecord): string[] {
  const analysis = report.analysis;
  if (!analysis) return [];
  const { summary, employees, followup } = analysis;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const lines = [`${plural(summary.total, "ticket")} in ${report.filename}`];
  if (summary.unresolved !== null) {
    lines.push(`${summary.unresolved} open; ${summary.overdue === null ? "overdue count not available" : `${summary.overdue} overdue`}`);
  }
  if (summary.ai_critical !== null && summary.ai_high !== null) {
    lines.push(`${summary.ai_critical} Critical and ${summary.ai_high} High, as recommended by the AI`);
  }
  lines.push(`${plural(employees.length, "row")} in the employee workload table`);
  if (!followup || followup.status === "unavailable") {
    lines.push("Waiting on vendor and OM/user issues: not available for this report");
  } else {
    lines.push(`${plural(followup.vendor.length, "ticket")} waiting on a vendor or needing review`);
    lines.push(plural(followup.provider.length, "OM/user issue"));
  }
  return lines;
}

/** The delivery status line. "Sent" is only ever said when the server recorded it. */
export function deliveryStatus(email: TicketReportRecord["email"], formatTime: (iso: string) => string): { tone: "neutral" | "success" | "error" | "busy"; text: string } {
  switch (email.status) {
    case "SENT":
      return { tone: "success", text: `Sent to ${email.recipient ?? "the IT team"}${email.sent_at ? ` on ${formatTime(email.sent_at)}` : ""}.` };
    case "SENDING":
      return { tone: "busy", text: "Sending…" };
    case "FAILED":
      return { tone: "error", text: `${email.error ?? "The report was not sent."} (attempt ${email.attempts})` };
    default:
      return { tone: "neutral", text: "Not sent yet. Nothing is emailed until you press Send Email." };
  }
}

// --- the page's own state -----------------------------------------------------------------------------------------

export interface PageState {
  reportId: string | null;
  uploading: boolean;
  starting: boolean;
  confirmOpen: boolean;
  sending: boolean;
  message: { kind: "error" | "success"; text: string } | null;
}

export type PageEvent =
  | { type: "upload-started" }
  | { type: "upload-finished"; reportId: string }
  | { type: "upload-failed"; error: string }
  | { type: "report-missing" }
  | { type: "analyze-started" }
  | { type: "analyze-settled"; error?: string }
  | { type: "confirm-opened" }
  | { type: "confirm-closed" }
  | { type: "send-started" }
  | { type: "send-settled"; ok: boolean; text: string }
  | { type: "dismiss-message" };

export const initialPageState = (reportId: string | null = null): PageState => ({
  reportId, uploading: false, starting: false, confirmOpen: false, sending: false, message: null,
});

/**
 * A start or a send that is already in flight ignores a second one, so a double click or a held Enter key
 * reaches the server once. (The server refuses a duplicate too; this keeps the page from asking.)
 */
export function pageReducer(state: PageState, event: PageEvent): PageState {
  switch (event.type) {
    case "upload-started":
      return state.uploading ? state : { ...state, uploading: true, message: null };
    case "upload-finished":
      return { ...initialPageState(event.reportId) };
    case "upload-failed":
      return { ...state, uploading: false, message: { kind: "error", text: event.error } };
    case "report-missing":
      return initialPageState(null);
    case "analyze-started":
      return state.starting ? state : { ...state, starting: true, message: null };
    case "analyze-settled":
      return { ...state, starting: false, message: event.error ? { kind: "error", text: event.error } : state.message };
    case "confirm-opened":
      return state.sending ? state : { ...state, confirmOpen: true };
    case "confirm-closed":
      return state.sending ? state : { ...state, confirmOpen: false };
    case "send-started":
      return state.sending ? state : { ...state, sending: true, message: null };
    case "send-settled":
      return { ...state, sending: false, confirmOpen: false, message: { kind: event.ok ? "success" : "error", text: event.text } };
    case "dismiss-message":
      return { ...state, message: null };
  }
}

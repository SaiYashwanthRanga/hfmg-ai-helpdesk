/**
 * Unit tests for the Ticket Analysis page's logic: which buttons are enabled and why, the guards against a repeated
 * click, the delivery status wording, and the report table's sorting and filters. Run with `npm test`.
 *
 * These cover the logic the page is built from (lib/ticketReport). The React components render that logic and are
 * checked by type-checking and by using the page; this project has no DOM test environment.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  analyzeAvailability,
  confirmationSummary,
  deliveryStatus,
  formatBytes,
  initialPageState,
  pageReducer,
  sendAvailability,
  validateFileClient,
} from "../src/lib/ticketReport/workflow.ts";
import type { PageEvent, PageState } from "../src/lib/ticketReport/workflow.ts";
import { ageLabel, assigneeOptions, filterTickets, formatCount, hasFilters, NO_FILTERS, visibleTickets } from "../src/lib/ticketReport/view.ts";
import { daysOpenLabel, followupNote, FOLLOWUP_ROWS, limitRows, vendorStatusLabel } from "../src/lib/ticketReport/followup.ts";
import type { Followup } from "../src/types/ticketReport.ts";
import type { Analysis, ReportConfig, ReportTicket, TicketReportRecord } from "../src/types/ticketReport.ts";

// --- fixtures ---------------------------------------------------------------

const CONFIG: ReportConfig = {
  enabled: true,
  accepted_extensions: [".csv", ".xlsx"],
  max_upload_bytes: 5_000_000,
  max_rows: 2000,
  ai_configured: true,
  email: { configured: true, recipient: "it-team@hfmg.net", problem: null },
};

function ticket(id: string, overrides: Partial<ReportTicket> = {}): ReportTicket {
  return {
    ticket_id: id, subject: `Subject ${id}`, status: "Open", state: "open", priority: "Medium", priority_level: "medium",
    assignee: "Dana", group: null, category: null, created: null, due: null, age_days: 3, overdue: false,
    ai: { status: "analyzed", priority: "Medium", reason: "r", next_action: "n", issue_label: "Outlook problem", limited_information: false },
    ...overrides,
  };
}

function analysis(overrides: Partial<Analysis> = {}): Analysis {
  return {
    version: 2,
    as_of: "2026-10-22T10:00:00+00:00",
    source: { filename: "export.csv", file_type: "csv", rows_in_file: 6, rows_used: 6 },
    summary_fields: ["ticket_id", "subject", "assignee"],
    summary: {
      total: 6, status_available: true, unresolved: 4, open: 3, resolved: 2, unknown_status: 1, by_priority: null, by_priority_unresolved: null,
      critical_high_unresolved: 2, overdue: 1, sla_breaches: null, first_response_breaches: null, unassigned: 1, unassigned_ticket_ids: ["3"], top_categories: null,
      oldest_unresolved_age_days: 21, median_unresolved_age_days: 5, ai_analyzed: 4, ai_critical: 1, ai_high: 2,
    },
    highlights: ["4 open tickets; 1 overdue."],
    employees: [
      { name: "Dana", unassigned: false, total: 3, overdue: 1, critical: 1, high: 1, remaining: 1, unknown_status: 0 },
      { name: "Unassigned", unassigned: true, total: 1, overdue: 0, critical: 0, high: 1, remaining: 0, unknown_status: 0 },
    ],
    tickets: [ticket("1")],
    action_ids: ["1"],
    recurring_issues: [{ title: "Outlook", ticket_ids: ["1", "2"], note: "n" }],
    recurring_note: "",
    ai: { provider: "openai", tickets_analyzed: 4, tickets_unavailable: 0, tickets_not_analyzed: 0, batches: 1, failed_batches: 0, dropped_answers: 0, synthesis: "complete" },
    data_quality: { warnings: [], unavailable: [], assumptions: [], limitations: [] },
    ...overrides,
  };
}

function report(overrides: Partial<TicketReportRecord> = {}): TicketReportRecord {
  return {
    id: "r1", filename: "export.csv", file_type: "csv", status: "UPLOADED", created_at: "2026-10-22T10:00:00Z",
    source_row_count: 6, row_count: 6, columns: [], validation: { valid: true, errors: [], warnings: [], fields_present: [] },
    can_analyze: true, progress: null, error: null, analysis: null, analysis_completed_at: null,
    email: { status: "NOT_SENT", recipient: null, attempts: 0, sent_at: null, last_attempt_at: null, error: null, can_send: true, blocked_reason: null },
    ...overrides,
  };
}

const completed = (overrides: Partial<TicketReportRecord> = {}) => report({ status: "COMPLETED", can_analyze: false, analysis: analysis(), ...overrides });
const idle = { starting: false, uploading: false };

// --- the file, before it is uploaded -----------------------------------------

describe("validateFileClient", () => {
  it("accepts csv and xlsx, whatever the letter case", () => {
    assert.equal(validateFileClient({ name: "tickets.csv", size: 100 }, CONFIG), null);
    assert.equal(validateFileClient({ name: "TICKETS.XLSX", size: 100 }, CONFIG), null);
  });
  it("explains each refusal", () => {
    assert.match(validateFileClient({ name: "old.xls", size: 100 }, CONFIG) ?? "", /\.xls\) files are not supported/);
    assert.match(validateFileClient({ name: "notes.txt", size: 100 }, CONFIG) ?? "", /Unsupported file type/);
    assert.match(validateFileClient({ name: "empty.csv", size: 0 }, CONFIG) ?? "", /empty/);
    assert.match(validateFileClient({ name: "big.csv", size: 6_000_000 }, CONFIG) ?? "", /the file is 6\.0 MB; the limit is 5\.0 MB/i);
  });
  it("formats sizes", () => {
    assert.equal(formatBytes(512), "512 B");
    assert.equal(formatBytes(2048), "2.0 KB");
    assert.equal(formatBytes(5_000_000), "5.0 MB");
  });
});

// --- Analyze with AI ----------------------------------------------------------

describe("analyzeAvailability", () => {
  it("needs an uploaded, valid file", () => {
    assert.equal(analyzeAvailability(null, idle).enabled, false);
    assert.match(analyzeAvailability(null, idle).reason, /Upload a Freshworks export/);
    assert.equal(analyzeAvailability(report(), idle).enabled, true);
  });
  it("is off for a file with validation errors, and says to fix it", () => {
    const bad = report({ can_analyze: false, validation: { valid: false, errors: [{ code: "x", message: "m" }], warnings: [], fields_present: [] } });
    const result = analyzeAvailability(bad, idle);
    assert.equal(result.enabled, false);
    assert.match(result.reason, /Fix the file errors/);
  });
  it("is off while uploading, while a start is in flight and while the analysis runs", () => {
    assert.equal(analyzeAvailability(report(), { starting: false, uploading: true }).enabled, false);
    assert.equal(analyzeAvailability(report(), { starting: true, uploading: false }).enabled, false);
    assert.equal(analyzeAvailability(report({ status: "ANALYZING", can_analyze: false }), idle).enabled, false);
  });
  it("is off once analyzed, and back on after a failure", () => {
    assert.equal(analyzeAvailability(completed(), idle).enabled, false);
    assert.equal(analyzeAvailability(report({ status: "FAILED", error: "x" }), idle).enabled, true);
  });
});

// --- Send Email ---------------------------------------------------------------

describe("sendAvailability", () => {
  it("is disabled until an analysis has completed successfully", () => {
    assert.equal(sendAvailability(null, CONFIG, false).enabled, false);
    assert.equal(sendAvailability(report(), CONFIG, false).enabled, false);
    assert.equal(sendAvailability(report({ status: "ANALYZING" }), CONFIG, false).enabled, false);
    assert.equal(sendAvailability(report({ status: "FAILED", error: "x" }), CONFIG, false).enabled, false);
    assert.match(sendAvailability(report(), CONFIG, false).reason, /Analyze the file successfully/);
  });
  it("is enabled for a completed, unsent report", () => {
    assert.deepEqual(sendAvailability(completed(), CONFIG, false), { enabled: true, reason: "" });
  });
  it("is disabled while it is being sent, and after it was sent", () => {
    assert.equal(sendAvailability(completed(), CONFIG, true).enabled, false);
    const sending = completed({ email: { ...completed().email, status: "SENDING", can_send: false } });
    assert.equal(sendAvailability(sending, CONFIG, false).enabled, false);
    const sent = completed({ email: { ...completed().email, status: "SENT", can_send: false } });
    assert.match(sendAvailability(sent, CONFIG, false).reason, /already been sent/);
  });
  it("is disabled when the email configuration is missing, with the reason", () => {
    const missing: ReportConfig = { ...CONFIG, email: { configured: false, recipient: null, problem: "IT_REPORT_DISTRIBUTION_EMAIL is not set." } };
    const result = sendAvailability(completed(), missing, false);
    assert.equal(result.enabled, false);
    assert.match(result.reason, /IT_REPORT_DISTRIBUTION_EMAIL/);
  });
  it("follows the server when it says the report cannot be sent", () => {
    const blocked = completed({ email: { ...completed().email, can_send: false, blocked_reason: "Email sending is not configured." } });
    assert.equal(sendAvailability(blocked, CONFIG, false).reason, "Email sending is not configured.");
  });
  it("can be retried after a failed send", () => {
    const failed = completed({ email: { ...completed().email, status: "FAILED", attempts: 1, error: "Nothing was sent.", can_send: true } });
    assert.equal(sendAvailability(failed, CONFIG, false).enabled, true);
  });
});

describe("confirmationSummary", () => {
  it("states what is about to be sent, and leaves out what is not available", () => {
    const lines = confirmationSummary(completed());
    assert.deepEqual(lines, [
      "6 tickets in export.csv",
      "4 open; 1 overdue",
      "1 Critical and 2 High, as recommended by the AI",
      "2 rows in the employee workload table",
      "Waiting on vendor and OM/user issues: not available for this report",
    ]);
    // The email lists no priority action items, so however many open tickets there are, the dialog does not mention any.
    const many = completed({ analysis: analysis({ tickets: [ticket("1"), ticket("2"), ticket("3")], action_ids: ["1"] }) });
    assert.doesNotMatch(confirmationSummary(many).join(" "), /action item|more open ticket/);
    const sparse = completed({ analysis: analysis({ summary: { ...analysis().summary, overdue: null, ai_critical: null, ai_high: null }, employees: [] }) });
    assert.deepEqual(confirmationSummary(sparse), [
      "6 tickets in export.csv", "4 open; overdue count not available", "0 rows in the employee workload table",
      "Waiting on vendor and OM/user issues: not available for this report",
    ]);
  });
  it("says how many vendor and OM/user rows the email will carry", () => {
    const item = { ticket_id: "1", subject: "s", assignee: "Dana", days_open: 3 };
    const followup = {
      vendor: [{ ...item, status: "waiting_on_vendor" as const, evidence: "e", next_action: "n" }],
      provider: [
        { ...item, issue: "i", impact: "m", next_action: "n", priority: null, freshworks_priority: null },
        { ...item, ticket_id: "2", issue: "i", impact: "m", next_action: "n", priority: null, freshworks_priority: null },
      ],
      daily_work: [], status: "complete" as const, classified: 6, failed_batches: 0, dropped_answers: 0,
    };
    const lines = confirmationSummary(completed({ analysis: analysis({ followup }) }));
    assert.deepEqual(lines.slice(-2), ["1 ticket waiting on a vendor or needing review", "2 OM/user issues"]);
    const failed = confirmationSummary(completed({ analysis: analysis({ followup: { ...followup, status: "unavailable" as const } }) }));
    assert.equal(failed.at(-1), "Waiting on vendor and OM/user issues: not available for this report");
  });
  it("is empty without an analysis", () => assert.deepEqual(confirmationSummary(report()), []));
});

describe("deliveryStatus", () => {
  const email = completed().email;
  const when = (iso: string) => `<${iso}>`;
  it("says sent only for a recorded send, with the time", () => {
    const sent = deliveryStatus({ ...email, status: "SENT", recipient: "it-team@hfmg.net", sent_at: "2026-10-22T11:00:00Z" }, when);
    assert.deepEqual(sent, { tone: "success", text: "Sent to it-team@hfmg.net on <2026-10-22T11:00:00Z>." });
  });
  it("never says sent for anything else", () => {
    for (const status of ["NOT_SENT", "SENDING", "FAILED"] as const) {
      const result = deliveryStatus({ ...email, status, error: "boom", attempts: 2 }, when);
      assert.doesNotMatch(result.text, /^Sent to/);
    }
    assert.equal(deliveryStatus({ ...email, status: "SENDING" }, when).tone, "busy");
    assert.match(deliveryStatus({ ...email, status: "FAILED", error: "Nothing was sent.", attempts: 2 }, when).text, /Nothing was sent\. \(attempt 2\)/);
    assert.match(deliveryStatus(email, when).text, /Nothing is emailed until you press Send Email/);
  });
});

// --- the page's own state -----------------------------------------------------

function run(events: PageEvent[], from: PageState = initialPageState("r1")): PageState {
  return events.reduce(pageReducer, from);
}

describe("pageReducer", () => {
  it("ignores a second analyze click while the first is starting", () => {
    const once = run([{ type: "analyze-started" }]);
    assert.equal(once.starting, true);
    assert.equal(pageReducer(once, { type: "analyze-started" }), once);
    assert.equal(run([{ type: "analyze-started" }, { type: "analyze-settled" }]).starting, false);
  });
  it("shows an analyze failure, but not the refusal of a duplicate", () => {
    assert.deepEqual(run([{ type: "analyze-started" }, { type: "analyze-settled", error: "bad" }]).message, { kind: "error", text: "bad" });
    assert.equal(run([{ type: "analyze-started" }, { type: "analyze-settled" }]).message, null);
  });
  it("ignores a second send while one is in flight, and keeps the dialog open until it finishes", () => {
    const sending = run([{ type: "confirm-opened" }, { type: "send-started" }]);
    assert.equal(sending.sending, true);
    assert.equal(pageReducer(sending, { type: "send-started" }), sending);
    assert.equal(pageReducer(sending, { type: "confirm-closed" }).confirmOpen, true);
    assert.equal(pageReducer(sending, { type: "confirm-opened" }), sending);
  });
  it("closes the dialog and reports the outcome when the server answers", () => {
    const ok = run([{ type: "confirm-opened" }, { type: "send-started" }, { type: "send-settled", ok: true, text: "The report was sent to it-team@hfmg.net." }]);
    assert.deepEqual([ok.sending, ok.confirmOpen, ok.message], [false, false, { kind: "success", text: "The report was sent to it-team@hfmg.net." }]);
    const failed = run([{ type: "send-started" }, { type: "send-settled", ok: false, text: "Nothing was sent." }]);
    assert.deepEqual(failed.message, { kind: "error", text: "Nothing was sent." });
  });
  it("can cancel the dialog when nothing is being sent", () => {
    assert.equal(run([{ type: "confirm-opened" }, { type: "confirm-closed" }]).confirmOpen, false);
  });
  it("a new upload replaces the report and clears every flag", () => {
    const busy = run([{ type: "analyze-started" }, { type: "confirm-opened" }]);
    assert.deepEqual(pageReducer(busy, { type: "upload-finished", reportId: "r2" }), initialPageState("r2"));
  });
  it("a failed upload keeps the current report and says why", () => {
    const state = run([{ type: "upload-started" }, { type: "upload-failed", error: "too big" }]);
    assert.deepEqual([state.reportId, state.uploading, state.message], ["r1", false, { kind: "error", text: "too big" }]);
  });
  it("forgets a report the server no longer has", () => {
    assert.deepEqual(run([{ type: "report-missing" }]), initialPageState(null));
  });
});

// --- the report table ---------------------------------------------------------

describe("report table", () => {
  const tickets = [
    ticket("10", { ai: { ...ticket("10").ai, priority: "Low" } }),
    ticket("2", { assignee: null, overdue: true, ai: { ...ticket("2").ai, priority: "Critical" } }),
    ticket("3", { assignee: "Sam", ai: { status: "not_analyzed", priority: null, reason: null, impact: null, next_action: null, issue_label: null } }),
    ticket("1", { ai: { ...ticket("1").ai, priority: "Critical" } }),
    ticket("20", { ai: { ...ticket("20").ai, priority: "High" } }),
  ];

  // The server stores tickets in work order; the page never re-sorts them, so the dashboard and the email cannot disagree.
  it("keeps the order the server stored and shows only the short list until asked for more", () => {
    const stored = [ticket("2"), ticket("1"), ticket("20"), ticket("10"), ticket("3")];
    const view = { showAll: false, filters: NO_FILTERS };
    assert.deepEqual(visibleTickets(stored, ["1", "2", "10"], view).map((t) => t.ticket_id), ["2", "1", "10"]);
    assert.deepEqual(visibleTickets(stored, ["1", "2", "10"], { ...view, showAll: true }).map((t) => t.ticket_id), ["2", "1", "20", "10", "3"]);
  });
  it("searches every open ticket, not just the short list, as soon as a filter is set", () => {
    const stored = [ticket("2"), ticket("1", { assignee: "Sam" })];
    const found = visibleTickets(stored, ["2"], { showAll: false, filters: { ...NO_FILTERS, assignee: "Sam" } });
    assert.deepEqual(found.map((t) => t.ticket_id), ["1"]);
  });
  it("shows nothing for a short list that names no ticket, instead of inventing one", () => {
    assert.deepEqual(visibleTickets([ticket("1")], [], { showAll: false, filters: NO_FILTERS }), []);
  });
  it("filters by AI priority, including tickets with no recommendation", () => {
    assert.deepEqual(filterTickets(tickets, { ...NO_FILTERS, priority: "Critical" }).map((t) => t.ticket_id), ["2", "1"]);
    assert.deepEqual(filterTickets(tickets, { ...NO_FILTERS, priority: "none" }).map((t) => t.ticket_id), ["3"]);
  });
  it("filters by assignee, unassigned and overdue", () => {
    assert.deepEqual(filterTickets(tickets, { ...NO_FILTERS, assignee: "Sam" }).map((t) => t.ticket_id), ["3"]);
    assert.deepEqual(filterTickets(tickets, { ...NO_FILTERS, assignee: "unassigned" }).map((t) => t.ticket_id), ["2"]);
    assert.deepEqual(filterTickets(tickets, { ...NO_FILTERS, overdueOnly: true }).map((t) => t.ticket_id), ["2"]);
  });
  it("searches id, subject, person and issue label, ignoring case", () => {
    assert.deepEqual(filterTickets(tickets, { ...NO_FILTERS, query: "subject 20" }).map((t) => t.ticket_id), ["20"]);
    assert.deepEqual(filterTickets(tickets, { ...NO_FILTERS, query: "SAM" }).map((t) => t.ticket_id), ["3"]);
    assert.equal(filterTickets(tickets, { ...NO_FILTERS, query: "nothing like this" }).length, 0);
  });
  it("lists assignees once, sorted, and knows when a filter is on", () => {
    assert.deepEqual(assigneeOptions(tickets), ["Dana", "Sam"]);
    assert.equal(hasFilters(NO_FILTERS), false);
    assert.equal(hasFilters({ ...NO_FILTERS, query: " x " }), true);
  });
  it("never shows an unavailable number as zero", () => {
    assert.equal(formatCount(null), "Not available");
    assert.equal(formatCount(0), "0");
    assert.equal(ageLabel(null), "Not available");
    assert.equal(ageLabel(1), "1 day");
    assert.equal(ageLabel(0), "0 days");
  });
});

// --- the weekly follow-up groups -----------------------------------------------------

describe("weekly follow-up", () => {
  const followup = (overrides: Partial<Followup> = {}): Followup => ({
    vendor: [], provider: [], daily_work: [], status: "complete", classified: 6, failed_batches: 0, dropped_answers: 0, ...overrides,
  });

  it("shows ten rows of a group until the user asks for all of them", () => {
    const rows = Array.from({ length: 25 }, (_, i) => ({ ticket_id: String(i + 1) }));
    assert.equal(FOLLOWUP_ROWS, 10);
    assert.deepEqual(limitRows(rows, false).map((r) => r.ticket_id), rows.slice(0, 10).map((r) => r.ticket_id));
    assert.equal(limitRows(rows, true).length, 25);
    assert.equal(limitRows(rows.slice(0, 4), false).length, 4);
  });

  it("keeps the order the server stored", () => {
    const rows = [{ ticket_id: "9" }, { ticket_id: "2" }, { ticket_id: "5" }];
    assert.deepEqual(limitRows(rows, false).map((r) => r.ticket_id), ["9", "2", "5"]);
  });

  it("labels a vendor ticket as waiting only when it is, and otherwise as needing review", () => {
    assert.equal(vendorStatusLabel("waiting_on_vendor"), "Waiting on vendor");
    assert.equal(vendorStatusLabel("needs_review"), "Needs review");
  });

  it("never turns an unknown age into a number", () => {
    assert.equal(daysOpenLabel(null), "Not available");
    assert.equal(daysOpenLabel(0), "0 days");
    assert.equal(daysOpenLabel(1), "1 day");
    assert.equal(daysOpenLabel(21), "21 days");
  });

  it("explains an empty or partial vendor/provider result instead of leaving it to look like 'none'", () => {
    assert.equal(followupNote(followup()), undefined);
    assert.equal(followupNote(followup({ status: "skipped", classified: 0 })), undefined);
    assert.match(followupNote(followup({ status: "unavailable", classified: 0 })) ?? "", /could not be determined.*priority issues below are not affected/);
    assert.match(followupNote(followup({ status: "partial", classified: 4 })) ?? "", /cover 4 tickets.*may be incomplete/);
  });
});

/** Filtering and wording for the report tables. Pure, so it can be tested without a browser. */

import type { AiPriority, ReportTicket } from "../../types/ticketReport.ts";

export const AI_PRIORITIES: AiPriority[] = ["Critical", "High", "Medium", "Low"];

/** "Not available" for a number the export could not establish; never a 0 that looks like a fact. */
export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? "Not available" : String(value);
}

export function ageLabel(days: number | null): string {
  if (days === null) return "Not available";
  return days === 1 ? "1 day" : `${days} days`;
}

export interface TicketFilters {
  priority: AiPriority | "all" | "none";
  assignee: string; // "all", "unassigned" or a name
  query: string;
  overdueOnly: boolean;
}

export const NO_FILTERS: TicketFilters = { priority: "all", assignee: "all", query: "", overdueOnly: false };

export function filterTickets(tickets: ReportTicket[], filters: TicketFilters): ReportTicket[] {
  const query = filters.query.trim().toLowerCase();
  return tickets.filter((t) => {
    if (filters.priority === "none" ? t.ai.priority !== null : filters.priority !== "all" && t.ai.priority !== filters.priority) return false;
    if (filters.assignee === "unassigned" ? t.assignee !== null : filters.assignee !== "all" && t.assignee !== filters.assignee) return false;
    if (filters.overdueOnly && t.overdue !== true) return false;
    if (!query) return true;
    return [t.ticket_id, t.subject, t.assignee ?? "", t.ai.issue_label ?? ""].some((text) => text.toLowerCase().includes(query));
  });
}

export function assigneeOptions(tickets: ReportTicket[]): string[] {
  return [...new Set(tickets.map((t) => t.assignee).filter((name): name is string => !!name))].sort((a, b) => a.localeCompare(b));
}

export function hasFilters(filters: TicketFilters): boolean {
  return filters.priority !== "all" || filters.assignee !== "all" || filters.query.trim() !== "" || filters.overdueOnly;
}

/**
 * The tickets the action table shows. The server stores them in work order (Critical to Low, overdue and oldest
 * first) and names the short list; this never re-sorts, so the dashboard and the email cannot disagree.
 *
 * Without "show all" and without a filter, only the short list is shown. A filter or "show all" searches every open ticket.
 */
export function visibleTickets(
  tickets: ReportTicket[],
  actionIds: string[],
  view: { showAll: boolean; filters: TicketFilters },
): ReportTicket[] {
  if (view.showAll || hasFilters(view.filters)) return filterTickets(tickets, view.filters);
  const wanted = new Set(actionIds);
  return tickets.filter((t) => wanted.has(t.ticket_id));
}

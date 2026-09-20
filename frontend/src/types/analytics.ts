import type { AISummaryStatus, Priority, TicketSource, TicketStatus } from "./ticket";

/** Every KPI/insight section carries its own status rather than a bare
 * number — "blocked" means a real business definition is missing
 * server-side (API_SPEC.md §12), never a zero standing in for "unknown". */
export interface KpiValue {
  status: "ready" | "blocked";
  value: number | null;
  blocked_reason: string | null;
}

export interface KpiReport {
  open_tickets: KpiValue;
  tickets_today: KpiValue;
  calls_today: KpiValue;
  escalations: KpiValue;
  ai_resolution_rate: KpiValue;
}

export interface ActivityItem {
  type: "ticket_created";
  occurred_at: string;
  ticket_id: string;
  ticket_number: string;
  caller_name: string;
  category: string;
  priority: Priority;
  status: TicketStatus;
}

export interface RecentActivityResponse {
  items: ActivityItem[];
}

export interface CategoryBreakdownItem {
  category: string;
  count: number;
}

export interface PriorityBreakdownItem {
  priority: Priority;
  count: number;
}

export interface SourceBreakdownItem {
  source: TicketSource;
  count: number;
}

export interface CallsByDayItem {
  date: string;
  count: number;
}

export interface EscalationRateResponse {
  total_terminal_calls: number;
  escalated_calls: number;
  rate_percent: number;
}

export interface AiSummaryUsageItem {
  status: AISummaryStatus;
  count: number;
  percentage: number;
}

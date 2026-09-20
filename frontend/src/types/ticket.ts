export type Priority = "LOW" | "MEDIUM" | "HIGH" | "URGENT";

export type TicketStatus =
  | "NEW"
  | "OPEN"
  | "IN_PROGRESS"
  | "ON_HOLD"
  | "RESOLVED"
  | "CLOSED"
  | "CANCELLED";

export type AISummaryStatus = "DISABLED" | "PENDING" | "COMPLETED" | "FAILED";

// `source` is now serialized by TicketListItem/TicketRead (fixed in Backend
// Tier 0 — see BACKEND_GAP_ANALYSIS.md/WORK_LOG.md). Kept optional here
// rather than required so SourceBadge's existing "Unknown" fallback stays
// harmless if it's ever missing, but it should always be present now.
export type TicketSource = "WEB" | "PHONE" | "EMAIL" | "WALK_IN";

export interface Category {
  id: string;
  name: string;
  description: string | null;
  default_priority: Priority | null;
}

export interface TicketListItem {
  id: string;
  ticket_number: string;
  caller_name: string;
  category: Category;
  priority: Priority;
  status: TicketStatus;
  ai_summary_status: AISummaryStatus;
  source?: TicketSource;
  created_at: string;
}

export interface Ticket extends TicketListItem {
  phone_number: string;
  email: string | null;
  description: string;
  ai_summary: string | null;
  ai_summary_generated_at: string | null;
  updated_at: string;
}

export interface TicketPage {
  items: TicketListItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface TicketCreateInput {
  caller_name: string;
  phone_number: string;
  email?: string;
  category_id: string;
  priority?: Priority;
  description: string;
}

export const STATUS_OPTIONS: TicketStatus[] = [
  "NEW",
  "OPEN",
  "IN_PROGRESS",
  "ON_HOLD",
  "RESOLVED",
  "CLOSED",
  "CANCELLED",
];

export const PRIORITY_OPTIONS: Priority[] = ["LOW", "MEDIUM", "HIGH", "URGENT"];

export const SOURCE_OPTIONS: TicketSource[] = ["WEB", "PHONE", "EMAIL", "WALK_IN"];

/**
 * Mirrors `VALID_STATUS_TRANSITIONS` in backend/app/db/models.py exactly.
 * Used to only ever offer transitions the backend will actually accept —
 * `POST /tickets/{id}/status` returns 422 for anything outside this map, so
 * the Status Controls dropdown restricts its options to this list rather
 * than letting a user pick a transition that's guaranteed to fail.
 */
export const VALID_STATUS_TRANSITIONS: Record<TicketStatus, TicketStatus[]> = {
  NEW: ["OPEN", "IN_PROGRESS", "CANCELLED"],
  OPEN: ["IN_PROGRESS", "ON_HOLD", "RESOLVED", "CANCELLED"],
  IN_PROGRESS: ["ON_HOLD", "RESOLVED", "CANCELLED"],
  ON_HOLD: ["IN_PROGRESS", "RESOLVED", "CANCELLED"],
  RESOLVED: ["CLOSED", "IN_PROGRESS"],
  CLOSED: [],
  CANCELLED: [],
};

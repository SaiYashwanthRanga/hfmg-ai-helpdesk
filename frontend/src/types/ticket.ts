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

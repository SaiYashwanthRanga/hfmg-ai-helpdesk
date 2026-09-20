import type {
  Category,
  Priority,
  Ticket,
  TicketCreateInput,
  TicketPage,
  TicketSource,
  TicketStatus,
} from "../types/ticket";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? body.error?.message ?? detail;
    } catch {
      // response body wasn't JSON; fall back to statusText
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  listCategories: () => request<Category[]>("/categories"),

  // `priority`/`source`/`q` are now real, working filters on the backend
  // (Backend Tier 0 — see BACKEND_GAP_ANALYSIS.md/WORK_LOG.md); `q` matches
  // via ILIKE, not the full-text index API_SPEC.md originally implied (that
  // index doesn't exist). TicketFilters/TicketSearch (Phase 3) still ship
  // with those controls disabled — enabling them is logged as a Phase 3
  // follow-up in FRONTEND_GAP_REPORT.md rather than done inline here, since
  // this session's scope is Phases 4-8.
  listTickets: (
    params: {
      page?: number;
      page_size?: number;
      status?: TicketStatus;
      category_id?: string;
      priority?: Priority;
      source?: TicketSource;
      q?: string;
    } = {},
  ) => {
    const search = new URLSearchParams();
    if (params.page) search.set("page", String(params.page));
    if (params.page_size) search.set("page_size", String(params.page_size));
    if (params.status) search.set("status", params.status);
    if (params.category_id) search.set("category_id", params.category_id);
    if (params.priority) search.set("priority", params.priority);
    if (params.source) search.set("source", params.source);
    if (params.q) search.set("q", params.q);
    const qs = search.toString();
    return request<TicketPage>(`/tickets${qs ? `?${qs}` : ""}`);
  },

  getTicket: (id: string) => request<Ticket>(`/tickets/${id}`),

  createTicket: (payload: TicketCreateInput) =>
    request<Ticket>("/tickets", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateTicketStatus: (id: string, newStatus: TicketStatus) =>
    request<Ticket>(`/tickets/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ status: newStatus }),
    }),
};

export { ApiError };

import type {
  Category,
  Ticket,
  TicketCreateInput,
  TicketPage,
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

  listTickets: (params: { page?: number; status?: TicketStatus } = {}) => {
    const search = new URLSearchParams();
    if (params.page) search.set("page", String(params.page));
    if (params.status) search.set("status", params.status);
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

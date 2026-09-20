import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { Priority, Ticket, TicketSource, TicketStatus } from "../types/ticket";

export function useCategoriesQuery() {
  return useQuery({ queryKey: ["categories"], queryFn: api.listCategories });
}

export interface TicketListParams {
  page: number;
  status: TicketStatus | "";
  categoryId: string;
  priority: Priority | "";
  source: TicketSource | "";
  q: string;
}

export function useTicketsQuery(params: TicketListParams) {
  return useQuery({
    queryKey: ["tickets", params],
    queryFn: () =>
      api.listTickets({
        page: params.page,
        status: params.status || undefined,
        category_id: params.categoryId || undefined,
        priority: params.priority || undefined,
        source: params.source || undefined,
        q: params.q || undefined,
      }),
    placeholderData: (previous) => previous, // avoids a table-clearing flash between page/filter changes
  });
}

export function useTicketQuery(ticketId: string | null) {
  return useQuery({
    queryKey: ["tickets", "detail", ticketId],
    queryFn: () => api.getTicket(ticketId as string),
    enabled: ticketId !== null,
    // AI summary lands asynchronously after creation — poll while pending
    // so it appears without a manual refresh (mirrors the pre-Phase-3 page).
    refetchInterval: (query) => (query.state.data?.ai_summary_status === "PENDING" ? 3000 : false),
  });
}

export function useUpdateTicketStatusMutation(ticketId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (status: TicketStatus) => api.updateTicketStatus(ticketId, status),
    onSuccess: (updated: Ticket) => {
      queryClient.setQueryData(["tickets", "detail", ticketId], updated);
      // Ticket list rows include `status` — invalidate rather than patch
      // every cached page/filter combination by hand.
      queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[0] === "tickets" && query.queryKey[1] !== "detail",
      });
    },
  });
}

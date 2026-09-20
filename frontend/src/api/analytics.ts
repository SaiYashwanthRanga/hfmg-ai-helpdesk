import { useQuery } from "@tanstack/react-query";
import type {
  AiSummaryUsageItem,
  CallsByDayItem,
  CategoryBreakdownItem,
  EscalationRateResponse,
  KpiReport,
  PriorityBreakdownItem,
  RecentActivityResponse,
  SourceBreakdownItem,
} from "../types/analytics";
import { ApiError } from "./client";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new ApiError(response.statusText, response.status);
  }
  return (await response.json()) as T;
}

export const analyticsApi = {
  getKpis: () => get<KpiReport>("/analytics/kpis"),
  getRecentActivity: (limit = 10) => get<RecentActivityResponse>(`/analytics/recent-activity?limit=${limit}`),
  getTicketsByCategory: (days = 30) => get<{ items: CategoryBreakdownItem[] }>(`/analytics/tickets-by-category?days=${days}`),
  getTicketsByPriority: (days = 30) => get<{ items: PriorityBreakdownItem[] }>(`/analytics/tickets-by-priority?days=${days}`),
  getTicketsBySource: (days = 30) => get<{ items: SourceBreakdownItem[] }>(`/analytics/tickets-by-source?days=${days}`),
  getCallsByDay: (days = 30) => get<{ items: CallsByDayItem[] }>(`/analytics/calls-by-day?days=${days}`),
  getEscalationRate: (days = 30) => get<EscalationRateResponse>(`/analytics/escalation-rate?days=${days}`),
  getAiSummaryUsage: (days = 30) => get<{ items: AiSummaryUsageItem[] }>(`/analytics/ai-summary-usage?days=${days}`),
};

export function useKpisQuery() {
  return useQuery({ queryKey: ["analytics", "kpis"], queryFn: analyticsApi.getKpis, refetchInterval: 30_000 });
}

export function useRecentActivityQuery(limit = 10) {
  return useQuery({
    queryKey: ["analytics", "recent-activity", limit],
    queryFn: () => analyticsApi.getRecentActivity(limit),
  });
}

export function useTicketsByCategoryQuery(days: number) {
  return useQuery({ queryKey: ["analytics", "tickets-by-category", days], queryFn: () => analyticsApi.getTicketsByCategory(days) });
}

export function useTicketsByPriorityQuery(days: number) {
  return useQuery({ queryKey: ["analytics", "tickets-by-priority", days], queryFn: () => analyticsApi.getTicketsByPriority(days) });
}

export function useTicketsBySourceQuery(days: number) {
  return useQuery({ queryKey: ["analytics", "tickets-by-source", days], queryFn: () => analyticsApi.getTicketsBySource(days) });
}

export function useCallsByDayQuery(days: number) {
  return useQuery({ queryKey: ["analytics", "calls-by-day", days], queryFn: () => analyticsApi.getCallsByDay(days) });
}

export function useEscalationRateQuery(days: number) {
  return useQuery({ queryKey: ["analytics", "escalation-rate", days], queryFn: () => analyticsApi.getEscalationRate(days) });
}

export function useAiSummaryUsageQuery(days: number) {
  return useQuery({ queryKey: ["analytics", "ai-summary-usage", days], queryFn: () => analyticsApi.getAiSummaryUsage(days) });
}

import { useQuery } from "@tanstack/react-query";
import type { AIInsightsResponse } from "../types/aiInsights";
import { ApiError } from "./client";
import { apiFetch } from "./http";


export async function fetchAiInsights(days = 30): Promise<AIInsightsResponse> {
  const response = await apiFetch(`/ai-insights?days=${days}`);
  if (!response.ok) {
    throw new ApiError(response.statusText, response.status);
  }
  return (await response.json()) as AIInsightsResponse;
}

export function useAiInsightsQuery(days = 30) {
  return useQuery({ queryKey: ["ai-insights", days], queryFn: () => fetchAiInsights(days) });
}

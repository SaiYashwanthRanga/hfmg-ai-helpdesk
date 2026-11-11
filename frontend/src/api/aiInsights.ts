import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AIAnalysisResponse } from "../types/aiInsights";
import { ApiError } from "./client";
import { apiFetch } from "./http";

async function analysis(days: number, refresh: boolean): Promise<AIAnalysisResponse> {
  const response = refresh
    ? await apiFetch(`/ai-insights/analysis/refresh?days=${days}`, { method: "POST" })
    : await apiFetch(`/ai-insights/analysis?days=${days}`);
  if (!response.ok) {
    throw new ApiError(response.statusText, response.status);
  }
  return (await response.json()) as AIAnalysisResponse;
}

const analysisKey = (days: number) => ["ai-insights", "analysis", days];

/**
 * The model's reading of the last `days` days of tickets. The first request
 * for a window waits on the model (several seconds); the server then keeps
 * the reading, so this is not retried or refetched on focus.
 */
export function useAiAnalysisQuery(days = 30) {
  return useQuery({
    queryKey: analysisKey(days),
    queryFn: () => analysis(days, false),
    staleTime: 5 * 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

/** Ask the model again now; every view of this window gets the new reading. */
export function useRefreshAiAnalysis(days = 30) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => analysis(days, true),
    onSuccess: (data) => queryClient.setQueryData(analysisKey(days), data),
  });
}

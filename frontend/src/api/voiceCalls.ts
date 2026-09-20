import { useQuery } from "@tanstack/react-query";
import type { VoiceCallDetail, VoiceCallPage, VoiceCallState, VoiceCallSummary } from "../types/voiceCall";
import { ApiError } from "./client";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // not JSON; fall back to statusText
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export interface VoiceCallListParams {
  page?: number;
  page_size?: number;
  state?: VoiceCallState;
  escalated?: boolean;
}

export const voiceCallsApi = {
  list: (params: VoiceCallListParams = {}) => {
    const search = new URLSearchParams();
    if (params.page) search.set("page", String(params.page));
    if (params.page_size) search.set("page_size", String(params.page_size));
    if (params.state) search.set("state", params.state);
    if (params.escalated !== undefined) search.set("escalated", String(params.escalated));
    const qs = search.toString();
    return get<VoiceCallPage>(`/voice-calls${qs ? `?${qs}` : ""}`);
  },
  getSummary: () => get<VoiceCallSummary>("/voice-calls/summary"),
  get: (id: string) => get<VoiceCallDetail>(`/voice-calls/${id}`),
};

export function useVoiceCallsQuery(params: VoiceCallListParams) {
  return useQuery({
    queryKey: ["voice-calls", params],
    queryFn: () => voiceCallsApi.list(params),
    placeholderData: (previous) => previous,
  });
}

export function useVoiceCallSummaryQuery() {
  return useQuery({ queryKey: ["voice-calls", "summary"], queryFn: voiceCallsApi.getSummary, refetchInterval: 30_000 });
}

export function useVoiceCallQuery(id: string | null) {
  return useQuery({
    queryKey: ["voice-calls", "detail", id],
    queryFn: () => voiceCallsApi.get(id as string),
    enabled: id !== null,
  });
}

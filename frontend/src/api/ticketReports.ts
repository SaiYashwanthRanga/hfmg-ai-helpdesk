import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReportConfig, SendResult, TicketReportRecord } from "../types/ticketReport";
import { ApiError } from "./client";
import { apiFetch } from "./http";

const BASE = "/reports";
/** How often a running analysis is checked for progress. */
const POLL_MS = 1500;

/** FastAPI's `detail` is a string for our own errors and a list for request-validation errors. */
function describeError(payload: unknown, fallback: string): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) return String((detail[0] as { msg?: string }).msg ?? fallback);
  return fallback;
}

async function request<T>(method: string, path: string, body?: FormData): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(`${BASE}${path}`, { method, body });
  } catch {
    throw new ApiError("Could not reach the server. Check the connection and try again.", 0);
  }
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      // not JSON; keep the status text
    }
    throw new ApiError(describeError(payload, response.statusText || "The request failed."), response.status);
  }
  return (await response.json()) as T;
}

export const ticketReportsApi = {
  config: () => request<ReportConfig>("GET", "/config"),
  upload: (file: File) => {
    const form = new FormData();
    form.set("file", file, file.name);
    return request<TicketReportRecord>("POST", "/upload", form);
  },
  analyze: (id: string) => request<TicketReportRecord>("POST", `/${id}/analyze`),
  get: (id: string) => request<TicketReportRecord>("GET", `/${id}`),
  send: (id: string) => request<SendResult>("POST", `/${id}/send`),
};

export function useReportConfigQuery() {
  return useQuery({ queryKey: ["ticket-report-config"], queryFn: ticketReportsApi.config, staleTime: 30_000 });
}

/** The report, re-read every few seconds while its analysis is running. */
export function useReportQuery(id: string | null) {
  return useQuery({
    queryKey: ["ticket-report", id],
    queryFn: () => ticketReportsApi.get(id as string),
    enabled: id !== null,
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
    refetchInterval: (query) => (query.state.data?.status === "ANALYZING" ? POLL_MS : false),
  });
}

export function useTicketReportMutations() {
  const queryClient = useQueryClient();
  const keep = (report: TicketReportRecord) => queryClient.setQueryData(["ticket-report", report.id], report);
  const refresh = (id: string) => queryClient.invalidateQueries({ queryKey: ["ticket-report", id] });
  return {
    upload: useMutation({ mutationFn: ticketReportsApi.upload, onSuccess: keep }),
    analyze: useMutation({ mutationFn: ticketReportsApi.analyze, onSuccess: keep, onError: (_e, id) => refresh(id) }),
    // A failed send is recorded on the report, so it is re-read either way.
    send: useMutation({
      mutationFn: ticketReportsApi.send,
      onSuccess: (result) => keep(result.report),
      onError: (_e, id) => refresh(id),
    }),
  };
}

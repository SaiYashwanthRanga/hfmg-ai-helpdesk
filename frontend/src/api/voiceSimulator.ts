import { useQuery } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import type {
  ApiCallRecord,
  AudioResponse,
  ClientMetrics,
  MockCaller,
  ProcessRequest,
  ProcessResponse,
  RandomIssue,
  SessionDetail,
  SimulatorConfig,
  SimulatorMetrics,
  SimulatorSession,
  SimulatorStats,
  StartRequest,
  StartResponse,
  TurnTimings,
} from "../types/voiceSimulator";
import { ApiError } from "./client";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";
const BASE = "/voice-simulator";

/** Absolute URL for a reply's `speech_path` (streamed audio). */
export function speechUrl(speechPath: string): string {
  return `${API_BASE_URL}${speechPath}`;
}

// --- API call log (Conversation Inspector → API calls) ---------------------

const LOG_LIMIT = 200;
let callLog: ApiCallRecord[] = [];
const logListeners = new Set<() => void>();

function appendLog(record: ApiCallRecord) {
  callLog = [...callLog.slice(-(LOG_LIMIT - 1)), record];
  for (const listener of logListeners) listener();
}

export function clearApiCallLog() {
  callLog = [];
  for (const listener of logListeners) listener();
}

export function useApiCallLog(): ApiCallRecord[] {
  return useSyncExternalStore(
    (listener) => {
      logListeners.add(listener);
      return () => logListeners.delete(listener);
    },
    () => callLog,
  );
}

/**
 * Like api/client.ts `request`, plus timing and a log entry per call. The
 * body may be FormData (audio upload), in which case the browser sets the
 * multipart Content-Type itself.
 */
async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const started = performance.now();
  const isForm = body instanceof FormData;
  let status: number | null = null;
  let error: string | null = null;
  try {
    const response = await fetch(`${API_BASE_URL}${BASE}${path}`, {
      method,
      signal,
      headers: isForm || body === undefined ? undefined : { "Content-Type": "application/json" },
      body: isForm ? body : body === undefined ? undefined : JSON.stringify(body),
    });
    status = response.status;
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const payload = await response.json();
        detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail ?? payload);
      } catch {
        // not JSON; keep statusText
      }
      error = detail;
      throw new ApiError(detail, response.status);
    }
    return (await response.json()) as T;
  } catch (err) {
    if (!error) error = err instanceof Error ? err.message : String(err);
    throw err;
  } finally {
    appendLog({
      id: crypto.randomUUID(),
      method,
      path: `${BASE}${path}`,
      status,
      durationMs: Math.round(performance.now() - started),
      at: new Date().toISOString(),
      error,
    });
  }
}

export const simulatorApi = {
  config: () => request<SimulatorConfig>("GET", "/config"),
  start: (body: StartRequest) => request<StartResponse>("POST", "/start", body),
  audio: (sessionId: string, turnClientId: string, audio: Blob, signal?: AbortSignal) => {
    const form = new FormData();
    form.set("session_id", sessionId);
    form.set("turn_client_id", turnClientId);
    const extension = audio.type.includes("mp4") ? "m4a" : audio.type.includes("ogg") ? "ogg" : "webm";
    form.set("audio", audio, `utterance.${extension}`);
    return request<AudioResponse>("POST", "/audio", form, signal);
  },
  process: (body: ProcessRequest, signal?: AbortSignal) => request<ProcessResponse>("POST", "/process", body, signal),
  end: (sessionId: string, reason: "user_hangup" | "agent_hangup" | "error" | "cleared") =>
    request<SimulatorSession>("POST", "/end", { session_id: sessionId, reason }),
  /**
   * Fire-and-forget hang-up that survives the page unloading (tab closed,
   * navigation). The idle sweeper catches anything this misses.
   */
  endOnUnload: (sessionId: string) => {
    void fetch(`${API_BASE_URL}${BASE}/end`, {
      method: "POST",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, reason: "user_hangup" }),
    }).catch(() => undefined);
  },
  session: (sessionId: string) => request<SessionDetail>("GET", `/session/${sessionId}`),
  metrics: (sessionId: string) => request<SimulatorMetrics>("GET", `/metrics/${sessionId}`),
  clientMetrics: (sessionId: string, body: ClientMetrics) =>
    request<TurnTimings>("POST", `/session/${sessionId}/client-metrics`, body),
  stats: (hours = 24) => request<SimulatorStats>("GET", `/stats?hours=${hours}`),
  mockCaller: (seed: number, options: { category?: string; escalate?: boolean } = {}) => {
    const search = new URLSearchParams({ seed: String(seed) });
    if (options.category) search.set("category", options.category);
    if (options.escalate) search.set("escalate", "true");
    return request<MockCaller>("GET", `/mock-caller?${search}`);
  },
  randomIssue: (seed: number, category?: string) => {
    const search = new URLSearchParams({ seed: String(seed) });
    if (category) search.set("category", category);
    return request<RandomIssue>("GET", `/random-issue?${search}`);
  },
};

export function useSimulatorConfigQuery() {
  return useQuery({ queryKey: ["voice-simulator", "config"], queryFn: simulatorApi.config, retry: false });
}

export function useSimulatorMetricsQuery(sessionId: string | null, version: number) {
  return useQuery({
    queryKey: ["voice-simulator", "metrics", sessionId, version],
    queryFn: () => simulatorApi.metrics(sessionId as string),
    enabled: sessionId !== null,
    placeholderData: (previous) => previous,
  });
}

export function useSimulatorStatsQuery(hours = 24) {
  return useQuery({
    queryKey: ["voice-simulator", "stats", hours],
    queryFn: () => simulatorApi.stats(hours),
    refetchInterval: 15_000,
  });
}

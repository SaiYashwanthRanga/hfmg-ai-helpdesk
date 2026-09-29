/**
 * Latency stages, budgets and statistics for the AI Call Simulator.
 * Pure functions — unit-tested by frontend/tests/voiceSimulator.test.ts.
 */
import type { LatencyStage, StageStats, TurnTimings } from "../../types/voiceSimulator";

export interface StageDefinition {
  key: LatencyStage;
  label: string;
  /** Who measures it — shown in the tooltip so nobody compares clocks. */
  measuredBy: "browser" | "server";
  description: string;
  /** True when the simulator's number does not predict production (design D4). */
  notRepresentative?: boolean;
}

/** Stages shown in the latency panel, in pipeline order. */
export const STAGES: StageDefinition[] = [
  {
    key: "capture_ms",
    label: "Audio capture",
    measuredBy: "browser",
    description: "Caller stops speaking → audio sent (includes the silence wait in continuous mode).",
  },
  {
    key: "stt_ms",
    label: "STT",
    measuredBy: "server",
    description: "Speech-to-text provider call.",
    notRepresentative: true,
  },
  {
    key: "llm_ms",
    label: "LLM / agent",
    measuredBy: "server",
    description:
      "One orchestrator turn: NLU (a rule for yes/no, phone and clear emails; otherwise a model call), validation, state change and ticket creation.",
  },
  {
    key: "tts_ms",
    label: "TTS",
    measuredBy: "server",
    description: "Time to the first audio of the streamed reply (near zero for repeated lines, which are cached). Blank with the browser voice.",
    notRepresentative: true,
  },
  {
    key: "playback_start_ms",
    label: "Playback",
    measuredBy: "browser",
    description: "Reply received → first audio frame out.",
  },
  {
    key: "turn_total_ms",
    label: "Total turn",
    measuredBy: "browser",
    description: "Caller stops speaking → first agent audio. How long the caller waits in silence.",
  },
];

/** Secondary server metrics shown in the inspector and load test. */
export const SERVER_STAGES: StageDefinition[] = [
  { key: "queue_wait_ms", label: "Queue wait", measuredBy: "server", description: "Waiting for a database connection and the session lock." },
  { key: "ticket_create_ms", label: "Ticket creation", measuredBy: "server", description: "ticket_service.create_ticket, when a ticket was created." },
  { key: "server_total_ms", label: "Server total", measuredBy: "server", description: "Whole /process request on the server." },
];

export type BudgetLevel = "good" | "warn" | "fail" | "none";

/**
 * Thresholds from the production constraint: Twilio abandons a webhook at
 * ~15s and each NLU call has a 4s budget (VOICE_SIMULATOR_DESIGN.md §5.2).
 */
export const BUDGETS: Partial<Record<LatencyStage, { warn: number; fail: number }>> = {
  llm_ms: { warn: 2500, fail: 4000 },
  turn_total_ms: { warn: 3000, fail: 6000 },
  stt_ms: { warn: 1500, fail: 3000 },
  tts_ms: { warn: 1500, fail: 3000 },
  queue_wait_ms: { warn: 250, fail: 1000 },
};

export function budgetLevel(stage: LatencyStage, value: number | null | undefined): BudgetLevel {
  const budget = BUDGETS[stage];
  if (value === null || value === undefined || !budget) return "none";
  if (value >= budget.fail) return "fail";
  if (value >= budget.warn) return "warn";
  return "good";
}

/** Nearest-rank percentile, matching backend/app/simulator/metrics.py. */
export function percentile(values: number[], pct: number): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const rank = Math.max(1, Math.ceil((pct / 100) * ordered.length));
  return ordered[rank - 1];
}

export function stageStats(values: Array<number | null | undefined>): StageStats {
  const series = values.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (series.length === 0) return { current: null, avg: null, max: null, p95: null, n: 0 };
  const sum = series.reduce((acc, v) => acc + v, 0);
  return {
    current: series[series.length - 1],
    avg: Math.round((sum / series.length) * 100) / 100,
    max: Math.max(...series),
    p95: percentile(series, 95),
    n: series.length,
  };
}

export function statsByStage(turns: TurnTimings[], stages: LatencyStage[]): Record<LatencyStage, StageStats> {
  const result = {} as Record<LatencyStage, StageStats>;
  for (const stage of stages) {
    result[stage] = stageStats(turns.map((t) => t[stage]));
  }
  return result;
}

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1000) return `${(value / 1000).toFixed(2)}s`;
  return `${Math.round(value)}ms`;
}

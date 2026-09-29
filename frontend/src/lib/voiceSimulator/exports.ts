/**
 * Export formats (VOICE_SIMULATOR_DESIGN.md §8.7). Built from what the
 * server recorded (GET /session, GET /metrics), never from UI state, so an
 * export matches the database. Pure except `downloadFile`.
 */
import type { SessionDetail, SimulatorMetrics, TurnTimings } from "../../types/voiceSimulator";

const TIMING_COLUMNS: Array<keyof TurnTimings> = [
  "turn_index",
  "capture_ms",
  "stt_ms",
  "llm_ms",
  "tts_ms",
  "playback_start_ms",
  "turn_total_ms",
  "queue_wait_ms",
  "ticket_create_ms",
  "server_total_ms",
  "utterance_ms",
  "playback_duration_ms",
];

/** Timestamped Agent:/Caller: lines, the same speaker labels as the ticket transcript. */
export function transcriptText(detail: SessionDetail): string {
  const { session, turns } = detail;
  const lines = [
    `HFMG AI Call Simulator transcript`,
    `Session: ${session.id}${session.label ? ` (${session.label})` : ""}`,
    `Started: ${session.started_at}`,
    `Ended: ${session.ended_at ?? "in progress"}${session.end_reason ? ` (${session.end_reason})` : ""}`,
    `Outcome: ${session.state}${session.ticket ? ` — ticket ${session.ticket.ticket_number}` : ""}`,
    "",
  ];
  for (const turn of turns) {
    if (turn.input_mode !== "system") {
      lines.push(`[${turn.created_at}] Caller (${turn.input_mode}): ${turn.utterance?.trim() || "(silence)"}`);
    }
    if (turn.agent_text) {
      lines.push(`[${turn.created_at}] Agent: ${turn.agent_text}`);
    }
  }
  return `${lines.join("\n")}\n`;
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(header: string[], rows: unknown[][]): string {
  return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\n") + "\n";
}

/** One row per turn, one column per timing field, plus intent and errors for context. */
export function metricsCsv(detail: SessionDetail): string {
  const header = [...TIMING_COLUMNS, "intent", "state_before", "state_after", "input_mode", "errors"];
  const rows = detail.turns.map((turn) => [
    ...TIMING_COLUMNS.map((column) => turn.timings[column]),
    turn.intent,
    turn.state_before,
    turn.state_after,
    turn.input_mode,
    turn.errors.map((e) => `${e.stage}: ${e.message}`).join(" | "),
  ]);
  return toCsv(header, rows);
}

export function sessionJson(detail: SessionDetail, metrics: SimulatorMetrics | null): string {
  return JSON.stringify(
    {
      exported_at: new Date().toISOString(),
      format: "hfmg-voice-simulator-session/v1",
      session: detail.session,
      turns: detail.turns,
      metrics,
    },
    null,
    2,
  );
}

export function exportFilename(sessionId: string, extension: string, now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
  return `hfmg-sim-${sessionId.slice(0, 8)}-${stamp}.${extension}`;
}

export function downloadFile(filename: string, content: string, mimeType: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

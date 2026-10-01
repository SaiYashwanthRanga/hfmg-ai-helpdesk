/**
 * Replay: re-run a recorded session's caller lines against a fresh session
 * and report what changed (design D10). A regression check, not an audio
 * replay — the same caller should reach the same category, priority and
 * ticket outcome. Stops at the first turn where the agent asked something
 * different, since later lines would be answering the wrong question.
 */
import type {
  AgentState,
  CollectedSlots,
  ProcessRequest,
  ProcessResponse,
  SessionDetail,
  SimulatorSession,
  StartRequest,
  StartResponse,
} from "../../types/voiceSimulator";
import { uuid as newUuid } from "../uuid.ts";

export interface ReplayApi {
  start: (body: StartRequest) => Promise<StartResponse>;
  process: (body: ProcessRequest) => Promise<ProcessResponse>;
  end: (sessionId: string, reason: "user_hangup" | "agent_hangup" | "error" | "cleared") => Promise<unknown>;
}

export interface ReplayDiff {
  field: string;
  before: unknown;
  after: unknown;
}

export interface ReplayResult {
  sourceSessionId: string;
  replaySessionId: string;
  turnsReplayed: number;
  turnsInSource: number;
  divergedAt: { turn: number; expected: AgentState | null; actual: AgentState } | null;
  diffs: ReplayDiff[];
  replaySession: SimulatorSession;
}

const COMPARED_SLOTS: Array<keyof CollectedSlots> = [
  "category",
  "priority",
  "caller_name",
  "phone_number",
  "email",
  "category_confidence",
];

export function outcomeDiffs(before: SimulatorSession, after: SimulatorSession): ReplayDiff[] {
  const diffs: ReplayDiff[] = [];
  const push = (field: string, a: unknown, b: unknown) => {
    if (JSON.stringify(a ?? null) !== JSON.stringify(b ?? null)) diffs.push({ field, before: a ?? null, after: b ?? null });
  };
  push("state", before.state, after.state);
  push("escalated", before.escalated, after.escalated);
  push("escalation_reason", before.escalation_reason, after.escalation_reason);
  push("ticket_created", before.ticket !== null, after.ticket !== null);
  for (const slot of COMPARED_SLOTS) push(slot, before.collected[slot], after.collected[slot]);
  return diffs;
}

export async function replaySession(api: ReplayApi, source: SessionDetail, uuid = () => newUuid()): Promise<ReplayResult> {
  const callerTurns = source.turns.filter((t) => t.input_mode !== "system" && t.status === "completed");
  const started = await api.start({
    tts: false,
    caller_id: source.session.caller_id,
    label: `Replay of ${source.session.label ?? source.session.id.slice(0, 8)}`,
  });

  let session = started.session;
  let replayed = 0;
  let divergedAt: ReplayResult["divergedAt"] = null;
  for (const turn of callerTurns) {
    if (turn.state_before && turn.state_before !== session.state) {
      divergedAt = { turn: replayed + 1, expected: turn.state_before, actual: session.state };
      break;
    }
    const response = await api.process({
      session_id: session.id,
      turn_client_id: uuid(),
      utterance: turn.utterance ?? "",
      input_mode: "text",
    });
    session = response.session;
    replayed += 1;
    if (response.reply.call_ended) break;
  }

  if (!session.ended_at) {
    await api.end(session.id, "cleared");
  }

  return {
    sourceSessionId: source.session.id,
    replaySessionId: session.id,
    turnsReplayed: replayed,
    turnsInSource: callerTurns.length,
    divergedAt,
    diffs: outcomeDiffs(source.session, session),
    replaySession: session,
  };
}

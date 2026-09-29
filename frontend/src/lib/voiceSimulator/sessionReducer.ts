/**
 * The simulated call's state machine (VOICE_SIMULATOR_DESIGN.md §8.3).
 *
 * Every transition is explicit; an event that does not apply to the current
 * status returns the same state object. That is what makes a double click,
 * a late network response after hang-up, or a key held down harmless.
 * Pure — unit-tested by frontend/tests/voiceSimulator.test.ts.
 */
import type {
  InputMode,
  ProcessResponse,
  SimulatorSession,
  SimulatorStatus,
  SimulatorTurn,
  StartResponse,
  TimelineEntry,
} from "../../types/voiceSimulator";

export interface PendingTurn {
  turnClientId: string;
  /** null = use the server-side transcript from /audio. */
  utterance: string | null;
  inputMode: Exclude<InputMode, "system">;
}

export interface SimulatorState {
  status: SimulatorStatus;
  session: SimulatorSession | null;
  turns: SimulatorTurn[];
  timeline: TimelineEntry[];
  pending: PendingTurn | null;
  /** The turn that failed, kept so Retry reuses its id (the server dedupes on it). */
  failed: PendingTurn | null;
  error: string | null;
  /** Bumped whenever server-side timings may have changed, to refetch metrics. */
  metricsVersion: number;
}

export type SimulatorEvent =
  | { type: "START_REQUESTED" }
  | { type: "START_SUCCEEDED"; response: StartResponse; willPlay: boolean; at: string }
  | { type: "START_FAILED"; message: string; at: string }
  | { type: "TURN_SUBMITTED"; turn: PendingTurn; at: string }
  | { type: "TRANSCRIBED"; turnClientId: string; text: string }
  | { type: "TURN_SUCCEEDED"; response: ProcessResponse; willPlay: boolean; at: string }
  | { type: "TURN_FAILED"; turnClientId: string; message: string; at: string }
  | { type: "PLAYBACK_ENDED" }
  | { type: "ERROR_DISMISSED" }
  | { type: "ENDED"; session: SimulatorSession | null; at: string; note?: string }
  | { type: "SESSION_REFRESHED"; session: SimulatorSession }
  | { type: "METRICS_CHANGED" }
  | { type: "NOTE"; text: string; tone: "info" | "warning" | "error"; at: string }
  | { type: "CLEARED" };

export const initialSimulatorState: SimulatorState = {
  status: "idle",
  session: null,
  turns: [],
  timeline: [],
  pending: null,
  failed: null,
  error: null,
  metricsVersion: 0,
};

function note(text: string, tone: "info" | "warning" | "error", at: string): TimelineEntry {
  return { kind: "system", key: `note-${at}-${tone}-${text}`, text, at, tone };
}

function upsertTurn(turns: SimulatorTurn[], turn: SimulatorTurn): SimulatorTurn[] {
  const index = turns.findIndex((t) => t.turn_client_id === turn.turn_client_id);
  if (index === -1) return [...turns, turn];
  const next = [...turns];
  next[index] = turn;
  return next;
}

function callerKey(turnClientId: string) {
  return `caller-${turnClientId}`;
}

export function simulatorReducer(state: SimulatorState, event: SimulatorEvent): SimulatorState {
  switch (event.type) {
    case "START_REQUESTED":
      if (state.status !== "idle" && state.status !== "disconnected" && !(state.status === "error" && !state.session)) {
        return state;
      }
      return { ...initialSimulatorState, status: "connecting", metricsVersion: state.metricsVersion + 1 };

    case "START_SUCCEEDED": {
      if (state.status !== "connecting") return state;
      const { session, greeting, turn } = event.response;
      return {
        ...state,
        status: event.willPlay ? "speaking" : "listening",
        session,
        turns: [turn],
        timeline: [{ kind: "agent", key: `agent-${turn.turn_client_id}`, turnClientId: turn.turn_client_id, text: greeting.text, at: event.at }],
        error: null,
        metricsVersion: state.metricsVersion + 1,
      };
    }

    case "START_FAILED":
      if (state.status !== "connecting") return state;
      return { ...state, status: "error", error: event.message, timeline: [note(event.message, "error", event.at)] };

    case "TURN_SUBMITTED": {
      const retrying = state.status === "error" && state.failed?.turnClientId === event.turn.turnClientId;
      if (!state.session || (state.status !== "listening" && !retrying)) return state;
      const existing = state.timeline.some((e) => e.key === callerKey(event.turn.turnClientId));
      const bubble: TimelineEntry = {
        kind: "caller",
        key: callerKey(event.turn.turnClientId),
        turnClientId: event.turn.turnClientId,
        text: event.turn.utterance ?? "…",
        at: event.at,
        pending: true,
        inputMode: event.turn.inputMode,
      };
      return {
        ...state,
        status: "thinking",
        pending: event.turn,
        failed: null,
        error: null,
        timeline: existing
          ? state.timeline.map((e) => (e.key === bubble.key ? { ...bubble, text: e.kind === "caller" ? e.text : bubble.text } : e))
          : [...state.timeline, bubble],
      };
    }

    case "TRANSCRIBED":
      if (state.status !== "thinking" || state.pending?.turnClientId !== event.turnClientId) return state;
      return {
        ...state,
        timeline: state.timeline.map((e) =>
          e.key === callerKey(event.turnClientId) && e.kind === "caller"
            ? { ...e, text: event.text.trim() ? event.text : "(no speech detected)" }
            : e,
        ),
      };

    case "TURN_SUCCEEDED": {
      const { turn, reply, session } = event.response;
      if (state.status !== "thinking" || state.pending?.turnClientId !== turn.turn_client_id) return state;
      const timeline = state.timeline.map((e) =>
        e.key === callerKey(turn.turn_client_id) && e.kind === "caller"
          ? { ...e, text: turn.utterance?.trim() ? turn.utterance : "(silence)", pending: false }
          : e,
      );
      timeline.push({ kind: "agent", key: `agent-${turn.turn_client_id}`, turnClientId: turn.turn_client_id, text: reply.text, at: event.at });
      for (const err of turn.errors) {
        timeline.push(note(`${err.stage.toUpperCase()} error: ${err.message}`, "warning", `${event.at}-${err.stage}`));
      }
      return {
        ...state,
        status: event.willPlay ? "speaking" : reply.call_ended ? "disconnected" : "listening",
        session,
        turns: upsertTurn(state.turns, turn),
        timeline,
        pending: null,
        metricsVersion: state.metricsVersion + 1,
      };
    }

    case "TURN_FAILED":
      if (state.status !== "thinking" || state.pending?.turnClientId !== event.turnClientId) return state;
      return {
        ...state,
        status: "error",
        failed: state.pending,
        pending: null,
        error: event.message,
        timeline: [...state.timeline, note(`Turn failed: ${event.message}`, "error", event.at)],
      };

    case "PLAYBACK_ENDED":
      if (state.status !== "speaking") return state;
      return { ...state, status: state.session?.ended_at ? "disconnected" : "listening" };

    case "ERROR_DISMISSED":
      if (state.status !== "error") return state;
      return {
        ...state,
        status: state.session && !state.session.ended_at ? "listening" : state.session ? "disconnected" : "idle",
        error: null,
        failed: null,
      };

    case "ENDED":
      if (!state.session || state.status === "idle") return state;
      return {
        ...state,
        status: "disconnected",
        session: event.session ?? state.session,
        pending: null,
        timeline: event.note ? [...state.timeline, note(event.note, "info", event.at)] : state.timeline,
        metricsVersion: state.metricsVersion + 1,
      };

    case "SESSION_REFRESHED":
      if (!state.session || state.session.id !== event.session.id) return state;
      return { ...state, session: event.session };

    case "METRICS_CHANGED":
      return { ...state, metricsVersion: state.metricsVersion + 1 };

    case "NOTE":
      return { ...state, timeline: [...state.timeline, note(event.text, event.tone, event.at)] };

    case "CLEARED":
      return { ...initialSimulatorState, metricsVersion: state.metricsVersion + 1 };
  }
}

/** True while a call is connected (anything that should block Start). */
export function isCallActive(state: SimulatorState): boolean {
  return state.session !== null && !state.session.ended_at && state.status !== "disconnected";
}

/**
 * Unit tests for the AI Call Simulator's pure logic: the call state
 * machine, latency statistics, exports, replay and the load-test runner.
 * Run with `npm test` (Node's built-in runner; Node 23+ strips types).
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { budgetLevel, formatMs, percentile, stageStats } from "../src/lib/voiceSimulator/latency.ts";
import { initialSimulatorState, isCallActive, simulatorReducer } from "../src/lib/voiceSimulator/sessionReducer.ts";
import type { SimulatorEvent, SimulatorState } from "../src/lib/voiceSimulator/sessionReducer.ts";
import { exportFilename, metricsCsv, toCsv, transcriptText } from "../src/lib/voiceSimulator/exports.ts";
import { outcomeDiffs, replaySession } from "../src/lib/voiceSimulator/replay.ts";
import { runLoadTest, summarize } from "../src/lib/voiceSimulator/loadTest.ts";
import type { LoadTestApi } from "../src/lib/voiceSimulator/loadTest.ts";
import type {
  AgentState,
  MockCaller,
  ProcessResponse,
  SessionDetail,
  SimulatorSession,
  SimulatorTurn,
  StartResponse,
  TurnTimings,
} from "../src/types/voiceSimulator.ts";

// --- fixtures ---------------------------------------------------------------

const AT = "2026-09-28T10:00:00.000Z";

function timings(overrides: Partial<TurnTimings> = {}): TurnTimings {
  return {
    turn_index: 0,
    stt_ms: null,
    llm_ms: null,
    tts_ms: null,
    ticket_create_ms: null,
    queue_wait_ms: null,
    server_total_ms: null,
    utterance_ms: null,
    capture_ms: null,
    playback_start_ms: null,
    playback_duration_ms: null,
    turn_total_ms: null,
    ...overrides,
  };
}

function session(overrides: Partial<SimulatorSession> = {}): SimulatorSession {
  return {
    id: "s1",
    label: null,
    state: "COLLECT_DESCRIPTION",
    collected: {
      description: null,
      short_issue: null,
      caller_name: null,
      phone_number: null,
      email: null,
      category: null,
      category_confidence: null,
      priority: null,
      impact: null,
    },
    misunderstanding_count: 0,
    escalated: false,
    escalation_reason: null,
    ticket: null,
    started_at: AT,
    ended_at: null,
    end_reason: null,
    tts_enabled: false,
    send_notifications: false,
    caller_id: null,
    ...overrides,
  };
}

function turn(id: string, overrides: Partial<SimulatorTurn> = {}): SimulatorTurn {
  return {
    id: `row-${id}`,
    turn_client_id: id,
    turn_index: 1,
    status: "completed",
    input_mode: "text",
    utterance: "my printer is jammed",
    stt_confidence: null,
    stt_raw: null,
    state_before: "COLLECT_DESCRIPTION",
    state_after: "COLLECT_NAME",
    intent: "report_issue",
    agent_text: "May I have your name?",
    call_ended: false,
    collected_after: null,
    ticket_payload: null,
    llm_trace: [],
    errors: [],
    timings: timings({ turn_index: 1, llm_ms: 900 }),
    created_at: AT,
    ...overrides,
  };
}

function started(): StartResponse {
  return {
    session: session(),
    greeting: { text: "Thank you for calling.", call_ended: false, audio: null },
    turn: turn("greet", { turn_index: 0, input_mode: "system", utterance: null, intent: "greeting" }),
  };
}

function run(events: SimulatorEvent[], from: SimulatorState = initialSimulatorState): SimulatorState {
  return events.reduce(simulatorReducer, from);
}

function listening(): SimulatorState {
  return run([{ type: "START_REQUESTED" }, { type: "START_SUCCEEDED", response: started(), willPlay: false, at: AT }]);
}

function processed(id: string, overrides: { ended?: boolean; state?: AgentState } = {}): ProcessResponse {
  return {
    turn: turn(id, { call_ended: overrides.ended ?? false }),
    reply: { text: "May I have your name?", call_ended: overrides.ended ?? false, audio: null },
    session: session({ state: overrides.state ?? "COLLECT_NAME", ended_at: overrides.ended ? AT : null }),
  };
}

// --- state machine ------------------------------------------------------------

describe("simulatorReducer", () => {
  it("starts, greets, and listens", () => {
    const state = listening();
    assert.equal(state.status, "listening");
    assert.equal(state.session?.id, "s1");
    assert.equal(state.timeline.length, 1);
    assert.equal(state.timeline[0].kind, "agent");
  });

  it("goes to speaking when the greeting has audio, then listening after playback", () => {
    let state = run([{ type: "START_REQUESTED" }, { type: "START_SUCCEEDED", response: started(), willPlay: true, at: AT }]);
    assert.equal(state.status, "speaking");
    state = simulatorReducer(state, { type: "PLAYBACK_ENDED" });
    assert.equal(state.status, "listening");
  });

  it("ignores a second Start while connecting (double click)", () => {
    const connecting = run([{ type: "START_REQUESTED" }]);
    assert.equal(simulatorReducer(connecting, { type: "START_REQUESTED" }), connecting);
  });

  it("runs a full turn: submitted → thinking → listening", () => {
    let state = listening();
    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { turnClientId: "t1", utterance: "my printer", inputMode: "text" }, at: AT });
    assert.equal(state.status, "thinking");
    const bubble = state.timeline.find((e) => e.kind === "caller");
    assert.ok(bubble && bubble.kind === "caller" && bubble.pending);

    state = simulatorReducer(state, { type: "TURN_SUCCEEDED", response: processed("t1"), willPlay: false, at: AT });
    assert.equal(state.status, "listening");
    assert.equal(state.session?.state, "COLLECT_NAME");
    assert.equal(state.turns.length, 2);
    const done = state.timeline.find((e) => e.kind === "caller");
    assert.ok(done && done.kind === "caller" && !done.pending);
  });

  it("refuses a submission while the agent is thinking", () => {
    let state = listening();
    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { turnClientId: "t1", utterance: "a", inputMode: "text" }, at: AT });
    const again = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { turnClientId: "t2", utterance: "b", inputMode: "text" }, at: AT });
    assert.equal(again, state);
  });

  it("ignores a response for a turn that is not pending (late or duplicate)", () => {
    let state = listening();
    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { turnClientId: "t1", utterance: "a", inputMode: "text" }, at: AT });
    assert.equal(simulatorReducer(state, { type: "TURN_SUCCEEDED", response: processed("other"), willPlay: false, at: AT }), state);
  });

  it("updates the caller bubble when the transcript arrives", () => {
    let state = listening();
    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { turnClientId: "v1", utterance: null, inputMode: "voice" }, at: AT });
    state = simulatorReducer(state, { type: "TRANSCRIBED", turnClientId: "v1", text: "outlook is broken" });
    const bubble = state.timeline.find((e) => e.kind === "caller");
    assert.ok(bubble && bubble.kind === "caller" && bubble.text === "outlook is broken");
  });

  it("keeps the failed turn so Retry reuses its id", () => {
    let state = listening();
    const pending = { turnClientId: "t1", utterance: "a", inputMode: "text" as const };
    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: pending, at: AT });
    state = simulatorReducer(state, { type: "TURN_FAILED", turnClientId: "t1", message: "network", at: AT });
    assert.equal(state.status, "error");
    assert.deepEqual(state.failed, pending);

    // A different turn cannot sneak in while in error.
    const other = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { ...pending, turnClientId: "t2" }, at: AT });
    assert.equal(other, state);

    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: pending, at: AT });
    assert.equal(state.status, "thinking");
    assert.equal(state.timeline.filter((e) => e.kind === "caller").length, 1, "retry must not duplicate the bubble");
  });

  it("dismissing an error returns to listening", () => {
    let state = listening();
    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { turnClientId: "t1", utterance: "a", inputMode: "text" }, at: AT });
    state = simulatorReducer(state, { type: "TURN_FAILED", turnClientId: "t1", message: "x", at: AT });
    assert.equal(simulatorReducer(state, { type: "ERROR_DISMISSED" }).status, "listening");
  });

  it("disconnects when the agent hangs up", () => {
    let state = listening();
    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { turnClientId: "t1", utterance: "no thanks", inputMode: "text" }, at: AT });
    state = simulatorReducer(state, { type: "TURN_SUCCEEDED", response: processed("t1", { ended: true, state: "COMPLETED" }), willPlay: false, at: AT });
    assert.equal(state.status, "disconnected");
    assert.equal(isCallActive(state), false);
  });

  it("hangs up after the goodbye finishes playing", () => {
    let state = listening();
    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { turnClientId: "t1", utterance: "no", inputMode: "text" }, at: AT });
    state = simulatorReducer(state, { type: "TURN_SUCCEEDED", response: processed("t1", { ended: true, state: "COMPLETED" }), willPlay: true, at: AT });
    assert.equal(state.status, "speaking");
    assert.equal(simulatorReducer(state, { type: "PLAYBACK_ENDED" }).status, "disconnected");
  });

  it("records turn errors as timeline warnings", () => {
    let state = listening();
    state = simulatorReducer(state, { type: "TURN_SUBMITTED", turn: { turnClientId: "t1", utterance: "a", inputMode: "text" }, at: AT });
    const response = processed("t1");
    response.turn.errors = [{ stage: "llm", type: "record_issue", message: "timeout" }];
    state = simulatorReducer(state, { type: "TURN_SUCCEEDED", response, willPlay: false, at: AT });
    assert.ok(state.timeline.some((e) => e.kind === "system" && e.tone === "warning" && e.text.includes("timeout")));
  });

  it("clears back to idle and can start again", () => {
    const cleared = simulatorReducer(listening(), { type: "CLEARED" });
    assert.equal(cleared.status, "idle");
    assert.equal(cleared.session, null);
    assert.equal(simulatorReducer(cleared, { type: "START_REQUESTED" }).status, "connecting");
  });

  it("ignores session refreshes for a different session", () => {
    const state = listening();
    assert.equal(simulatorReducer(state, { type: "SESSION_REFRESHED", session: session({ id: "other" }) }), state);
  });
});

// --- latency --------------------------------------------------------------------

describe("latency", () => {
  it("computes nearest-rank percentiles like the backend", () => {
    assert.equal(percentile([], 95), null);
    assert.equal(percentile([5], 95), 5);
    assert.equal(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95), 10);
    assert.equal(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50), 5);
  });

  it("ignores nulls and reports the latest value as current", () => {
    assert.deepEqual(stageStats([100, null, 300, undefined]), { current: 300, avg: 200, max: 300, p95: 300, n: 2 });
    assert.deepEqual(stageStats([]), { current: null, avg: null, max: null, p95: null, n: 0 });
  });

  it("classifies against the phone budgets", () => {
    assert.equal(budgetLevel("llm_ms", 1000), "good");
    assert.equal(budgetLevel("llm_ms", 3000), "warn");
    assert.equal(budgetLevel("llm_ms", 4000), "fail");
    assert.equal(budgetLevel("capture_ms", 99999), "none");
    assert.equal(budgetLevel("llm_ms", null), "none");
  });

  it("formats milliseconds", () => {
    assert.equal(formatMs(null), "—");
    assert.equal(formatMs(412.4), "412ms");
    assert.equal(formatMs(2345), "2.35s");
  });
});

// --- exports -----------------------------------------------------------------------

describe("exports", () => {
  const detail: SessionDetail = {
    session: session({ label: "QA", state: "COMPLETED" }),
    turns: [
      turn("g", { turn_index: 0, input_mode: "system", utterance: null, agent_text: "Hello", timings: timings() }),
      turn("t1", { utterance: 'He said "hi", then left', errors: [{ stage: "tts", type: "SpeechError", message: "down" }] }),
    ],
  };

  it("writes a transcript with both speakers and no caller line for the greeting", () => {
    const text = transcriptText(detail);
    assert.match(text, /Session: s1 \(QA\)/);
    assert.match(text, /Agent: Hello/);
    assert.equal((text.match(/Caller \(/g) ?? []).length, 1);
  });

  it("escapes CSV cells", () => {
    assert.equal(toCsv(["a"], [['x,"y"']]), 'a\n"x,""y"""\n');
    const csv = metricsCsv(detail);
    assert.match(csv.split("\n")[0], /^turn_index,capture_ms,stt_ms,llm_ms/);
    assert.match(csv, /tts: down/);
  });

  it("names files by session and time", () => {
    assert.equal(exportFilename("abcdef123456", "csv", new Date(2026, 8, 28, 9, 5)), "hfmg-sim-abcdef12-20260928-0905.csv");
  });
});

// --- replay -------------------------------------------------------------------------

describe("replay", () => {
  it("diffs outcomes field by field", () => {
    const a = session({ state: "COMPLETED", collected: { ...session().collected, category: "Printer" } });
    const b = session({ state: "COMPLETED", collected: { ...session().collected, category: "Other" } });
    assert.deepEqual(outcomeDiffs(a, b), [{ field: "category", before: "Printer", after: "Other" }]);
    assert.deepEqual(outcomeDiffs(a, a), []);
  });

  it("stops at the first turn where the agent asked something different", async () => {
    const source: SessionDetail = {
      session: session({ state: "COMPLETED" }),
      turns: [
        turn("g", { turn_index: 0, input_mode: "system" }),
        turn("t1", { state_before: "COLLECT_DESCRIPTION", utterance: "printer jammed" }),
        turn("t2", { state_before: "COLLECT_NAME", utterance: "Maria" }),
      ],
    };
    const sent: string[] = [];
    let ended = "";
    const api = {
      start: async () => started(),
      process: async (body: { utterance?: string | null }) => {
        sent.push(body.utterance ?? "");
        // The replayed agent re-asks for the description instead of moving on.
        return { ...processed("x"), session: session({ state: "COLLECT_DESCRIPTION" }) };
      },
      end: async (_id: string, reason: string) => {
        ended = reason;
        return null;
      },
    };
    let n = 0;
    const result = await replaySession(api, source, () => `u${n++}`);
    assert.deepEqual(sent, ["printer jammed"]);
    assert.deepEqual(result.divergedAt, { turn: 2, expected: "COLLECT_NAME", actual: "COLLECT_DESCRIPTION" });
    assert.equal(ended, "cleared");
    assert.ok(result.diffs.some((d) => d.field === "state"));
  });
});

// --- load test --------------------------------------------------------------------

describe("load test", () => {
  function fakeApi(options: { failStartFor?: number; slowMs?: number } = {}): LoadTestApi & { active: number; peak: number } {
    const flow: AgentState[] = ["COLLECT_DESCRIPTION", "COLLECT_NAME", "COLLECT_EMAIL", "CONFIRM_EMAIL", "ANYTHING_ELSE", "COMPLETED"];
    const stateBySession = new Map<string, number>();
    let sessions = 0;
    const api = {
      active: 0,
      peak: 0,
      mockCaller: async (seed: number): Promise<MockCaller> => ({
        seed,
        persona: "p",
        caller_name: "Maria Lopez",
        phone_number: "+18455550100",
        email_spoken: "m at hfmg dot net",
        issue: "printer",
        answers: { COLLECT_DESCRIPTION: "printer", COLLECT_NAME: "Maria", COLLECT_EMAIL: "m", CONFIRM_EMAIL: "yes", ANYTHING_ELSE: "no" },
        expect: { category: "Printer", priority: "MEDIUM", escalated: false, ticket_created: true, note: null },
      }),
      start: async (): Promise<StartResponse> => {
        sessions += 1;
        if (options.failStartFor === sessions) throw Object.assign(new Error("limit reached"), { status: 409 });
        const id = `s${sessions}`;
        stateBySession.set(id, 0);
        return { ...started(), session: session({ id }) };
      },
      process: async (body: { session_id: string }): Promise<ProcessResponse> => {
        api.active += 1;
        api.peak = Math.max(api.peak, api.active);
        await new Promise((r) => setTimeout(r, options.slowMs ?? 1));
        api.active -= 1;
        const step = (stateBySession.get(body.session_id) ?? 0) + 1;
        stateBySession.set(body.session_id, step);
        const state = flow[step];
        const ended = state === "COMPLETED";
        const ticketNow = state === "ANYTHING_ELSE";
        const hasTicket = step >= flow.indexOf("ANYTHING_ELSE");
        return {
          turn: turn(`${body.session_id}-${step}`, {
            timings: timings({ turn_index: step, llm_ms: 100, queue_wait_ms: 5, server_total_ms: 120, ticket_create_ms: ticketNow ? 30 : null }),
          }),
          reply: { text: "ok", call_ended: ended, audio: null },
          session: session({
            id: body.session_id,
            state,
            ended_at: ended ? AT : null,
            collected: { ...session().collected, category: "Printer", priority: "MEDIUM" },
            ticket: hasTicket
              ? { id: "t", ticket_number: "SIM-2026-ABCDEF12", status: "NEW", category: "Printer", priority: "MEDIUM", ai_summary_status: "DISABLED", ai_summary: null }
              : null,
          }),
        };
      },
      end: async () => null,
    };
    return api;
  }

  it("runs every caller to completion and checks expectations", async () => {
    const api = fakeApi();
    let clock = 0;
    const { results, summary } = await runLoadTest(api, { callers: 5, seed: 1, now: () => (clock += 10) });
    assert.equal(results.length, 5);
    assert.equal(summary.passed, 5);
    assert.equal(summary.turns, 25);
    assert.equal(summary.errorRate, 0);
    assert.equal(summary.ticketCreation.n, 5);
    assert.equal(summary.ticketCreation.avg, 30);
    assert.equal(summary.queueWait.max, 5);
    assert.deepEqual(results.map((r) => r.seed), [1, 2, 3, 4, 5]);
  });

  it("respects the concurrency limit", async () => {
    const api = fakeApi({ slowMs: 5 });
    await runLoadTest(api, { callers: 10, concurrency: 3, seed: 0 });
    assert.ok(api.peak <= 3, `peak ${api.peak} exceeded concurrency 3`);
  });

  it("counts a refused session as a failed request", async () => {
    const api = fakeApi({ failStartFor: 2 });
    const { results, summary } = await runLoadTest(api, { callers: 3, seed: 0, concurrency: 1 });
    assert.equal(summary.failedRequests, 1);
    assert.equal(summary.passed, 2);
    assert.match(results[1].failures[0], /409 limit reached/);
    assert.ok(summary.errorRate > 0);
  });

  it("stops starting new callers once aborted", async () => {
    const controller = new AbortController();
    const api = fakeApi({ slowMs: 2 });
    const pending = runLoadTest(api, { callers: 25, concurrency: 1, seed: 0, signal: controller.signal }, (p) => {
      if (p.finished === 2) controller.abort();
    });
    const { results } = await pending;
    assert.ok(results.length < 25);
  });

  it("summarizes an empty run without dividing by zero", () => {
    const summary = summarize([], 0);
    assert.equal(summary.errorRate, 0);
    assert.equal(summary.throughputTurnsPerSecond, 0);
  });
});

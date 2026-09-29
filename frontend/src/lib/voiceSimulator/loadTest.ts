/**
 * Load test runner: N scripted callers through the real simulator API.
 *
 * Runs text-only with TTS off, so it measures the agent and the platform
 * (database, session locking, connection pool, model latency) rather than
 * paying for audio. Each caller answers whatever the agent is currently
 * asking, using the mock caller's per-state answers, so it stays in step
 * however the conversation branches. The API is injected so the runner is
 * unit-testable without a server.
 */
import type {
  AgentState,
  MockCaller,
  ProcessRequest,
  ProcessResponse,
  StartRequest,
  StartResponse,
} from "../../types/voiceSimulator";
import { stageStats } from "./latency.ts";

export const LOAD_TEST_PRESETS = [1, 5, 10, 25] as const;

/** Guard against a script that never reaches a terminal state. */
const MAX_TURNS_PER_CALLER = 16;

export interface LoadTestApi {
  mockCaller: (seed: number) => Promise<MockCaller>;
  start: (body: StartRequest) => Promise<StartResponse>;
  process: (body: ProcessRequest) => Promise<ProcessResponse>;
  end: (sessionId: string, reason: "user_hangup" | "agent_hangup" | "error" | "cleared") => Promise<unknown>;
}

export interface LoadTestOptions {
  callers: number;
  /** Callers in flight at once; defaults to all of them. */
  concurrency?: number;
  seed: number;
  signal?: AbortSignal;
  now?: () => number;
  uuid?: () => string;
}

export interface CallerResult {
  index: number;
  seed: number;
  sessionId: string | null;
  persona: string | null;
  turns: number;
  finalState: AgentState | null;
  ticketNumber: string | null;
  category: string | null;
  priority: string | null;
  expectedCategory: string | null;
  expectedPriority: string | null;
  passed: boolean;
  failures: string[];
  /** Browser-observed request time per turn. */
  roundTripMs: number[];
  llmMs: number[];
  queueWaitMs: number[];
  serverMs: number[];
  ticketCreateMs: number[];
  requestErrors: number;
  turnErrors: number;
  durationMs: number;
}

export interface LoadTestSummary {
  callers: number;
  completed: number;
  passed: number;
  turns: number;
  requests: number;
  failedRequests: number;
  turnsWithErrors: number;
  /** (failed requests + turns reporting an error) / requests. */
  errorRate: number;
  roundTrip: ReturnType<typeof stageStats>;
  llm: ReturnType<typeof stageStats>;
  queueWait: ReturnType<typeof stageStats>;
  server: ReturnType<typeof stageStats>;
  ticketCreation: ReturnType<typeof stageStats>;
  wallClockMs: number;
  throughputTurnsPerSecond: number;
}

export interface LoadTestProgress {
  started: number;
  finished: number;
  turns: number;
  results: CallerResult[];
}

async function runCaller(
  api: LoadTestApi,
  index: number,
  seed: number,
  options: Required<Pick<LoadTestOptions, "now" | "uuid">> & { signal?: AbortSignal },
): Promise<CallerResult> {
  const { now, uuid, signal } = options;
  const started = now();
  const result: CallerResult = {
    index,
    seed,
    sessionId: null,
    persona: null,
    turns: 0,
    finalState: null,
    ticketNumber: null,
    category: null,
    priority: null,
    expectedCategory: null,
    expectedPriority: null,
    passed: false,
    failures: [],
    roundTripMs: [],
    llmMs: [],
    queueWaitMs: [],
    serverMs: [],
    ticketCreateMs: [],
    requestErrors: 0,
    turnErrors: 0,
    durationMs: 0,
  };

  let caller: MockCaller;
  try {
    caller = await api.mockCaller(seed);
  } catch (err) {
    result.requestErrors += 1;
    result.failures.push(`mock caller: ${describe(err)}`);
    result.durationMs = now() - started;
    return result;
  }
  result.persona = caller.persona;
  result.expectedCategory = caller.expect.category;
  result.expectedPriority = caller.expect.priority;

  let state: AgentState;
  let sessionId: string;
  try {
    const response = await api.start({ tts: false, label: `Load test #${index + 1} (seed ${seed})` });
    sessionId = response.session.id;
    state = response.session.state;
  } catch (err) {
    result.requestErrors += 1;
    result.failures.push(`start: ${describe(err)}`);
    result.durationMs = now() - started;
    return result;
  }
  result.sessionId = sessionId;

  let ended = false;
  let last: ProcessResponse | null = null;
  while (!ended && result.turns < MAX_TURNS_PER_CALLER && !signal?.aborted) {
    const utterance = caller.answers[state] ?? "";
    const requestStarted = now();
    try {
      last = await api.process({ session_id: sessionId, turn_client_id: uuid(), utterance, input_mode: "mock" });
    } catch (err) {
      result.requestErrors += 1;
      result.failures.push(`turn ${result.turns + 1}: ${describe(err)}`);
      break;
    }
    result.turns += 1;
    result.roundTripMs.push(now() - requestStarted);
    const timings = last.turn.timings;
    if (timings.llm_ms !== null) result.llmMs.push(timings.llm_ms);
    if (timings.queue_wait_ms !== null) result.queueWaitMs.push(timings.queue_wait_ms);
    if (timings.server_total_ms !== null) result.serverMs.push(timings.server_total_ms);
    if (timings.ticket_create_ms !== null) result.ticketCreateMs.push(timings.ticket_create_ms);
    if (last.turn.errors.length > 0) result.turnErrors += 1;
    state = last.session.state;
    ended = last.reply.call_ended;
  }

  if (!ended) {
    try {
      await api.end(sessionId, signal?.aborted ? "cleared" : "user_hangup");
    } catch {
      result.requestErrors += 1;
    }
  }

  const session = last?.session;
  result.finalState = session?.state ?? state;
  result.ticketNumber = session?.ticket?.ticket_number ?? null;
  result.category = session?.ticket?.category ?? session?.collected.category ?? null;
  result.priority = session?.ticket?.priority ?? session?.collected.priority ?? null;

  if (signal?.aborted) result.failures.push("cancelled");
  if (!ended && !signal?.aborted) result.failures.push(`call did not end within ${MAX_TURNS_PER_CALLER} turns`);
  if (caller.expect.ticket_created && !result.ticketNumber) result.failures.push("no ticket created");
  if (caller.expect.escalated !== (session?.escalated ?? false)) {
    result.failures.push(caller.expect.escalated ? "expected escalation" : `unexpected escalation (${session?.escalation_reason})`);
  }
  if (caller.expect.category && result.category !== caller.expect.category) {
    result.failures.push(`category ${result.category ?? "none"}, expected ${caller.expect.category}`);
  }
  if (caller.expect.priority && result.priority !== caller.expect.priority) {
    result.failures.push(`priority ${result.priority ?? "none"}, expected ${caller.expect.priority}`);
  }
  result.passed = result.failures.length === 0;
  result.durationMs = now() - started;
  return result;
}

function describe(err: unknown): string {
  if (err instanceof Error) {
    const status = (err as { status?: number }).status;
    return status ? `${status} ${err.message}` : err.message;
  }
  return String(err);
}

export async function runLoadTest(
  api: LoadTestApi,
  options: LoadTestOptions,
  onProgress?: (progress: LoadTestProgress) => void,
): Promise<{ results: CallerResult[]; summary: LoadTestSummary }> {
  const now = options.now ?? (() => performance.now());
  const uuid = options.uuid ?? (() => crypto.randomUUID());
  const concurrency = Math.max(1, Math.min(options.concurrency ?? options.callers, options.callers));
  const results: CallerResult[] = new Array(options.callers);
  const progress: LoadTestProgress = { started: 0, finished: 0, turns: 0, results: [] };
  const wallStarted = now();

  let next = 0;
  async function worker() {
    while (next < options.callers && !options.signal?.aborted) {
      const index = next++;
      progress.started += 1;
      onProgress?.({ ...progress });
      const result = await runCaller(api, index, options.seed + index, { now, uuid, signal: options.signal });
      results[index] = result;
      progress.finished += 1;
      progress.turns += result.turns;
      progress.results = results.filter(Boolean);
      onProgress?.({ ...progress });
    }
  }
  await Promise.all(Array.from({ length: concurrency }, () => worker()));

  const done = results.filter(Boolean);
  return { results: done, summary: summarize(done, now() - wallStarted) };
}

export function summarize(results: CallerResult[], wallClockMs: number): LoadTestSummary {
  const turns = results.reduce((acc, r) => acc + r.turns, 0);
  const failedRequests = results.reduce((acc, r) => acc + r.requestErrors, 0);
  const turnsWithErrors = results.reduce((acc, r) => acc + r.turnErrors, 0);
  const requests = turns + failedRequests;
  return {
    callers: results.length,
    completed: results.filter((r) => r.finalState === "COMPLETED" || r.finalState === "ESCALATED").length,
    passed: results.filter((r) => r.passed).length,
    turns,
    requests,
    failedRequests,
    turnsWithErrors,
    errorRate: requests ? (failedRequests + turnsWithErrors) / requests : 0,
    roundTrip: stageStats(results.flatMap((r) => r.roundTripMs)),
    llm: stageStats(results.flatMap((r) => r.llmMs)),
    queueWait: stageStats(results.flatMap((r) => r.queueWaitMs)),
    server: stageStats(results.flatMap((r) => r.serverMs)),
    ticketCreation: stageStats(results.flatMap((r) => r.ticketCreateMs)),
    wallClockMs,
    throughputTurnsPerSecond: wallClockMs > 0 ? turns / (wallClockMs / 1000) : 0,
  };
}

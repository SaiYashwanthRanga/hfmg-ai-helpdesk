import { Download, Play, Square } from "lucide-react";
import { useRef, useState } from "react";
import { simulatorApi } from "../../api/voiceSimulator";
import { cn } from "../../lib/cn";
import { formatMs } from "../../lib/voiceSimulator/latency";
import { downloadFile, toCsv } from "../../lib/voiceSimulator/exports";
import { LOAD_TEST_PRESETS, runLoadTest } from "../../lib/voiceSimulator/loadTest";
import type { CallerResult, LoadTestProgress, LoadTestSummary } from "../../lib/voiceSimulator/loadTest";
import { Button } from "../ui/Button";
import { Drawer } from "../ui/Drawer";

export interface LoadTestDrawerProps {
  open: boolean;
  onClose: () => void;
  maxConcurrent: number;
  llmConfigured: boolean;
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "good" | "bad" }) {
  return (
    <div className="rounded-lg border border-stone-200/80 bg-white p-3">
      <dt className="text-[11px] font-medium text-stone-500">{label}</dt>
      <dd className={cn("text-lg font-semibold tabular-nums", tone === "bad" ? "text-rose-700" : tone === "good" ? "text-emerald-700" : "text-stone-900")}>
        {value}
      </dd>
    </div>
  );
}

function resultsCsv(results: CallerResult[]) {
  return toCsv(
    ["caller", "seed", "session_id", "persona", "turns", "final_state", "ticket", "category", "expected_category", "priority", "expected_priority", "passed", "failures", "avg_round_trip_ms", "max_round_trip_ms", "avg_llm_ms", "max_queue_wait_ms", "ticket_create_ms", "request_errors", "turn_errors", "duration_ms"],
    results.map((r) => [
      r.index + 1,
      r.seed,
      r.sessionId,
      r.persona,
      r.turns,
      r.finalState,
      r.ticketNumber,
      r.category,
      r.expectedCategory,
      r.priority,
      r.expectedPriority,
      r.passed,
      r.failures.join(" | "),
      r.roundTripMs.length ? Math.round(r.roundTripMs.reduce((a, b) => a + b, 0) / r.roundTripMs.length) : "",
      r.roundTripMs.length ? Math.round(Math.max(...r.roundTripMs)) : "",
      r.llmMs.length ? Math.round(r.llmMs.reduce((a, b) => a + b, 0) / r.llmMs.length) : "",
      r.queueWaitMs.length ? Math.round(Math.max(...r.queueWaitMs)) : "",
      r.ticketCreateMs.join(" "),
      r.requestErrors,
      r.turnErrors,
      Math.round(r.durationMs),
    ]),
  );
}

/**
 * Runs 1/5/10/25 scripted callers concurrently through the real API and
 * reports latency, queue wait, error rate and ticket-creation time.
 */
export function LoadTestDrawer({ open, onClose, maxConcurrent, llmConfigured }: LoadTestDrawerProps) {
  const [callers, setCallers] = useState<number>(5);
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 10_000));
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<LoadTestProgress | null>(null);
  const [outcome, setOutcome] = useState<{ results: CallerResult[]; summary: LoadTestSummary } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const overCap = callers > maxConcurrent;

  async function run() {
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setOutcome(null);
    setError(null);
    setProgress({ started: 0, finished: 0, turns: 0, results: [] });
    try {
      const result = await runLoadTest(
        simulatorApi,
        { callers, seed, concurrency: Math.min(callers, maxConcurrent), signal: controller.signal },
        setProgress,
      );
      setOutcome(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }

  const summary = outcome?.summary;
  const results = outcome?.results ?? progress?.results ?? [];

  return (
    <Drawer open={open} onClose={running ? () => undefined : onClose} title="Load test" width={720}>
      <div className="flex flex-col gap-5 p-6">
        <p className="text-sm text-stone-600">
          Runs scripted mock callers through the real simulator API at the same time — typed turns, no audio — and reports how the agent and the
          platform hold up. Each caller creates a SIMULATOR ticket.
          {!llmConfigured ? " The server has no LLM key, so every turn will fail NLU and escalate." : ""}
        </p>

        <fieldset className="flex flex-col gap-3" disabled={running}>
          <legend className="mb-1 text-xs font-semibold text-stone-800">Concurrent callers</legend>
          <div role="radiogroup" aria-label="Concurrent callers" className="grid grid-cols-4 gap-2">
            {LOAD_TEST_PRESETS.map((n) => (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={callers === n}
                onClick={() => setCallers(n)}
                className={cn(
                  "h-10 rounded-lg border text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800",
                  callers === n ? "border-emerald-800 bg-emerald-800 text-white" : "border-stone-200 bg-white text-stone-700 hover:border-stone-300",
                )}
              >
                {n}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-xs text-stone-700">
            Seed
            <input
              type="number"
              min={0}
              value={seed}
              onChange={(event) => setSeed(Math.max(0, Number(event.target.value) || 0))}
              className="h-8 w-28 rounded-lg border border-stone-200/80 px-2 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-800"
            />
            <span className="text-[11px] text-stone-500">Same seed, same callers — rerun to reproduce a failure.</span>
          </label>
          {overCap ? (
            <p className="text-[11px] text-amber-700">
              The server allows {maxConcurrent} simultaneous simulator sessions, so callers beyond that queue behind the first {maxConcurrent}.
            </p>
          ) : null}
        </fieldset>

        <div className="flex gap-2">
          {running ? (
            <Button variant="danger" onClick={() => abortRef.current?.abort()} icon={<Square className="size-4" aria-hidden="true" />}>
              Stop
            </Button>
          ) : (
            <Button onClick={() => void run()} icon={<Play className="size-4" aria-hidden="true" />}>
              Run {callers} caller{callers === 1 ? "" : "s"}
            </Button>
          )}
          {outcome ? (
            <>
              <Button
                variant="secondary"
                icon={<Download className="size-4" aria-hidden="true" />}
                onClick={() => downloadFile(`hfmg-sim-loadtest-${callers}x-seed${seed}.csv`, resultsCsv(outcome.results), "text/csv")}
              >
                CSV
              </Button>
              <Button
                variant="secondary"
                icon={<Download className="size-4" aria-hidden="true" />}
                onClick={() =>
                  downloadFile(
                    `hfmg-sim-loadtest-${callers}x-seed${seed}.json`,
                    JSON.stringify({ callers, seed, summary: outcome.summary, results: outcome.results }, null, 2),
                    "application/json",
                  )
                }
              >
                JSON
              </Button>
            </>
          ) : null}
        </div>

        {progress && running ? (
          <div role="status" aria-live="polite" className="flex flex-col gap-1">
            <div className="h-2 overflow-hidden rounded-full bg-stone-100">
              <div className="h-full bg-emerald-700 transition-all" style={{ width: `${(progress.finished / callers) * 100}%` }} />
            </div>
            <p className="text-xs text-stone-600">
              {progress.finished} of {callers} callers finished · {progress.turns} turns
            </p>
          </div>
        ) : null}

        {error ? <p role="alert" className="rounded-lg bg-rose-50 p-3 text-xs text-rose-800">{error}</p> : null}

        {summary ? (
          <section aria-labelledby="loadtest-summary" className="flex flex-col gap-3">
            <h3 id="loadtest-summary" className="text-sm font-semibold text-stone-900">Summary</h3>
            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="Passed" value={`${summary.passed}/${summary.callers}`} tone={summary.passed === summary.callers ? "good" : "bad"} />
              <Stat label="Error rate" value={`${(summary.errorRate * 100).toFixed(1)}%`} tone={summary.errorRate > 0 ? "bad" : "good"} />
              <Stat label="Avg turn latency" value={formatMs(summary.roundTrip.avg)} />
              <Stat label="Max turn latency" value={formatMs(summary.roundTrip.max)} />
              <Stat label="p95 turn latency" value={formatMs(summary.roundTrip.p95)} />
              <Stat label="Avg agent (LLM)" value={formatMs(summary.llm.avg)} />
              <Stat label="Avg / max queue wait" value={`${formatMs(summary.queueWait.avg)} / ${formatMs(summary.queueWait.max)}`} />
              <Stat label="Ticket creation avg / max" value={`${formatMs(summary.ticketCreation.avg)} / ${formatMs(summary.ticketCreation.max)}`} />
              <Stat label="Turns" value={String(summary.turns)} />
              <Stat label="Failed requests" value={String(summary.failedRequests)} tone={summary.failedRequests ? "bad" : undefined} />
              <Stat label="Throughput" value={`${summary.throughputTurnsPerSecond.toFixed(1)} turns/s`} />
              <Stat label="Wall clock" value={formatMs(summary.wallClockMs)} />
            </dl>
            <p className="text-[11px] text-stone-500">
              Turn latency is measured in this browser per request. Queue wait is server time spent waiting for a database connection and the session lock.
            </p>
          </section>
        ) : null}

        {results.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-xs">
              <caption className="sr-only">Per-caller results</caption>
              <thead>
                <tr className="border-b border-stone-200 text-left text-[11px] text-stone-500">
                  <th scope="col" className="py-1.5 font-medium">#</th>
                  <th scope="col" className="py-1.5 font-medium">Result</th>
                  <th scope="col" className="py-1.5 font-medium">Outcome</th>
                  <th scope="col" className="py-1.5 font-medium">Category / priority</th>
                  <th scope="col" className="py-1.5 text-right font-medium">Turns</th>
                  <th scope="col" className="py-1.5 text-right font-medium">Max RTT</th>
                </tr>
              </thead>
              <tbody>
                {[...results].sort((a, b) => a.index - b.index).map((r) => (
                  <tr key={r.index} className="border-b border-stone-100 align-top">
                    <td className="py-1.5 tabular-nums">{r.index + 1}</td>
                    <td className="py-1.5">
                      <span className={r.passed ? "font-semibold text-emerald-700" : "font-semibold text-rose-700"}>{r.passed ? "Pass" : "Fail"}</span>
                      {r.failures.length ? <p className="text-[11px] text-rose-700">{r.failures.join("; ")}</p> : null}
                    </td>
                    <td className="py-1.5">
                      {r.finalState ?? "—"}
                      {r.ticketNumber ? <p className="font-mono text-[11px] text-stone-500">{r.ticketNumber}</p> : null}
                    </td>
                    <td className="py-1.5">
                      {r.category ?? "—"} / {r.priority ?? "—"}
                      {r.expectedCategory ? <p className="text-[11px] text-stone-500">expected {r.expectedCategory} / {r.expectedPriority}</p> : null}
                    </td>
                    <td className="py-1.5 text-right tabular-nums">{r.turns}</td>
                    <td className="py-1.5 text-right tabular-nums">{r.roundTripMs.length ? formatMs(Math.max(...r.roundTripMs)) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </Drawer>
  );
}

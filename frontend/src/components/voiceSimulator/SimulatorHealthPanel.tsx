import { Activity } from "lucide-react";
import { useSimulatorStatsQuery } from "../../api/voiceSimulator";
import { formatMs } from "../../lib/voiceSimulator/latency";
import { Card } from "../ui/Card";

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col" title={hint}>
      <dt className="text-[11px] text-stone-500">{label}</dt>
      <dd className="text-sm font-semibold text-stone-900 tabular-nums">{value}</dd>
    </div>
  );
}

/**
 * Observability roll-up across every simulated session in the last 24h
 * (GET /voice-simulator/stats): session counts, per-stage latency and
 * failure rate. Refreshes every 15 seconds.
 */
export function SimulatorHealthPanel() {
  const { data, isError } = useSimulatorStatsQuery(24);

  return (
    <Card padding="sm">
      <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold text-stone-800">
        <Activity className="size-3.5 text-emerald-700" aria-hidden="true" />
        Simulator health · last 24h
      </h2>
      {isError ? (
        <p className="text-xs text-stone-500">Stats unavailable.</p>
      ) : (
        <dl className="grid grid-cols-3 gap-x-4 gap-y-2 sm:grid-cols-5 lg:grid-cols-9">
          <Metric label="Sessions" value={data ? String(data.sessions_total) : "…"} />
          <Metric label="Active now" value={data ? String(data.sessions_active) : "…"} />
          <Metric label="Tickets" value={data ? String(data.tickets_created) : "…"} />
          <Metric label="Turns" value={data ? String(data.turns_total) : "…"} />
          <Metric
            label="Failure rate"
            value={data ? `${(data.failure_rate * 100).toFixed(1)}%` : "…"}
            hint="Turns that recorded an STT, LLM, TTS or agent error"
          />
          <Metric label="STT avg" value={formatMs(data?.stages.stt_ms.avg)} />
          <Metric label="LLM avg / p95" value={data ? `${formatMs(data.stages.llm_ms.avg)} / ${formatMs(data.stages.llm_ms.p95)}` : "…"} />
          <Metric label="TTS avg" value={formatMs(data?.stages.tts_ms.avg)} />
          <Metric label="End-to-end p95" value={formatMs(data?.stages.turn_total_ms.p95)} />
        </dl>
      )}
    </Card>
  );
}

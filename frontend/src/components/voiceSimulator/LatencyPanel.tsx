import { Info } from "lucide-react";
import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_AXIS_COLOR, CHART_COLORS, CHART_GRID_COLOR, CHART_TOOLTIP_BG } from "../../lib/chartColors";
import { cn } from "../../lib/cn";
import { BUDGETS, STAGES, budgetLevel, formatMs, statsByStage } from "../../lib/voiceSimulator/latency";
import type { BudgetLevel } from "../../lib/voiceSimulator/latency";
import type { LatencyStage, TurnTimings } from "../../types/voiceSimulator";
import { Card } from "../ui/Card";

const LEVEL_CLASS: Record<BudgetLevel, string> = {
  good: "text-emerald-700",
  warn: "text-amber-700",
  fail: "text-rose-700",
  none: "text-stone-900",
};
const LEVEL_LABEL: Record<BudgetLevel, string> = { good: "within budget", warn: "warn", fail: "over budget", none: "" };

const STAGE_COLOR: Partial<Record<LatencyStage, string>> = {
  capture_ms: CHART_COLORS[4],
  stt_ms: CHART_COLORS[1],
  llm_ms: CHART_COLORS[0],
  tts_ms: CHART_COLORS[2],
  playback_start_ms: CHART_COLORS[5],
  turn_total_ms: CHART_COLORS[3],
};

export interface LatencyPanelProps {
  turns: TurnTimings[];
  selectedTurnIndex: number | null;
  isLoading: boolean;
}

/**
 * Per-stage latency: current / average / max, a trend across turns, and a
 * waterfall for the selected turn. Budgets come from the production phone
 * constraint (latency.ts BUDGETS).
 */
const STAGE_KEYS = STAGES.map((s) => s.key);

export function LatencyPanel({ turns, selectedTurnIndex, isLoading }: LatencyPanelProps) {
  const stats = useMemo(() => statsByStage(turns, STAGE_KEYS), [turns]);
  const trend = useMemo(
    () =>
      turns
        .filter((t) => t.turn_index > 0)
        .map((t) => ({ turn: t.turn_index, ...Object.fromEntries(STAGE_KEYS.map((s) => [s, t[s]])) })),
    [turns],
  );
  const selected = turns.find((t) => t.turn_index === selectedTurnIndex) ?? turns[turns.length - 1] ?? null;
  const waterfall = selected
    ? [
        {
          name: `Turn ${selected.turn_index}`,
          capture_ms: selected.capture_ms ?? 0,
          stt_ms: selected.stt_ms ?? 0,
          llm_ms: selected.llm_ms ?? 0,
          tts_ms: selected.tts_ms ?? 0,
          playback_start_ms: selected.playback_start_ms ?? 0,
        },
      ]
    : [];

  return (
    <Card padding="none">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-stone-100 px-5 py-3">
        <h2 className="text-sm font-semibold text-stone-900">Latency</h2>
        <p className="flex items-center gap-1 text-[11px] text-stone-500">
          <Info className="size-3" aria-hidden="true" />
          STT and TTS here use OpenAI audio models; production uses the SIP gateway (Whisper and OpenAI TTS), so treat those two as indicative only.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <caption className="sr-only">Latency per stage for this session</caption>
          <thead>
            <tr className="border-b border-stone-100 text-left text-[11px] tracking-wide text-stone-500 uppercase">
              <th scope="col" className="px-5 py-2 font-semibold">Stage</th>
              <th scope="col" className="px-3 py-2 text-right font-semibold">Current</th>
              <th scope="col" className="px-3 py-2 text-right font-semibold">Average</th>
              <th scope="col" className="px-3 py-2 text-right font-semibold">p95</th>
              <th scope="col" className="px-3 py-2 text-right font-semibold">Max</th>
              <th scope="col" className="px-5 py-2 text-right font-semibold">Budget</th>
            </tr>
          </thead>
          <tbody>
            {STAGES.map((stage) => {
              const s = stats[stage.key];
              const level = budgetLevel(stage.key, s.current);
              const budget = BUDGETS[stage.key];
              return (
                <tr key={stage.key} className={cn("border-b border-stone-50", stage.key === "turn_total_ms" && "bg-stone-50/70 font-semibold")}>
                  <th scope="row" className="px-5 py-2 text-left font-medium text-stone-800">
                    <span title={`${stage.description} Measured by the ${stage.measuredBy}.`}>
                      {stage.label}
                      {stage.notRepresentative ? <sup className="ml-0.5 text-stone-400" aria-label="indicative only">*</sup> : null}
                    </span>
                  </th>
                  <td className={cn("px-3 py-2 text-right tabular-nums", LEVEL_CLASS[level])}>
                    {isLoading && s.n === 0 ? "…" : formatMs(s.current)}
                    {level !== "none" && level !== "good" ? <span className="sr-only"> ({LEVEL_LABEL[level]})</span> : null}
                  </td>
                  <td className="px-3 py-2 text-right text-stone-700 tabular-nums">{formatMs(s.avg)}</td>
                  <td className="px-3 py-2 text-right text-stone-700 tabular-nums">{formatMs(s.p95)}</td>
                  <td className={cn("px-3 py-2 text-right tabular-nums", LEVEL_CLASS[budgetLevel(stage.key, s.max)])}>{formatMs(s.max)}</td>
                  <td className="px-5 py-2 text-right text-[11px] text-stone-500">
                    {budget ? `warn ${formatMs(budget.warn)} · fail ${formatMs(budget.fail)}` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="grid grid-cols-1 gap-6 p-5 lg:grid-cols-3">
        <figure className="lg:col-span-2">
          <figcaption className="mb-2 text-xs font-semibold text-stone-700">Trend across turns</figcaption>
          {trend.length === 0 ? (
            <p className="py-10 text-center text-xs text-stone-500">Timings appear after the first caller turn.</p>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={trend} margin={{ left: 4, right: 12, top: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_COLOR} vertical={false} />
                <XAxis dataKey="turn" tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }} label={{ value: "Turn", position: "insideBottomRight", offset: -4, fontSize: 11, fill: CHART_AXIS_COLOR }} />
                <YAxis tickFormatter={(v: number) => formatMs(v)} tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }} width={56} />
                <Tooltip
                  contentStyle={{ background: CHART_TOOLTIP_BG, border: "1px solid #E7E5E4", borderRadius: 8, fontSize: 12 }}
                  formatter={(value, name) => [formatMs(typeof value === "number" ? value : null), STAGES.find((s) => s.key === name)?.label ?? String(name)]}
                  labelFormatter={(label) => `Turn ${label}`}
                />
                <Legend formatter={(value) => STAGES.find((s) => s.key === value)?.label ?? value} wrapperStyle={{ fontSize: 11 }} />
                <ReferenceLine y={BUDGETS.turn_total_ms?.warn} stroke={CHART_COLORS[2]} strokeDasharray="4 4" />
                {STAGES.map((stage) => (
                  <Line
                    key={stage.key}
                    type="monotone"
                    dataKey={stage.key}
                    stroke={STAGE_COLOR[stage.key]}
                    strokeWidth={stage.key === "turn_total_ms" ? 2.5 : 1.5}
                    dot={{ r: 2 }}
                    connectNulls
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </figure>

        <figure>
          <figcaption className="mb-2 text-xs font-semibold text-stone-700">
            Where the time went{selected ? ` — turn ${selected.turn_index}` : ""}
          </figcaption>
          {selected ? (
            <>
              <ResponsiveContainer width="100%" height={120}>
                <BarChart data={waterfall} layout="vertical" margin={{ left: 0, right: 8 }}>
                  <XAxis type="number" tickFormatter={(v: number) => formatMs(v)} tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }} />
                  <YAxis type="category" dataKey="name" hide />
                  <Tooltip
                    contentStyle={{ background: CHART_TOOLTIP_BG, border: "1px solid #E7E5E4", borderRadius: 8, fontSize: 12 }}
                    formatter={(value, name) => [formatMs(typeof value === "number" ? value : null), STAGES.find((s) => s.key === name)?.label ?? String(name)]}
                  />
                  {(["capture_ms", "stt_ms", "llm_ms", "tts_ms", "playback_start_ms"] as const).map((key) => (
                    <Bar key={key} dataKey={key} stackId="t" fill={STAGE_COLOR[key]} isAnimationActive={false} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
              <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-stone-600">
                {(["capture_ms", "stt_ms", "llm_ms", "tts_ms", "playback_start_ms", "turn_total_ms"] as const).map((key) => (
                  <li key={key} className="flex items-center gap-1.5">
                    <span className="size-2 rounded-full" style={{ background: STAGE_COLOR[key] }} aria-hidden="true" />
                    {STAGES.find((s) => s.key === key)?.label}: <span className="tabular-nums">{formatMs(selected[key])}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="py-10 text-center text-xs text-stone-500">No turns yet.</p>
          )}
        </figure>
      </div>
    </Card>
  );
}

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useCallsByDayQuery } from "../../api/analytics";
import { CHART_AXIS_COLOR, CHART_COLORS, CHART_GRID_COLOR, CHART_TOOLTIP_BG } from "../../lib/chartColors";
import { ChartCard } from "./ChartCard";

function formatDayTick(value: unknown): string {
  const date = new Date(String(value));
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Calls per Day — GET /analytics/calls-by-day, zero-filled for every day in the window (Backend Tier 3). Single series only. */
export function CallsPerDayChart({ days }: { days: number }) {
  const { data, isLoading, isError, refetch } = useCallsByDayQuery(days);
  const items = data?.items ?? [];
  const total = items.reduce((sum, item) => sum + item.count, 0);

  return (
    <ChartCard title="Calls per Day" isLoading={isLoading} isError={isError} onRetry={refetch} isEmpty={total === 0}>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={items}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_COLOR} vertical={false} />
          <XAxis dataKey="date" tickFormatter={formatDayTick} tick={{ fill: CHART_AXIS_COLOR, fontSize: 12 }} />
          <YAxis allowDecimals={false} tick={{ fill: CHART_AXIS_COLOR, fontSize: 12 }} />
          <Tooltip
            contentStyle={{ background: CHART_TOOLTIP_BG, border: "none", borderRadius: 8 }}
            labelFormatter={formatDayTick}
          />
          <Line type="monotone" dataKey="count" stroke={CHART_COLORS[0]} strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

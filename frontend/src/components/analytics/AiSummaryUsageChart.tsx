import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useAiSummaryUsageQuery } from "../../api/analytics";
import { CHART_AXIS_COLOR, CHART_COLORS, CHART_GRID_COLOR, CHART_TOOLTIP_BG } from "../../lib/chartColors";
import { ChartCard } from "./ChartCard";

/** AI Summary Usage — GET /analytics/ai-summary-usage, bucketed by ai_summary_status (Backend Tier 3). */
export function AiSummaryUsageChart({ days }: { days: number }) {
  const { data, isLoading, isError, refetch } = useAiSummaryUsageQuery(days);
  const items = data?.items ?? [];
  const total = items.reduce((sum, item) => sum + item.count, 0);

  return (
    <ChartCard title="AI Summary Usage" isLoading={isLoading} isError={isError} onRetry={refetch} isEmpty={total === 0}>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={items}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_COLOR} vertical={false} />
          <XAxis dataKey="status" tick={{ fill: CHART_AXIS_COLOR, fontSize: 12 }} />
          <YAxis allowDecimals={false} tick={{ fill: CHART_AXIS_COLOR, fontSize: 12 }} />
          <Tooltip
            contentStyle={{ background: CHART_TOOLTIP_BG, border: "none", borderRadius: 8 }}
            formatter={(value, _name, entry) => {
              const percentage = (entry.payload as { percentage?: number }).percentage ?? 0;
              return [`${value} (${percentage}%)`, "Tickets"];
            }}
          />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {items.map((_, index) => (
              <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

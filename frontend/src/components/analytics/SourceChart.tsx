import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { useTicketsBySourceQuery } from "../../api/analytics";
import { CHART_COLORS, CHART_TOOLTIP_BG } from "../../lib/chartColors";
import { ChartCard } from "./ChartCard";

/**
 * Tickets by Source — GET /analytics/tickets-by-source. Pie chart is only
 * used because source has exactly 4 possible values, within
 * DESIGN_SYSTEM.md §17's "pie only when ≤4 slices" rule.
 */
export function SourceChart({ days }: { days: number }) {
  const { data, isLoading, isError, refetch } = useTicketsBySourceQuery(days);
  const items = data?.items ?? [];
  const total = items.reduce((sum, item) => sum + item.count, 0);

  return (
    <ChartCard title="Tickets by Source" isLoading={isLoading} isError={isError} onRetry={refetch} isEmpty={total === 0}>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie data={items} dataKey="count" nameKey="source" innerRadius={50} outerRadius={80} paddingAngle={2}>
            {items.map((_, index) => (
              <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip contentStyle={{ background: CHART_TOOLTIP_BG, border: "none", borderRadius: 8 }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
        </PieChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

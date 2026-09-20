import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTicketsByPriorityQuery } from "../../api/analytics";
import { CHART_AXIS_COLOR, CHART_COLORS, CHART_GRID_COLOR, CHART_TOOLTIP_BG } from "../../lib/chartColors";
import { ChartCard } from "./ChartCard";

/** Tickets by Priority — GET /analytics/tickets-by-priority, zero-filled for all 4 levels (Backend Tier 3). */
export function PriorityChart({ days }: { days: number }) {
  const { data, isLoading, isError, refetch } = useTicketsByPriorityQuery(days);
  const items = data?.items ?? [];
  const total = items.reduce((sum, item) => sum + item.count, 0);

  return (
    <ChartCard title="Tickets by Priority" isLoading={isLoading} isError={isError} onRetry={refetch} isEmpty={total === 0}>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={items}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_COLOR} vertical={false} />
          <XAxis dataKey="priority" tick={{ fill: CHART_AXIS_COLOR, fontSize: 12 }} />
          <YAxis allowDecimals={false} tick={{ fill: CHART_AXIS_COLOR, fontSize: 12 }} />
          <Tooltip contentStyle={{ background: CHART_TOOLTIP_BG, border: "none", borderRadius: 8 }} />
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

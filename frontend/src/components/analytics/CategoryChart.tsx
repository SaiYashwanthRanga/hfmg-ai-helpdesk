import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTicketsByCategoryQuery } from "../../api/analytics";
import { CHART_AXIS_COLOR, CHART_COLORS, CHART_GRID_COLOR, CHART_TOOLTIP_BG } from "../../lib/chartColors";
import { ChartCard } from "./ChartCard";

/** Tickets by Category — GET /analytics/tickets-by-category, real (Backend Tier 3). */
export function CategoryChart({ days }: { days: number }) {
  const { data, isLoading, isError, refetch } = useTicketsByCategoryQuery(days);
  const items = data?.items ?? [];

  return (
    <ChartCard title="Tickets by Category" isLoading={isLoading} isError={isError} onRetry={refetch} isEmpty={items.length === 0}>
      <ResponsiveContainer width="100%" height={Math.max(200, items.length * 40)}>
        <BarChart data={items} layout="vertical" margin={{ left: 24 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_COLOR} horizontal={false} />
          <XAxis type="number" allowDecimals={false} tick={{ fill: CHART_AXIS_COLOR, fontSize: 12 }} />
          <YAxis type="category" dataKey="category" width={140} tick={{ fill: CHART_AXIS_COLOR, fontSize: 12 }} />
          <Tooltip contentStyle={{ background: CHART_TOOLTIP_BG, border: "none", borderRadius: 8 }} />
          <Bar dataKey="count" radius={[0, 4, 4, 0]}>
            {items.map((_, index) => (
              <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

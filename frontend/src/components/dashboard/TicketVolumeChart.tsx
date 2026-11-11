import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTicketsByDayQuery } from "../../api/analytics";
import { CHART_AXIS_COLOR, CHART_COLORS, CHART_GRID_COLOR, CHART_TOOLTIP_BG } from "../../lib/chartColors";
import { Card } from "../ui/Card";
import { ErrorState } from "../ui/ErrorState";

const DAYS = 14;

/** The API sends a calendar date ("2026-11-11"); parse it as local so the label can't slip a day. */
function formatDay(value: unknown): string {
  const [year, month, day] = String(value).split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Tickets created per day over the last two weeks (GET /analytics/tickets-by-day, zero-filled). */
export function TicketVolumeChart() {
  const { data, isLoading, isError, refetch } = useTicketsByDayQuery(DAYS);
  const items = data?.items ?? [];
  const total = items.reduce((sum, item) => sum + item.count, 0);

  return (
    <Card className="flex flex-col p-5 sm:p-6">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h2 className="!m-0 !text-sm font-semibold text-stone-900">Tickets per day</h2>
        <p className="text-xs text-stone-500">
          {isLoading || isError ? `Last ${DAYS} days` : (
            <>
              <span className="font-semibold tabular-nums text-stone-900">{total}</span> in the last {DAYS} days
            </>
          )}
        </p>
      </div>

      {isLoading ? (
        <div role="status" aria-label="Loading" className="h-[220px] animate-pulse rounded-lg bg-stone-100" />
      ) : isError ? (
        <ErrorState severity="degraded" title="Couldn't load ticket volume" description="Try again shortly." retry={() => refetch()} />
      ) : total === 0 ? (
        <p className="flex h-[220px] items-center justify-center text-sm text-stone-500">No tickets in the last {DAYS} days.</p>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={items} margin={{ top: 4, right: 0, bottom: 0, left: -24 }} barCategoryGap="28%">
            <CartesianGrid stroke={CHART_GRID_COLOR} vertical={false} />
            <XAxis
              dataKey="date"
              tickFormatter={formatDay}
              tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: CHART_GRID_COLOR }}
              interval="preserveStartEnd"
              minTickGap={24}
            />
            <YAxis allowDecimals={false} tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }} tickLine={false} axisLine={false} />
            <Tooltip
              cursor={{ fill: "rgba(6, 78, 59, 0.06)" }}
              contentStyle={{
                background: CHART_TOOLTIP_BG,
                border: `1px solid ${CHART_GRID_COLOR}`,
                borderRadius: 8,
                fontSize: 12,
                boxShadow: "0 4px 12px -4px rgba(28, 25, 23, 0.15)",
              }}
              labelFormatter={formatDay}
              formatter={(value) => [value, "Tickets"]}
            />
            <Bar dataKey="count" fill={CHART_COLORS[0]} radius={[3, 3, 0, 0]} maxBarSize={28} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

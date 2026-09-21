import { useCallsByDayQuery, useTicketsByCategoryQuery } from "../../api/analytics";
import { Card } from "../ui/Card";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";

/**
 * Range-scoped summary stats, distinct from the Dashboard's "right now"
 * KPIGrid. Derived from data already fetched by CategoryChart/CallsPerDayChart
 * (no new endpoint) — total tickets/calls in the selected window.
 */
export function MetricsGrid({ days }: { days: number }) {
  const categoryQuery = useTicketsByCategoryQuery(days);
  const callsQuery = useCallsByDayQuery(days);

  const totalTickets = categoryQuery.data?.items.reduce((sum, item) => sum + item.count, 0);
  const totalCalls = callsQuery.data?.items.reduce((sum, item) => sum + item.count, 0);

  if (categoryQuery.isError || callsQuery.isError) {
    return (
      <Card padding="sm">
        <ErrorState
          severity="degraded"
          title="Couldn't load summary totals"
          description="Try again shortly."
          retry={() => {
            void categoryQuery.refetch();
            void callsQuery.refetch();
          }}
        />
      </Card>
    );
  }

  if (categoryQuery.isLoading || callsQuery.isLoading) {
    return (
      <div className="grid grid-cols-2 gap-4">
        <LoadingState variant="card" />
        <LoadingState variant="card" />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4">
      <Card padding="sm">
        <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Tickets in range</p>
        <p className="mt-1 text-3xl font-bold tabular-nums text-foreground">{totalTickets ?? "—"}</p>
      </Card>
      <Card padding="sm">
        <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Calls in range</p>
        <p className="mt-1 text-3xl font-bold tabular-nums text-foreground">{totalCalls ?? "—"}</p>
      </Card>
    </div>
  );
}

import { useEscalationRateQuery } from "../../api/analytics";
import { Card } from "../ui/Card";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";

/**
 * Escalation Rate — single stat (WIREFRAMES.md §7 draws it this way, not a
 * chart). GET /analytics/escalation-rate; definition is disclosed, not
 * product-confirmed — see FRONTEND_GAP_REPORT.md / REMAINING_PRODUCT_DECISIONS.md.
 */
export function EscalationChart({ days }: { days: number }) {
  const { data, isLoading, isError, refetch } = useEscalationRateQuery(days);

  return (
    <Card>
      <h3 className="mb-4 text-sm font-semibold text-foreground">Escalation Rate</h3>
      {isLoading ? (
        <LoadingState variant="card" />
      ) : isError ? (
        <ErrorState severity="degraded" title="Couldn't load this metric" description="Try again shortly." retry={() => refetch()} />
      ) : data ? (
        <>
          <p className="text-4xl font-bold tabular-nums text-foreground">{data.rate_percent}%</p>
          <p
            className="mt-1 text-xs text-muted-foreground"
            title="escalated_calls / calls that reached a terminal state — a disclosed definition, not yet product-confirmed"
          >
            {data.escalated_calls} of {data.total_terminal_calls} completed calls escalated
          </p>
        </>
      ) : null}
    </Card>
  );
}

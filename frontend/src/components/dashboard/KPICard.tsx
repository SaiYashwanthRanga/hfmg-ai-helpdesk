import { Card } from "../ui/Card";
import { LoadingState } from "../ui/LoadingState";
import type { KpiValue } from "../../types/analytics";

export interface KPICardProps {
  label: string;
  data?: KpiValue;
  isLoading?: boolean;
}

/**
 * A single Dashboard KPI (DESIGN_SYSTEM.md §12). Renders `—` with the
 * server's own `blocked_reason` as a tooltip when a metric is blocked
 * (e.g. AI Resolution Rate) — never a fabricated number
 * (FRONTEND_IMPLEMENTATION_PLAN.md Phase 4 acceptance criteria).
 */
export function KPICard({ label, data, isLoading }: KPICardProps) {
  if (isLoading || !data) {
    return <LoadingState variant="card" />;
  }

  return (
    <Card padding="sm">
      <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{label}</p>
      {data.status === "ready" ? (
        <p className="mt-1 text-3xl font-bold tabular-nums text-foreground">{data.value}</p>
      ) : (
        <p className="mt-1 text-3xl font-bold text-muted-foreground" title={data.blocked_reason ?? undefined}>
          —
        </p>
      )}
      {data.status === "blocked" ? (
        <p className="mt-1 text-xs text-muted-foreground">Definition pending</p>
      ) : null}
    </Card>
  );
}

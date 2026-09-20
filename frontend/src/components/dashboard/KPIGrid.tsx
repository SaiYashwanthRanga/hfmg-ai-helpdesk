import { useKpisQuery } from "../../api/analytics";
import { ErrorState } from "../ui/ErrorState";
import { KPICard } from "./KPICard";

/** Five KPI cards backed by GET /analytics/kpis (real, all fields — DESIGN.md §6.2). */
export function KPIGrid() {
  const { data, isLoading, isError, refetch } = useKpisQuery();

  if (isError) {
    return (
      <ErrorState
        severity="degraded"
        title="Couldn't load KPIs"
        description="The analytics endpoint didn't respond. Other parts of the dashboard still work."
        retry={() => refetch()}
      />
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-5">
      <KPICard label="Open Tickets" data={data?.open_tickets} isLoading={isLoading} />
      <KPICard label="Tickets Today" data={data?.tickets_today} isLoading={isLoading} />
      <KPICard label="Calls Today" data={data?.calls_today} isLoading={isLoading} />
      <KPICard label="Escalations" data={data?.escalations} isLoading={isLoading} />
      <KPICard label="AI Resolution Rate" data={data?.ai_resolution_rate} isLoading={isLoading} />
    </div>
  );
}

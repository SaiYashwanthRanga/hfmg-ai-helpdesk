import { AlertTriangle, RefreshCw } from "lucide-react";
import { useKpisQuery } from "../../api/analytics";
import { KPICard } from "./KPICard";
import { Button } from "../ui/Button";

/** Five KPI cards backed directly by GET /analytics/kpis with full transparency. */
export function KPIGrid() {
  const { data, isLoading, isError, refetch } = useKpisQuery();

  if (isError) {
    return (
      <div className="flex items-center justify-between rounded-lg border border-rose-200 bg-rose-50 p-4 text-xs">
        <div className="flex items-center gap-2 text-rose-800">
          <AlertTriangle className="size-4 shrink-0 text-rose-600" />
          <span>Unable to retrieve live database KPIs. Please check backend connection.</span>
        </div>
        <Button variant="ghost" onClick={() => refetch()} className="h-7 px-2.5 gap-1.5 text-xs text-rose-700 hover:bg-rose-100 hover:text-rose-900">
          <RefreshCw className="size-3" />
          Retry
        </Button>
      </div>
    );
  }


  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-5">
      <KPICard
        label="Open Tickets"
        data={data?.open_tickets}
        isLoading={isLoading}
        iconType="ticket"
        to="/tickets?status=OPEN"
      />
      <KPICard
        label="Tickets Today"
        data={data?.tickets_today}
        isLoading={isLoading}
        iconType="zap"
        to="/tickets"
      />
      <KPICard
        label="Calls Today"
        data={data?.calls_today}
        isLoading={isLoading}
        iconType="call"
        to="/calls"
      />
      <KPICard
        label="Escalations"
        data={data?.escalations}
        isLoading={isLoading}
        iconType="alert"
        to="/tickets?priority=URGENT"
      />
      <KPICard
        label="AI Resolution Rate"
        data={data?.ai_resolution_rate}
        isLoading={isLoading}
        iconType="ai"
        to="/analytics"
      />
    </div>
  );
}


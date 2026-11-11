import { AlertTriangle, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { useKpisQuery } from "../../api/analytics";
import { cn } from "../../lib/cn";
import type { KpiReport, KpiValue } from "../../types/analytics";
import { Button } from "../ui/Button";

interface Kpi {
  key: keyof KpiReport;
  label: string;
  hint: string;
  to: string;
  /** A non-zero value here is a problem, not a volume. */
  alarming?: boolean;
}

const KPIS: Kpi[] = [
  { key: "open_tickets", label: "Open tickets", hint: "Not yet resolved", to: "/tickets?status=OPEN" },
  { key: "tickets_today", label: "Tickets today", hint: "Created since midnight UTC", to: "/tickets" },
  { key: "calls_today", label: "Calls today", hint: "Answered by the voice agent", to: "/calls" },
  { key: "escalations", label: "Escalations today", hint: "Calls handed to a person", to: "/calls", alarming: true },
];

/**
 * The day's numbers as one ruled strip (GET /analytics/kpis). A KPI whose
 * definition is still undecided server-side ("blocked") is left out here
 * rather than shown as a dash. AI resolution rate is not listed for that
 * reason (REMAINING_PRODUCT_DECISIONS.md §1).
 */
export function KPIGrid() {
  const { data, isLoading, isError, refetch } = useKpisQuery();

  if (isError) {
    return (
      <div className="flex items-center justify-between rounded-lg border border-rose-200 bg-rose-50 p-4 text-xs">
        <div className="flex items-center gap-2 text-rose-800">
          <AlertTriangle className="size-4 shrink-0 text-rose-600" aria-hidden="true" />
          <span>Today's numbers couldn't be loaded. Check the backend connection.</span>
        </div>
        <Button variant="ghost" onClick={() => refetch()} className="h-7 gap-1.5 px-2.5 text-xs text-rose-700 hover:bg-rose-100 hover:text-rose-900">
          <RefreshCw className="size-3" aria-hidden="true" />
          Retry
        </Button>
      </div>
    );
  }

  const shown = KPIS.filter((kpi) => !data || data[kpi.key].status === "ready");

  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-stone-200/80 bg-stone-200/80 shadow-sm lg:grid-cols-4">
      {shown.map((kpi) => {
        const value: KpiValue | undefined = data?.[kpi.key];
        const alarmed = kpi.alarming && (value?.value ?? 0) > 0;
        return (
          <Link
            key={kpi.key}
            to={kpi.to}
            className="group flex flex-col bg-white px-5 py-4 transition-colors hover:bg-stone-50 focus-visible:relative focus-visible:z-10"
          >
            <p className="text-xs font-medium text-stone-600">{kpi.label}</p>
            <p
              className={cn(
                "mt-1.5 font-display text-3xl font-semibold leading-none tracking-tight tabular-nums",
                alarmed ? "text-rose-700" : "text-stone-900",
              )}
            >
              {isLoading || !value ? (
                <span className="inline-block h-7 w-12 animate-pulse rounded bg-stone-100 align-middle" aria-label="Loading" />
              ) : (
                value.value
              )}
            </p>
            <p className="mt-2 text-[11px] text-stone-500 transition-colors group-hover:text-stone-700">{kpi.hint}</p>
          </Link>
        );
      })}
    </div>
  );
}

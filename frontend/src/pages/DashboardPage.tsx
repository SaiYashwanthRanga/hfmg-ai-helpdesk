import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { fetchDependencyHealth } from "../api/health";
import { ActivityFeed } from "../components/dashboard/ActivityFeed";
import { AIInsightsPreview } from "../components/dashboard/AIInsightsPreview";
import { KPIGrid } from "../components/dashboard/KPIGrid";
import { RecentCallsPanel } from "../components/dashboard/RecentCallsPanel";
import { RecentTicketsPanel } from "../components/dashboard/RecentTicketsPanel";
import { SystemHealthPanel } from "../components/dashboard/SystemHealthPanel";

/**
 * Executive Dashboard — Clean, high-contrast, modern operations view.
 * Fully transparent, zero fabricated data.
 */
export function DashboardPage() {
  const { data: health } = useQuery({
    queryKey: ["health", "dependencies"],
    queryFn: fetchDependencyHealth,
    refetchInterval: 30_000,
  });

  const isVoiceReady = health?.openai.status === "operational";

  return (
    <div className="flex flex-col gap-6">
      {/* Clean, confident page header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-stone-900">Dashboard</h1>
          <p className="mt-1 text-sm text-stone-500">
            Real-time overview of AI voice agent operations, incoming tickets, and system health.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Link
            to="/calls"
            className="flex items-center gap-2 rounded-full border border-stone-200/80 bg-white px-3 py-1.5 text-xs text-stone-600 shadow-2xs hover:border-stone-300 hover:bg-stone-50 transition-colors"
            title="View AI Voice Agent status and call logs"
          >
            <span
              className={`size-2 rounded-full ${
                isVoiceReady ? "bg-emerald-500" : health ? "bg-amber-500" : "bg-stone-400"
              }`}
            />
            <span className="font-medium text-stone-800">AI Voice Agent:</span>
            <span className={isVoiceReady ? "font-semibold text-emerald-800" : "text-stone-600"}>
              {isVoiceReady ? "Online" : health ? health.openai.status : "Checking..."}
            </span>
          </Link>

          <Link
            to="/tickets/new"
            className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg bg-emerald-800 px-3.5 text-xs font-semibold text-white shadow-sm transition-all hover:bg-emerald-900 active:bg-emerald-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800 focus-visible:ring-offset-1 select-none"
          >
            <Plus className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="leading-none">New Ticket</span>
          </Link>
        </div>

      </div>

      <SystemHealthPanel />
      <KPIGrid />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <RecentTicketsPanel />
        <RecentCallsPanel />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <AIInsightsPreview />
        <ActivityFeed />
      </div>
    </div>
  );
}

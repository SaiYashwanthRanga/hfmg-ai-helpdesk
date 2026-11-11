import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { fetchDependencyHealth } from "../api/health";
import { AIBriefing } from "../components/aiInsights/AIBriefing";
import { ThemeList } from "../components/aiInsights/ThemeList";
import { KPIGrid } from "../components/dashboard/KPIGrid";
import { RecentCallsPanel } from "../components/dashboard/RecentCallsPanel";
import { RecentTicketsPanel } from "../components/dashboard/RecentTicketsPanel";
import { SystemHealthPanel } from "../components/dashboard/SystemHealthPanel";
import { TicketVolumeChart } from "../components/dashboard/TicketVolumeChart";

const TODAY = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" });

/**
 * Executive dashboard, AI first (DESIGN.md §2.1–2.2): what the AI makes of the
 * recent tickets, then today's numbers, then volume and recurring problems,
 * then the latest tickets and calls. System health closes the page: it matters
 * when something is wrong, and the header's status strip already says so.
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
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="!mb-0 font-display text-2xl font-semibold tracking-tight text-stone-900">Dashboard</h1>
          <p className="mt-1 text-sm text-stone-600">{TODAY.format(new Date())}</p>
        </div>

        <div className="flex items-center gap-3">
          <Link
            to="/calls"
            className="flex h-8 items-center gap-2 rounded-full border border-stone-200/80 bg-white px-3 text-xs text-stone-600 shadow-2xs transition-colors hover:border-stone-300 hover:bg-stone-50"
            title="Voice agent status and call log"
          >
            <span
              aria-hidden="true"
              className={`size-2 rounded-full ${
                isVoiceReady ? "bg-emerald-500" : health ? "bg-amber-500" : "bg-stone-400"
              }`}
            />
            <span className="font-medium text-stone-800">Voice agent</span>
            <span className={isVoiceReady ? "font-semibold text-emerald-800" : "text-stone-600"}>
              {isVoiceReady ? "Online" : health ? health.openai.status : "Checking"}
            </span>
          </Link>

          <Link
            to="/tickets/new"
            className="inline-flex h-8 select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-lg bg-emerald-800 px-3.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-emerald-900 active:bg-emerald-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800 focus-visible:ring-offset-1"
          >
            <Plus className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="leading-none">New ticket</span>
          </Link>
        </div>
      </div>

      <AIBriefing days={30} />
      <KPIGrid />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <TicketVolumeChart />
        <ThemeList days={30} limit={4} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <RecentTicketsPanel />
        <RecentCallsPanel />
      </div>

      <SystemHealthPanel />
    </div>
  );
}

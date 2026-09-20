import { ActivityFeed } from "../components/dashboard/ActivityFeed";
import { AIInsightsPreview } from "../components/dashboard/AIInsightsPreview";
import { KPIGrid } from "../components/dashboard/KPIGrid";
import { RecentCallsPanel } from "../components/dashboard/RecentCallsPanel";
import { RecentTicketsPanel } from "../components/dashboard/RecentTicketsPanel";
import { SystemHealthPanel } from "../components/dashboard/SystemHealthPanel";

/**
 * Executive Dashboard (WIREFRAMES.md §2, DESIGN.md §2.2 — legible within
 * 10 seconds). Every panel fetches independently so one slow endpoint
 * never blocks the rest of the page from rendering.
 */
export function DashboardPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>

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

import { useState } from "react";
import { AiSummaryUsageChart } from "../components/analytics/AiSummaryUsageChart";
import { CallsPerDayChart } from "../components/analytics/CallsPerDayChart";
import { CategoryChart } from "../components/analytics/CategoryChart";
import { DateRangeSelect } from "../components/analytics/DateRangeSelect";
import { EscalationChart } from "../components/analytics/EscalationChart";
import { MetricsGrid } from "../components/analytics/MetricsGrid";
import { PriorityChart } from "../components/analytics/PriorityChart";
import { SourceChart } from "../components/analytics/SourceChart";

/**
 * Analytics (WIREFRAMES.md §7). Every chart pre-aggregates server-side
 * (DESIGN.md §10) and is independently loading/error-stated — one failed
 * endpoint never blanks the page.
 */
export function AnalyticsPage() {
  const [days, setDays] = useState(30);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-foreground">Analytics</h1>
        <DateRangeSelect days={days} onChange={setDays} />
      </div>

      <MetricsGrid days={days} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <CategoryChart days={days} />
        <PriorityChart days={days} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <SourceChart days={days} />
        <CallsPerDayChart days={days} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <EscalationChart days={days} />
        <AiSummaryUsageChart days={days} />
      </div>
    </div>
  );
}

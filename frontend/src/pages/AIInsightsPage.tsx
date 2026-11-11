import { ArrowRight, FileUp } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { AIBriefing } from "../components/aiInsights/AIBriefing";
import { CategoryMovement } from "../components/aiInsights/CategoryMovement";
import { RecommendationsList } from "../components/aiInsights/RecommendationsList";
import { ThemeList } from "../components/aiInsights/ThemeList";
import { DateRangeSelect } from "../components/analytics/DateRangeSelect";

/**
 * AI Insights: one GET /ai-insights/analysis reading of the chosen period.
 * The model groups tickets into recurring problems, flags what needs
 * attention and suggests actions; every statement links to the tickets it
 * rests on, and every number is counted by the server, not the model.
 */
export function AIInsightsPage() {
  const [days, setDays] = useState(30);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="!mb-0 font-display text-2xl font-semibold tracking-tight text-stone-900">AI Insights</h1>
          <p className="mt-1 max-w-[62ch] text-sm text-stone-600">
            What the AI makes of your tickets. Open any ticket number to check the evidence.
          </p>
        </div>
        <DateRangeSelect days={days} onChange={setDays} />
      </div>

      <AIBriefing days={days} />

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <ThemeList days={days} />
        <RecommendationsList days={days} />
      </div>

      <CategoryMovement days={days} />

      <Link
        to="/ai-insights/ticket-analysis"
        className="flex items-center justify-between gap-4 rounded-xl border border-stone-200/80 bg-white p-5 shadow-sm transition-colors hover:border-stone-300 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800"
      >
        <span className="flex items-start gap-3">
          <FileUp className="mt-0.5 size-5 shrink-0 text-emerald-800" aria-hidden="true" />
          <span>
            <span className="block text-base font-semibold text-stone-900">Analyze Freshworks tickets</span>
            <span className="block text-sm text-stone-600">Upload a ticket export, get an AI-prioritized report, and send it to the IT team.</span>
          </span>
        </span>
        <ArrowRight className="size-4 shrink-0 text-stone-500" aria-hidden="true" />
      </Link>
    </div>
  );
}

import { ArrowRight, FileUp } from "lucide-react";
import { Link } from "react-router-dom";
import { useAiInsightsQuery } from "../api/aiInsights";
import { ApiError } from "../api/client";
import { CategoryBreakdownCard } from "../components/aiInsights/CategoryBreakdownCard";
import { RecommendationsCard } from "../components/aiInsights/RecommendationsCard";
import { RepeatedProblemsCard } from "../components/aiInsights/RepeatedProblemsCard";
import { RiskAlertsCard } from "../components/aiInsights/RiskAlertsCard";
import { TrendingIssuesCard } from "../components/aiInsights/TrendingIssuesCard";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";

/**
 * AI Insights (WIREFRAMES.md §8/§11). Single GET /ai-insights payload —
 * only Category Breakdown returns real data; every other section renders
 * the backend's own disclosed blocked_reason, never fabricated content
 * (see REMAINING_PRODUCT_DECISIONS.md for what would unblock each).
 */
export function AIInsightsPage() {
  const { data, isLoading, isError, error, refetch } = useAiInsightsQuery();

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-bold text-foreground">AI Insights</h1>

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

      {isLoading ? (
        <LoadingState variant="page" />
      ) : isError ? (
        <ErrorState
          severity="degraded"
          title="AI Insights is not yet available"
          description={error instanceof ApiError ? error.message : "Something went wrong. Please try again."}
          retry={() => refetch()}
        />
      ) : data ? (
        <div className="flex flex-col gap-6">
          <CategoryBreakdownCard items={data.category_breakdown.items} />
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <TrendingIssuesCard blockedReason={data.trending_issues.blocked_reason} />
            <RepeatedProblemsCard blockedReason={data.repeated_problems.blocked_reason} />
            <RiskAlertsCard blockedReason={data.high_risk_alerts.blocked_reason} />
            <RecommendationsCard blockedReason={data.recommendations.blocked_reason} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

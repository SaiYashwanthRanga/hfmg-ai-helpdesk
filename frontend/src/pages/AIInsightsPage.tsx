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

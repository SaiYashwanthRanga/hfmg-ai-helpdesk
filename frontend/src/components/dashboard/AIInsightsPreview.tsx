import { Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { useAiInsightsQuery } from "../../api/aiInsights";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";

/**
 * Dashboard preview of the AI Insights page (DESIGN.md §6.4) — shares the
 * same GET /ai-insights endpoint the full page uses, requesting a smaller
 * slice (top 3 categories) rather than a separate endpoint. Only Category
 * Breakdown has real data server-side (see api/aiInsights.ts) — this
 * preview never references the other, deliberately blocked sections.
 */
export function AIInsightsPreview() {
  const { data, isLoading, isError, refetch } = useAiInsightsQuery();

  return (
    <Card className="border-l-4 border-l-ai-accent">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Sparkles className="size-4 text-ai-accent" aria-hidden="true" />
          AI Insights
        </h2>
        <Link to="/ai-insights" className="text-xs font-medium text-primary hover:underline">
          View all
        </Link>
      </div>

      {isLoading ? (
        <LoadingState variant="card" />
      ) : isError ? (
        <ErrorState severity="degraded" title="Couldn't load insights" description="Try again shortly." retry={() => refetch()} />
      ) : !data || data.category_breakdown.items.length === 0 ? (
        <EmptyState icon={Sparkles} title="No notable patterns yet" />
      ) : (
        <ul className="flex flex-col gap-2 text-sm">
          {data.category_breakdown.items.slice(0, 3).map((item) => (
            <li key={item.category} className="flex items-center justify-between text-foreground">
              <span>{item.category}</span>
              <span className="text-muted-foreground">{item.count} tickets</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

import { ArrowRight, RefreshCw, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { useAiInsightsQuery } from "../../api/aiInsights";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { LoadingState } from "../ui/LoadingState";
import { Button } from "../ui/Button";

export function AIInsightsPreview() {
  const { data, isLoading, isError, refetch } = useAiInsightsQuery();

  const rawItems = data?.category_breakdown?.items ?? [];
  const total = rawItems.reduce((acc, c) => acc + c.count, 0) || 1;
  const categories = rawItems.slice(0, 4).map((item) => ({
    category: item.category,
    count: item.count,
    pct: Math.round((item.count / total) * 100),
  }));

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles className="size-4 text-emerald-800" />
          <h2 className="text-sm font-semibold text-stone-900">AI Insights</h2>
        </div>

        <Link
          to="/ai-insights"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-emerald-800 transition-colors hover:bg-emerald-50 hover:text-emerald-900"
        >
          <span>View all</span>
          <ArrowRight className="size-3" aria-hidden="true" />
        </Link>
      </div>

      {isLoading ? (
        <LoadingState variant="card" />
      ) : isError ? (
        <div className="flex flex-col items-center justify-center py-6 text-center text-xs text-stone-500">
          <p className="text-rose-700 font-medium">Failed to load AI insights from database</p>
          <Button variant="ghost" onClick={() => refetch()} className="mt-2 h-7 px-2.5 gap-1.5 text-xs text-stone-600 hover:bg-stone-100 hover:text-stone-900">
            <RefreshCw className="size-3" />
            Retry
          </Button>
        </div>
      ) : categories.length === 0 ? (
        <EmptyState icon={Sparkles} title="No category insight patterns yet" description="Patterns will aggregate automatically from incoming tickets." />
      ) : (
        <div className="flex flex-col gap-3">
          {categories.map((item) => (
            <div key={item.category} className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="truncate font-medium text-stone-900">{item.category}</span>
                <span className="text-stone-500 shrink-0 pl-2">
                  {item.count} tickets ({item.pct}%)
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-stone-100">
                <div
                  className="h-full rounded-full bg-emerald-800 transition-all duration-500"
                  style={{ width: `${item.pct}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}



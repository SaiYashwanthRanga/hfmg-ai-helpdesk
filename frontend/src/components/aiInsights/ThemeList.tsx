import { ArrowRight, Layers } from "lucide-react";
import { Link } from "react-router-dom";
import { useAiAnalysisQuery } from "../../api/aiInsights";
import { PriorityBadge } from "../tickets/PriorityBadge";
import { Card } from "../ui/Card";
import { TicketChips } from "./TicketChips";

export interface ThemeListProps {
  days?: number;
  /** Dashboard preview: the largest few, without notes or ticket links. */
  limit?: number;
}

/**
 * Recurring problems the model found by reading the tickets — grouped by what
 * is actually wrong, across the fixed categories. Titles and notes are the
 * model's; every count is computed from the tickets it cited.
 */
export function ThemeList({ days = 30, limit }: ThemeListProps) {
  const { data, isLoading } = useAiAnalysisQuery(days);
  const compact = limit !== undefined;
  const themes = data?.status === "ready" ? data.themes : [];
  const shown = compact ? themes.slice(0, limit) : themes;
  const largest = Math.max(1, ...themes.map((theme) => theme.ticket_count));

  return (
    <Card className="flex flex-col p-5 sm:p-6">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h2 className="!m-0 !text-sm flex items-center gap-2 font-semibold text-stone-900">
          <Layers className="size-4 text-emerald-800" aria-hidden="true" />
          Recurring problems
        </h2>
        {compact ? (
          <Link
            to="/ai-insights"
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-emerald-800 transition-colors hover:bg-emerald-50 hover:text-emerald-900"
          >
            All insights
            <ArrowRight className="size-3" aria-hidden="true" />
          </Link>
        ) : null}
      </div>
      <p className="mb-4 text-xs text-stone-500">
        Grouped by AI from what each ticket describes, across categories.
      </p>

      {isLoading ? (
        <div role="status" aria-label="Loading" className="flex flex-col gap-4">
          {[0, 1, 2].map((row) => (
            <div key={row} className="flex flex-col gap-2">
              <div className="h-4 w-2/3 animate-pulse rounded bg-stone-100" />
              <div className="h-1.5 w-full animate-pulse rounded-full bg-stone-100" />
            </div>
          ))}
        </div>
      ) : shown.length === 0 ? (
        <p className="py-6 text-sm text-stone-500">
          {data?.status === "ready"
            ? "No problem came up more than once in this period."
            : data?.status === "empty"
              ? "No tickets in this period yet."
              : "Shown once the AI briefing is available."}
        </p>
      ) : (
        <ol className={compact ? "flex flex-col gap-4" : "flex flex-col divide-y divide-stone-200/80"}>
          {shown.map((theme) => (
            <li key={theme.title} className={compact ? "" : "py-4 first:pt-0 last:pb-0"}>
              <div className="flex items-baseline justify-between gap-3">
                <p className="min-w-0 truncate text-sm font-semibold text-stone-900" title={theme.title}>
                  {theme.title}
                </p>
                <p className="shrink-0 text-xs tabular-nums text-stone-500">
                  <span className="font-semibold text-stone-900">{theme.ticket_count}</span> tickets
                  {theme.open_count > 0 ? ` · ${theme.open_count} open` : " · none open"}
                </p>
              </div>
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-stone-100">
                <div
                  className="h-full rounded-full bg-emerald-800"
                  style={{ width: `${(theme.ticket_count / largest) * 100}%` }}
                />
              </div>
              {compact ? null : (
                <>
                  {theme.note ? <p className="mt-2.5 max-w-[70ch] text-sm text-stone-600">{theme.note}</p> : null}
                  <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
                    <PriorityBadge priority={theme.top_priority} />
                    <span className="text-xs text-stone-500">{theme.categories.join(", ")}</span>
                    <TicketChips tickets={theme.tickets} />
                    {theme.ticket_count > theme.tickets.length ? (
                      <span className="text-xs tabular-nums text-stone-500">
                        +{theme.ticket_count - theme.tickets.length} more
                      </span>
                    ) : null}
                  </div>
                </>
              )}
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

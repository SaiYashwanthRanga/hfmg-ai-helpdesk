import { LayoutGrid } from "lucide-react";
import { useAiAnalysisQuery } from "../../api/aiInsights";
import { Card } from "../ui/Card";

/**
 * Tickets per category in this period beside the same-length period before
 * it. Plain counts from the database — both numbers are shown and no
 * "trending" verdict is drawn (REMAINING_PRODUCT_DECISIONS.md §2).
 */
export function CategoryMovement({ days = 30 }: { days?: number }) {
  const { data, isLoading } = useAiAnalysisQuery(days);
  const items = data?.category_movement ?? [];
  const largest = Math.max(1, ...items.flatMap((item) => [item.count, item.previous_count]));

  return (
    <Card className="p-5 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h2 className="!m-0 !text-sm flex items-center gap-2 font-semibold text-stone-900">
          <LayoutGrid className="size-4 text-emerald-800" aria-hidden="true" />
          Tickets by category
        </h2>
        <p className="flex items-center gap-4 text-xs text-stone-600">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm bg-emerald-800" aria-hidden="true" />
            Last {days} days
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm bg-stone-300" aria-hidden="true" />
            {days} days before
          </span>
        </p>
      </div>

      {isLoading ? (
        <div role="status" aria-label="Loading" className="flex flex-col gap-3">
          {[0, 1, 2, 3].map((row) => (
            <div key={row} className="h-6 animate-pulse rounded bg-stone-100" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="py-6 text-sm text-stone-500">No tickets in either period.</p>
      ) : (
        <ul className="flex flex-col gap-3.5">
          {items.map((item) => (
            <li key={item.category} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-center gap-x-4 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)]">
              <span className="truncate text-sm text-stone-800" title={item.category}>
                {item.category}
              </span>
              <div className="flex flex-col gap-1">
                <Bar value={item.count} largest={largest} className="bg-emerald-800" label={`Last ${days} days`} strong />
                <Bar value={item.previous_count} largest={largest} className="bg-stone-300" label={`${days} days before`} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Bar({ value, largest, className, label, strong }: { value: number; largest: number; className: string; label: string; strong?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1">
        <div
          className={`h-full rounded-sm ${className}`}
          style={{ width: `${(value / largest) * 100}%`, minWidth: value > 0 ? 2 : 0 }}
        />
      </div>
      <span
        className={`w-8 shrink-0 text-right text-xs tabular-nums ${strong ? "font-semibold text-stone-900" : "text-stone-500"}`}
        aria-label={`${label}: ${value}`}
      >
        {value}
      </span>
    </div>
  );
}

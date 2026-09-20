import { LayoutGrid } from "lucide-react";
import type { CategoryBreakdownItem } from "../../types/analytics";
import { Card } from "../ui/Card";

/** The one AI Insights section with real data — ticket count per category (reused from Analytics). */
export function CategoryBreakdownCard({ items }: { items: CategoryBreakdownItem[] }) {
  const max = Math.max(1, ...items.map((item) => item.count));

  return (
    <Card>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <LayoutGrid className="size-4 text-muted-foreground" aria-hidden="true" />
        Category Breakdown
      </h2>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No tickets in this range.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li key={item.category} className="flex items-center gap-3">
              <span className="w-32 shrink-0 truncate text-sm text-foreground">{item.category}</span>
              <div className="h-2 flex-1 rounded-pill bg-card-hover">
                <div
                  className="h-2 rounded-pill bg-primary"
                  style={{ width: `${(item.count / max) * 100}%` }}
                />
              </div>
              <span className="w-8 shrink-0 text-right text-sm tabular-nums text-muted-foreground">{item.count}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

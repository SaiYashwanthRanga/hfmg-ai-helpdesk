import { ListChecks } from "lucide-react";
import { useAiAnalysisQuery } from "../../api/aiInsights";
import { Card } from "../ui/Card";
import { TicketChips } from "./TicketChips";

/**
 * What the model suggests the IT lead does, each with the tickets it rests on.
 * Advice to review: nothing here changes a ticket.
 */
export function RecommendationsList({ days = 30 }: { days?: number }) {
  const { data, isLoading } = useAiAnalysisQuery(days);
  const items = data?.status === "ready" ? data.recommendations : [];

  return (
    <Card className="p-5 sm:p-6">
      <h2 className="!m-0 !mb-1 !text-sm flex items-center gap-2 font-semibold text-stone-900">
        <ListChecks className="size-4 text-emerald-800" aria-hidden="true" />
        Suggested actions
      </h2>
      <p className="mb-4 text-xs text-stone-500">AI suggestions for the IT lead to review. Nothing is changed automatically.</p>

      {isLoading ? (
        <div role="status" aria-label="Loading" className="flex flex-col gap-3">
          {[0, 1].map((row) => (
            <div key={row} className="h-12 animate-pulse rounded-lg bg-stone-100" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="py-6 text-sm text-stone-500">
          {data?.status === "ready"
            ? "The tickets in this period don't point to a specific action."
            : data?.status === "empty"
              ? "No tickets in this period yet."
              : "Shown once the AI briefing is available."}
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-stone-200/80">
          {items.map((item) => (
            <li key={item.action} className="py-4 first:pt-0 last:pb-0">
              <p className="text-sm font-semibold text-stone-900">{item.action}</p>
              {item.rationale ? <p className="mt-1 max-w-[70ch] text-sm text-stone-600">{item.rationale}</p> : null}
              <TicketChips tickets={item.tickets} className="mt-2.5" />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

import { RefreshCw, Sparkles } from "lucide-react";
import { Button } from "../ui/Button";
import { LoadingState } from "../ui/LoadingState";
import type { AISummaryStatus } from "../../types/ticket";

const STATUS_COPY: Record<AISummaryStatus, string> = {
  DISABLED: "AI summaries are turned off for this deployment.",
  PENDING: "Generating summary…",
  COMPLETED: "",
  FAILED: "AI summary generation failed. You can rely on the description above.",
};

export interface AISummaryPanelProps {
  summary: string | null;
  status: AISummaryStatus;
}

/**
 * AI Summary drawer section (WIREFRAMES.md §4, DESIGN_SYSTEM.md §18).
 * `POST /api/v1/tickets/{id}/regenerate-summary` is documented in
 * API_SPEC.md §3 but does not exist on the running backend
 * (backend/app/api/v1/tickets.py has no such route) — the Regenerate
 * button ships disabled with an explanatory tooltip rather than calling an
 * endpoint that would 404 (FRONTEND_IMPLEMENTATION_PLAN.md Phase 3 blocker).
 */
export function AISummaryPanel({ summary, status }: AISummaryPanelProps) {
  return (
    <section className="border-b border-l-4 border-border border-l-ai-accent bg-ai-subtle/40 px-6 py-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Sparkles className="size-4 text-ai-accent" aria-hidden="true" />
          AI Summary
        </h3>
        <Button
          variant="ghost"
          disabled
          title="Regenerating a summary isn't available yet — the backend endpoint for it hasn't been built."
        >
          <RefreshCw className="size-3.5" aria-hidden="true" />
          Regenerate
        </Button>
      </div>

      {status === "PENDING" ? (
        <LoadingState variant="card" />
      ) : summary ? (
        <p className="text-sm whitespace-pre-wrap text-foreground">{summary}</p>
      ) : (
        <p className="text-sm text-muted-foreground italic">{STATUS_COPY[status]}</p>
      )}
    </section>
  );
}

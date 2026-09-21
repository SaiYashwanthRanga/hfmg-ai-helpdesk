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
  onRegenerate: () => void;
  isRegenerating: boolean;
}

/**
 * AI Summary drawer section (WIREFRAMES.md §4, DESIGN_SYSTEM.md §18).
 * Calls `POST /api/v1/tickets/{id}/regenerate-summary`, which does exist on
 * the backend (backend/app/api/v1/tickets.py) — verified live during the
 * 2026-09-21 OpenAI integration pass; see OPENAI_INTEGRATION_REPORT.md.
 */
export function AISummaryPanel({ summary, status, onRegenerate, isRegenerating }: AISummaryPanelProps) {
  const disabled = status === "DISABLED" || status === "PENDING" || isRegenerating;

  return (
    <section className="border-b border-l-4 border-border border-l-ai-accent bg-ai-subtle/40 px-6 py-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Sparkles className="size-4 text-ai-accent" aria-hidden="true" />
          AI Summary
        </h3>
        <Button
          variant="ghost"
          disabled={disabled}
          loading={isRegenerating}
          onClick={onRegenerate}
          title={status === "DISABLED" ? "AI summaries are turned off for this deployment." : undefined}
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

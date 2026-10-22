import { Loader2 } from "lucide-react";
import type { ReportProgress } from "../../types/ticketReport";
import { Card } from "../ui/Card";

/** Shown while the analysis runs. The message is announced to screen readers; the bar is determinate once the steps are known. */
export function AnalysisProgress({ progress }: { progress: ReportProgress | null }) {
  const total = progress?.total ?? 0;
  const percent = total > 0 ? Math.round((100 * (progress?.done ?? 0)) / total) : null;
  return (
    <Card className="flex flex-col gap-3">
      <div role="status" aria-live="polite" className="flex items-center gap-2 text-sm text-stone-800">
        <Loader2 className="size-4 animate-spin text-emerald-800" aria-hidden="true" />
        <span className="font-medium">Analyzing…</span>
        <span className="text-stone-600">{progress?.message || "Starting the analysis…"}</span>
      </div>
      <div
        role="progressbar"
        aria-label="Analysis progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
        className="h-2 w-full overflow-hidden rounded-full bg-stone-200"
      >
        <div className={`h-full rounded-full bg-emerald-700 ${percent === null ? "w-1/3 animate-pulse" : "transition-all"}`} style={percent === null ? undefined : { width: `${percent}%` }} />
      </div>
      <p className="text-xs text-stone-500">You can leave this page; the analysis keeps running and the report will be here when you return.</p>
    </Card>
  );
}

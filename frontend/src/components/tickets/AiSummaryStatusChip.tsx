import { Sparkles } from "lucide-react";
import { Badge } from "../ui/Badge";
import type { AISummaryStatus } from "../../types/ticket";

const LABEL: Record<AISummaryStatus, string> = {
  PENDING: "Generating…",
  COMPLETED: "Summary ready",
  FAILED: "Summary failed",
  DISABLED: "Disabled",
};

/**
 * Table-row-scale AI Summary indicator. `TicketListItem` (API_SPEC.md §3)
 * deliberately excludes the summary text itself to keep list payloads
 * light — only `ai_summary_status` is available here, so this renders that
 * status rather than a fabricated excerpt (WIREFRAMES.md §3 open decision:
 * widen the schema or truncate server-side; until that's decided, this is
 * the honest middle ground). The full summary text lives in AISummaryPanel
 * inside the drawer, once the detail fetch resolves.
 */
export function AiSummaryStatusChip({ status }: { status: AISummaryStatus }) {
  return <Badge label={LABEL[status]} color={status === "COMPLETED" ? "ai-accent" : "muted"} icon={Sparkles} />;
}

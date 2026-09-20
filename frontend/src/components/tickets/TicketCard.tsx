import { Card } from "../ui/Card";
import { formatRelativeTime, formatAbsoluteTime } from "../../lib/format";
import type { TicketListItem } from "../../types/ticket";
import { AiSummaryStatusChip } from "./AiSummaryStatusChip";
import { PriorityBadge } from "./PriorityBadge";
import { SourceBadge } from "./SourceBadge";
import { StatusBadge } from "./StatusBadge";

/** Mobile fallback for TicketTable rows below the `md` breakpoint (DESIGN_SYSTEM.md §12, §16). */
export function TicketCard({ ticket, onClick }: { ticket: TicketListItem; onClick: () => void }) {
  return (
    <Card
      clickable
      padding="sm"
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick();
        }
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-sm text-foreground">{ticket.ticket_number}</span>
        <PriorityBadge priority={ticket.priority} />
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-sm text-muted-foreground">
        <span className="truncate">{ticket.caller_name}</span>
        <span className="shrink-0">{ticket.category.name}</span>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          <SourceBadge source={ticket.source} />
          <StatusBadge status={ticket.status} />
        </div>
        <AiSummaryStatusChip status={ticket.ai_summary_status} />
      </div>
      <div className="mt-2 text-xs text-muted-foreground" title={formatAbsoluteTime(ticket.created_at)}>
        {formatRelativeTime(ticket.created_at)}
      </div>
    </Card>
  );
}

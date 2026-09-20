import type { ReactNode } from "react";
import type { ColumnDef } from "../ui/DataTable";
import { DataTable } from "../ui/DataTable";
import { LoadingState } from "../ui/LoadingState";
import { formatAbsoluteTime, formatRelativeTime } from "../../lib/format";
import { deriveCallOutcome, formatCallDuration } from "../../lib/callOutcome";
import type { VoiceCallListItem } from "../../types/voiceCall";
import { PriorityBadge } from "../tickets/PriorityBadge";
import { CallCard } from "./CallCard";
import { CallStateBadge } from "./CallStateBadge";
import { EscalationReasonBadge } from "./EscalationReasonBadge";

export interface CallTableProps {
  calls: VoiceCallListItem[];
  isLoading: boolean;
  onRowClick: (call: VoiceCallListItem) => void;
  emptyState: ReactNode;
}

const COLUMNS: ColumnDef<VoiceCallListItem>[] = [
  { id: "caller", header: "Caller", render: (c) => c.caller_name ?? c.from_number },
  {
    id: "duration",
    header: "Duration",
    render: (c) => (
      <span title={formatAbsoluteTime(c.created_at)} className="text-muted-foreground">
        {formatCallDuration(c.created_at, c.ended_at)}
      </span>
    ),
  },
  // WIREFRAMES.md §5 calls this column "Issue"; the list endpoint only
  // denormalizes `category`, not a free-text description, so this shows
  // category rather than fabricating an issue summary the API doesn't return.
  { id: "category", header: "Category", render: (c) => c.category ?? "Uncategorized" },
  { id: "priority", header: "Priority", render: (c) => (c.priority ? <PriorityBadge priority={c.priority} /> : "—") },
  { id: "state", header: "State", render: (c) => <CallStateBadge state={c.state} /> },
  {
    id: "escalation",
    header: "Escalation",
    render: (c) => (c.escalation_reason ? <EscalationReasonBadge reason={c.escalation_reason} /> : "—"),
  },
  { id: "outcome", header: "Outcome", render: (c) => deriveCallOutcome(c) },
  {
    id: "created_at",
    header: "Started",
    render: (c) => (
      <span title={formatAbsoluteTime(c.created_at)} className="text-muted-foreground">
        {formatRelativeTime(c.created_at)}
      </span>
    ),
  },
];

/** Voice Operations Center call table (WIREFRAMES.md §5). No audio player,
 * playback control, or recording UI anywhere — recording is off by design
 * (TWILIO_ARCHITECTURE.md §9). */
export function CallTable({ calls, isLoading, onRowClick, emptyState }: CallTableProps) {
  if (isLoading) {
    return <LoadingState variant="table-rows" count={8} />;
  }

  return (
    <>
      <div className="hidden md:block">
        <DataTable columns={COLUMNS} rows={calls} rowKey={(c) => c.id} onRowClick={onRowClick} emptyState={emptyState} />
      </div>

      <div className="md:hidden">
        {calls.length === 0 ? (
          <div className="rounded-md border border-table-border bg-card">{emptyState}</div>
        ) : (
          <div className="flex flex-col gap-3">
            {calls.map((call) => (
              <CallCard key={call.id} call={call} onClick={() => onRowClick(call)} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}

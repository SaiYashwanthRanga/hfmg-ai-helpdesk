import type { ReactNode } from "react";
import type { ColumnDef } from "../ui/DataTable";
import { DataTable } from "../ui/DataTable";
import { LoadingState } from "../ui/LoadingState";
import { formatAbsoluteTime, formatRelativeTime } from "../../lib/format";
import type { TicketListItem } from "../../types/ticket";
import { AiSummaryStatusChip } from "./AiSummaryStatusChip";
import { PriorityBadge } from "./PriorityBadge";
import { SourceBadge } from "./SourceBadge";
import { StatusBadge } from "./StatusBadge";
import { TicketCard } from "./TicketCard";

export interface TicketTableProps {
  tickets: TicketListItem[];
  isLoading: boolean;
  onRowClick: (ticket: TicketListItem) => void;
  emptyState: ReactNode;
}

const COLUMNS: ColumnDef<TicketListItem>[] = [
  { id: "ticket_number", header: "Ticket #", render: (t) => <span className="font-mono">{t.ticket_number}</span> },
  { id: "caller_name", header: "Caller", render: (t) => t.caller_name },
  { id: "category", header: "Category", render: (t) => t.category.name },
  { id: "ai_summary", header: "AI Summary", render: (t) => <AiSummaryStatusChip status={t.ai_summary_status} /> },
  { id: "source", header: "Source", render: (t) => <SourceBadge source={t.source} /> },
  { id: "priority", header: "Priority", render: (t) => <PriorityBadge priority={t.priority} /> },
  { id: "status", header: "Status", render: (t) => <StatusBadge status={t.status} /> },
  {
    id: "created_at",
    header: "Created",
    render: (t) => (
      <span title={formatAbsoluteTime(t.created_at)} className="text-muted-foreground">
        {formatRelativeTime(t.created_at)}
      </span>
    ),
  },
];

/**
 * Ticket Operations table (WIREFRAMES.md §3). Desktop/tablet render the
 * DataTable (horizontally scrollable below `xl`); mobile swaps to a
 * TicketCard stack (DESIGN_SYSTEM.md §16).
 */
export function TicketTable({ tickets, isLoading, onRowClick, emptyState }: TicketTableProps) {
  if (isLoading) {
    return <LoadingState variant="table-rows" count={8} />;
  }

  return (
    <>
      <div className="hidden md:block">
        <DataTable
          columns={COLUMNS}
          rows={tickets}
          rowKey={(t) => t.id}
          onRowClick={onRowClick}
          emptyState={emptyState}
        />
      </div>

      <div className="md:hidden">
        {tickets.length === 0 ? (
          <div className="rounded-md border border-table-border bg-card">{emptyState}</div>
        ) : (
          <div className="flex flex-col gap-3">
            {tickets.map((ticket) => (
              <TicketCard key={ticket.id} ticket={ticket} onClick={() => onRowClick(ticket)} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}

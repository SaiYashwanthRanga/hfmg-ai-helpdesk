import { useRegenerateSummaryMutation, useTicketQuery } from "../../api/tickets";
import { ApiError } from "../../api/client";
import { Drawer } from "../ui/Drawer";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";
import { formatAbsoluteTime } from "../../lib/format";
import { AISummaryPanel } from "./AISummaryPanel";
import { CallerInfoPanel } from "./CallerInfoPanel";
import { PriorityBadge } from "./PriorityBadge";
import { SourceBadge } from "./SourceBadge";
import { TicketStatusControl } from "./TicketStatusControl";
import { TicketTimeline } from "./TicketTimeline";

export interface TicketDrawerProps {
  ticketId: string | null;
  onClose: () => void;
}

/**
 * Ticket Intelligence Drawer (WIREFRAMES.md §4) — replaces the former
 * full-page TicketDetailPage.tsx. Opened/closed via the `?ticket=` URL
 * param on TicketsPage, so the underlying list's scroll position and
 * applied filters are untouched when it closes.
 */
export function TicketDrawer({ ticketId, onClose }: TicketDrawerProps) {
  const { data: ticket, isLoading, isError, error, refetch } = useTicketQuery(ticketId);
  const regenerateSummary = useRegenerateSummaryMutation(ticketId ?? "");

  return (
    <Drawer open={ticketId !== null} onClose={onClose} title={ticket?.ticket_number ?? "Ticket"}>
      {isLoading ? (
        <div className="p-6">
          <LoadingState variant="page" />
        </div>
      ) : isError ? (
        <div className="p-6">
          <ErrorState
            severity="degraded"
            title="Couldn't load this ticket"
            description={error instanceof ApiError ? error.message : "Something went wrong. Please try again."}
            retry={() => refetch()}
          />
        </div>
      ) : ticket ? (
        <div className="flex flex-col">
          <section className="flex flex-wrap items-center gap-2 border-b border-border px-6 py-4">
            <PriorityBadge priority={ticket.priority} />
            <SourceBadge source={ticket.source} />
          </section>

          <CallerInfoPanel callerName={ticket.caller_name} phoneNumber={ticket.phone_number} email={ticket.email} />

          <section className="flex flex-col gap-2 border-b border-border px-6 py-4">
            <h3 className="text-sm font-semibold text-foreground">Issue Information</h3>
            <dl className="grid grid-cols-[120px_1fr] gap-y-1.5 text-sm">
              <dt className="text-muted-foreground">Category</dt>
              <dd className="text-foreground">{ticket.category.name}</dd>
              <dt className="text-muted-foreground">Created</dt>
              <dd className="text-foreground">{formatAbsoluteTime(ticket.created_at)}</dd>
            </dl>
          </section>

          <section className="border-b border-border px-6 py-4">
            <h3 className="mb-2 text-sm font-semibold text-foreground">Description</h3>
            <p className="text-sm whitespace-pre-wrap text-foreground">{ticket.description}</p>
          </section>

          <AISummaryPanel
            summary={ticket.ai_summary}
            status={ticket.ai_summary_status}
            onRegenerate={() => regenerateSummary.mutate()}
            isRegenerating={regenerateSummary.isPending}
          />

          <TicketTimeline />

          <TicketStatusControl ticketId={ticket.id} status={ticket.status} />

          <section className="px-6 py-4 text-xs text-muted-foreground">
            AI Analysis — Model: not recorded (ai_model is not persisted in the current schema)
          </section>
        </div>
      ) : null}
    </Drawer>
  );
}

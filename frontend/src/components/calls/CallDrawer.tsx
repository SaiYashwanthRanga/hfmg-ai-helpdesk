import { Link } from "react-router-dom";
import { useVoiceCallQuery } from "../../api/voiceCalls";
import { ApiError } from "../../api/client";
import { Drawer } from "../ui/Drawer";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";
import { formatAbsoluteTime } from "../../lib/format";
import { deriveCallOutcome, formatCallDuration } from "../../lib/callOutcome";
import { PriorityBadge } from "../tickets/PriorityBadge";
import { CallStateBadge } from "./CallStateBadge";
import { CallTimeline } from "./CallTimeline";
import { EscalationReasonBadge } from "./EscalationReasonBadge";
import { TranscriptViewer } from "./TranscriptViewer";

export interface CallDrawerProps {
  callId: string | null;
  onClose: () => void;
}

/**
 * Call detail slide-over (WIREFRAMES.md §5/§9's Ticket-Drawer-equivalent
 * for calls). Opened/closed via the `?call=` URL param on CallsPage, same
 * pattern as TicketDrawer's `?ticket=`. No audio player anywhere — text
 * transcript only (TWILIO_ARCHITECTURE.md §9).
 */
export function CallDrawer({ callId, onClose }: CallDrawerProps) {
  const { data: call, isLoading, isError, error, refetch } = useVoiceCallQuery(callId);

  return (
    <Drawer open={callId !== null} onClose={onClose} title={call?.caller_name ?? call?.from_number ?? "Call"}>
      {isLoading ? (
        <div className="p-6">
          <LoadingState variant="page" />
        </div>
      ) : isError ? (
        <div className="p-6">
          <ErrorState
            severity="degraded"
            title="Couldn't load this call"
            description={error instanceof ApiError ? error.message : "Something went wrong. Please try again."}
            retry={() => refetch()}
          />
        </div>
      ) : call ? (
        <div className="flex flex-col">
          <section className="flex flex-wrap items-center gap-2 border-b border-border px-6 py-4">
            <CallStateBadge state={call.state} />
            {call.priority ? <PriorityBadge priority={call.priority} /> : null}
            {call.escalation_reason ? <EscalationReasonBadge reason={call.escalation_reason} /> : null}
          </section>

          <section className="flex flex-col gap-2 border-b border-border px-6 py-4">
            <h3 className="text-sm font-semibold text-foreground">Caller Information</h3>
            <dl className="grid grid-cols-[120px_1fr] gap-y-1.5 text-sm">
              <dt className="text-muted-foreground">Caller</dt>
              <dd className="text-foreground">{call.caller_name ?? "Unknown"}</dd>
              <dt className="text-muted-foreground">From</dt>
              <dd className="text-foreground">{call.from_number}</dd>
              <dt className="text-muted-foreground">To</dt>
              <dd className="text-foreground">{call.to_number}</dd>
            </dl>
          </section>

          <section className="flex flex-col gap-2 border-b border-border px-6 py-4">
            <h3 className="text-sm font-semibold text-foreground">Call Information</h3>
            <dl className="grid grid-cols-[120px_1fr] gap-y-1.5 text-sm">
              <dt className="text-muted-foreground">Category</dt>
              <dd className="text-foreground">{call.category ?? "Uncategorized"}</dd>
              <dt className="text-muted-foreground">Started</dt>
              <dd className="text-foreground">{formatAbsoluteTime(call.created_at)}</dd>
              <dt className="text-muted-foreground">Duration</dt>
              <dd className="text-foreground">{formatCallDuration(call.created_at, call.ended_at)}</dd>
              <dt className="text-muted-foreground">Outcome</dt>
              <dd className="text-foreground">{deriveCallOutcome(call)}</dd>
              <dt className="text-muted-foreground">Misunderstandings</dt>
              <dd className="text-foreground">{call.misunderstanding_count} / 3</dd>
            </dl>
            {call.ticket_id ? (
              <Link to={`/tickets?ticket=${call.ticket_id}`} className="text-sm font-medium text-primary hover:underline">
                View linked ticket →
              </Link>
            ) : null}
          </section>

          <section className="border-b border-border px-6 py-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">Transcript</h3>
            <TranscriptViewer turns={call.turns} />
          </section>

          <CallTimeline />
        </div>
      ) : null}
    </Drawer>
  );
}

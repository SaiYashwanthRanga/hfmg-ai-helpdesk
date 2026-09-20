import { Card } from "../ui/Card";
import { formatAbsoluteTime, formatRelativeTime } from "../../lib/format";
import { deriveCallOutcome, formatCallDuration } from "../../lib/callOutcome";
import type { VoiceCallListItem } from "../../types/voiceCall";
import { CallStateBadge } from "./CallStateBadge";

/** Mobile fallback for CallTable rows below the `md` breakpoint (mirrors TicketCard). */
export function CallCard({ call, onClick }: { call: VoiceCallListItem; onClick: () => void }) {
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
        <span className="text-sm text-foreground">{call.caller_name ?? call.from_number}</span>
        <CallStateBadge state={call.state} />
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-sm text-muted-foreground">
        <span>{call.category ?? "Uncategorized"}</span>
        <span>{formatCallDuration(call.created_at, call.ended_at)}</span>
      </div>
      <div className="mt-2 text-xs text-muted-foreground">{deriveCallOutcome(call)}</div>
      <div className="mt-1 text-xs text-muted-foreground" title={formatAbsoluteTime(call.created_at)}>
        {formatRelativeTime(call.created_at)}
      </div>
    </Card>
  );
}

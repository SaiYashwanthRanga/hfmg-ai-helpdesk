import { useVoiceCallSummaryQuery } from "../../api/voiceCalls";
import { Card } from "../ui/Card";
import { LoadingState } from "../ui/LoadingState";

/** Active/Completed/Escalated counts (WIREFRAMES.md §5) — GET /voice-calls/summary, real. */
export function CallStatsPanel() {
  const { data, isLoading } = useVoiceCallSummaryQuery();

  if (isLoading || !data) {
    return (
      <div className="grid grid-cols-3 gap-4">
        <LoadingState variant="card" />
        <LoadingState variant="card" />
        <LoadingState variant="card" />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-3 gap-4">
      <Card padding="sm">
        <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Active Calls</p>
        <p className="mt-1 text-3xl font-bold tabular-nums text-foreground">{data.active}</p>
      </Card>
      <Card padding="sm">
        <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Completed Calls</p>
        <p className="mt-1 text-3xl font-bold tabular-nums text-foreground">{data.completed}</p>
      </Card>
      <Card padding="sm">
        <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Escalated Calls</p>
        <p className="mt-1 text-3xl font-bold tabular-nums text-foreground">{data.escalated}</p>
      </Card>
    </div>
  );
}

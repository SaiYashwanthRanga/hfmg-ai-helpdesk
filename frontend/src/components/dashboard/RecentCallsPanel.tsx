import { Phone } from "lucide-react";
import { Link } from "react-router-dom";
import { useVoiceCallsQuery } from "../../api/voiceCalls";
import { CallStateBadge } from "../calls/CallStateBadge";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";
import { formatRelativeTime } from "../../lib/format";

/** Latest 10 calls (DESIGN.md §6.3) — GET /voice-calls?page_size=10, real since Backend Tier 2. */
export function RecentCallsPanel() {
  const { data, isLoading, isError, refetch } = useVoiceCallsQuery({ page: 1, page_size: 10 });

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">Recent Calls</h2>
        <Link to="/calls" className="text-xs font-medium text-primary hover:underline">
          View all
        </Link>
      </div>

      {isLoading ? (
        <LoadingState variant="table-rows" count={5} />
      ) : isError ? (
        <ErrorState severity="degraded" title="Couldn't load calls" description="Try again shortly." retry={() => refetch()} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState icon={Phone} title="No calls yet" description="Calls will appear here once the Twilio number receives calls." />
      ) : (
        <ul className="flex flex-col gap-3">
          {data.items.map((call) => (
            <li key={call.id}>
              <Link
                to={`/calls?call=${call.id}`}
                className="flex items-center justify-between gap-3 rounded-sm p-1 hover:bg-card-hover"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-foreground">{call.caller_name ?? call.from_number}</p>
                  <p className="text-xs text-muted-foreground">{formatRelativeTime(call.created_at)}</p>
                </div>
                <CallStateBadge state={call.state} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

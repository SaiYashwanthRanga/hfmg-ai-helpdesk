import { ArrowRight, Phone } from "lucide-react";
import { Link } from "react-router-dom";
import { useVoiceCallsQuery } from "../../api/voiceCalls";
import { CallStateBadge } from "../calls/CallStateBadge";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { LoadingState } from "../ui/LoadingState";
import { ErrorState } from "../ui/ErrorState";
import { formatRelativeTime } from "../../lib/format";

export function RecentCallsPanel() {
  const { data, isLoading, isError, refetch } = useVoiceCallsQuery({ page: 1, page_size: 10 });

  const calls = data?.items ?? [];

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Phone className="size-4 text-emerald-800" />
          <h2 className="text-sm font-semibold text-stone-900">Recent Calls</h2>
        </div>

        <Link
          to="/calls"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-emerald-800 transition-colors hover:bg-emerald-50 hover:text-emerald-900"
        >
          <span>View all</span>
          <ArrowRight className="size-3" aria-hidden="true" />
        </Link>
      </div>

      {isLoading ? (
        <LoadingState variant="table-rows" count={4} />
      ) : isError ? (
        <ErrorState
          severity="degraded"
          title="Couldn't load calls"
          description="The voice calls endpoint did not respond. Check backend server."
          retry={() => refetch()}
        />
      ) : calls.length === 0 ? (
        <EmptyState icon={Phone} title="No voice calls in database" description="Voice calls will appear here once handled by the voice agent." />
      ) : (
        <ul className="flex flex-col gap-2.5">
          {calls.slice(0, 5).map((call) => (
            <li key={call.id}>
              <Link
                to={`/calls?call=${call.id}`}
                className="flex items-center justify-between gap-3 rounded-lg border border-stone-200/80 bg-stone-50/60 p-3 shadow-2xs transition-colors hover:border-stone-300 hover:bg-stone-100/70"
              >
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-stone-900">
                    {call.caller_name ?? call.from_number}
                  </p>
                  <p className="mt-0.5 truncate text-[11px] text-stone-500">
                    {call.category ?? call.from_number} • {formatRelativeTime(call.created_at)}
                  </p>
                </div>

                <div className="shrink-0">
                  <CallStateBadge state={call.state} />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}


import { Activity, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { useRecentActivityQuery } from "../../api/analytics";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { LoadingState } from "../ui/LoadingState";
import { Button } from "../ui/Button";
import { formatRelativeTime } from "../../lib/format";

export function ActivityFeed() {
  const { data, isLoading, isError, refetch } = useRecentActivityQuery(10);

  const activities = data?.items ?? [];

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="size-4 text-emerald-800" />
          <h2 className="text-sm font-semibold text-stone-900">Recent Activity</h2>
        </div>
        <span className="text-xs text-stone-400">Live feed</span>
      </div>

      {isLoading ? (
        <LoadingState variant="table-rows" count={4} />
      ) : isError ? (
        <div className="flex flex-col items-center justify-center py-6 text-center text-xs text-stone-500">
          <p className="text-rose-700 font-medium">Failed to load recent activity from database</p>
          <Button variant="ghost" onClick={() => refetch()} className="mt-2 h-7 px-2.5 gap-1.5 text-xs text-stone-600 hover:bg-stone-100 hover:text-stone-900">
            <RefreshCw className="size-3" />
            Retry
          </Button>
        </div>
      ) : activities.length === 0 ? (
        <EmptyState icon={Activity} title="No recent activity in database" description="Audit log and ticket events will appear here in real time." />
      ) : (
        <ul className="flex flex-col gap-2.5">
          {activities.slice(0, 5).map((item) => (
            <li key={item.ticket_id}>
              <Link
                to={`/tickets?ticket=${item.ticket_id}`}
                className="flex items-center justify-between gap-3 rounded-lg border border-stone-200/80 bg-stone-50/60 p-3 shadow-2xs transition-colors hover:border-stone-300 hover:bg-stone-100/70"
              >
                <div className="min-w-0">
                  <p className="truncate text-xs text-stone-900">
                    <span className="font-mono font-semibold text-stone-900">{item.ticket_number}</span>{" "}
                    created by <span className="font-medium text-stone-800">{item.caller_name || "Unknown"}</span>
                  </p>
                  <p className="mt-0.5 truncate text-[11px] text-stone-500">
                    {item.category || "General"}
                  </p>
                </div>

                <span className="shrink-0 text-[11px] text-stone-400">
                  {formatRelativeTime(item.occurred_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}



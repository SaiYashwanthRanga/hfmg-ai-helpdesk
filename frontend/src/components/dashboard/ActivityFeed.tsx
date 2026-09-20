import { Activity, Ticket as TicketIcon } from "lucide-react";
import { Link } from "react-router-dom";
import { useRecentActivityQuery } from "../../api/analytics";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";
import { formatRelativeTime } from "../../lib/format";

/**
 * Merged ticket+call activity feed (DESIGN.md §6.3). Ticket-created events
 * only today — GET /analytics/recent-activity doesn't merge in call events
 * yet (a documented gap, not a bug — see FRONTEND_GAP_REPORT.md).
 */
export function ActivityFeed() {
  const { data, isLoading, isError, refetch } = useRecentActivityQuery(15);

  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-foreground">Recent Activity</h2>

      {isLoading ? (
        <LoadingState variant="table-rows" count={5} />
      ) : isError ? (
        <ErrorState severity="degraded" title="Couldn't load activity" description="Try again shortly." retry={() => refetch()} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState icon={Activity} title="No recent activity" />
      ) : (
        <ul className="flex flex-col gap-3">
          {data.items.map((item) => (
            <li key={item.ticket_id}>
              <Link to={`/tickets?ticket=${item.ticket_id}`} className="flex items-start gap-2 rounded-sm p-1 hover:bg-card-hover">
                <TicketIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="truncate text-sm text-foreground">
                    <span className="font-mono">{item.ticket_number}</span> created · {item.caller_name} · {item.category}
                  </p>
                  <p className="text-xs text-muted-foreground">{formatRelativeTime(item.occurred_at)}</p>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

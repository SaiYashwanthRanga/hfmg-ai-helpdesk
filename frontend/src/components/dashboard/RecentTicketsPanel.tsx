import { useQuery } from "@tanstack/react-query";
import { Inbox } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { PriorityBadge } from "../tickets/PriorityBadge";
import { StatusBadge } from "../tickets/StatusBadge";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";
import { formatRelativeTime } from "../../lib/format";

/** Latest 10 tickets (DESIGN.md §6.3) — GET /tickets?page_size=10, real data, no new endpoint needed. */
export function RecentTicketsPanel() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["tickets", { page: 1, page_size: 10 }],
    queryFn: () => api.listTickets({ page: 1, page_size: 10 }),
  });

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">Recent Tickets</h2>
        <Link to="/tickets" className="text-xs font-medium text-primary hover:underline">
          View all
        </Link>
      </div>

      {isLoading ? (
        <LoadingState variant="table-rows" count={5} />
      ) : isError ? (
        <ErrorState severity="degraded" title="Couldn't load tickets" description="Try again shortly." retry={() => refetch()} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState icon={Inbox} title="No tickets yet" description="Tickets will appear here." />
      ) : (
        <ul className="flex flex-col gap-3">
          {data.items.map((ticket) => (
            <li key={ticket.id}>
              <Link
                to={`/tickets?ticket=${ticket.id}`}
                className="flex items-center justify-between gap-3 rounded-sm p-1 hover:bg-card-hover"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-foreground">
                    <span className="font-mono">{ticket.ticket_number}</span> · {ticket.caller_name}
                  </p>
                  <p className="text-xs text-muted-foreground">{formatRelativeTime(ticket.created_at)}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <PriorityBadge priority={ticket.priority} />
                  <StatusBadge status={ticket.status} />
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

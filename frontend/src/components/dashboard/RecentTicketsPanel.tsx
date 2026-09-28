import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Inbox, Ticket } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { PriorityBadge } from "../tickets/PriorityBadge";
import { StatusBadge } from "../tickets/StatusBadge";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/EmptyState";
import { LoadingState } from "../ui/LoadingState";
import { formatRelativeTime } from "../../lib/format";
import { ErrorState } from "../ui/ErrorState";

export function RecentTicketsPanel() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["tickets", { page: 1, page_size: 10 }],
    queryFn: () => api.listTickets({ page: 1, page_size: 10 }),
  });

  const tickets = data?.items ?? [];

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Ticket className="size-4 text-emerald-800" />
          <h2 className="text-sm font-semibold text-stone-900">Recent Tickets</h2>
        </div>

        <Link
          to="/tickets"
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
          title="Couldn't load tickets"
          description="The database endpoint did not respond. Check backend server."
          retry={() => refetch()}
        />
      ) : tickets.length === 0 ? (
        <EmptyState icon={Inbox} title="No tickets in database" description="New tickets will appear here once submitted." />
      ) : (
        <ul className="flex flex-col gap-2.5">
          {tickets.slice(0, 5).map((ticket) => (
            <li key={ticket.id}>
              <Link
                to={`/tickets?ticket=${ticket.id}`}
                className="flex items-center justify-between gap-3 rounded-lg border border-stone-200/80 bg-stone-50/60 p-3 shadow-2xs transition-colors hover:border-stone-300 hover:bg-stone-100/70"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold text-stone-900">
                      {ticket.ticket_number}
                    </span>
                    <span className="text-xs text-stone-300">•</span>
                    <p className="truncate text-xs font-medium text-stone-800">
                      {ticket.caller_name}
                    </p>
                  </div>
                  <p className="mt-0.5 truncate text-[11px] text-stone-500">
                    {ticket.category?.name ?? "Hardware"} • {formatRelativeTime(ticket.created_at)}
                  </p>
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


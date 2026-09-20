import { Inbox, SearchX } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useCategoriesQuery, useTicketsQuery } from "../api/tickets";
import { ApiError } from "../api/client";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Pagination } from "../components/ui/Pagination";
import { TicketDrawer } from "../components/tickets/TicketDrawer";
import { TicketFilters, type TicketFilterState } from "../components/tickets/TicketFilters";
import { TicketSearch } from "../components/tickets/TicketSearch";
import { TicketTable } from "../components/tickets/TicketTable";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import type { Priority, TicketListItem, TicketSource, TicketStatus } from "../types/ticket";

const PAGE_SIZE = 25;

/**
 * Ticket Operations Screen (WIREFRAMES.md §3). Filters, page, search, and
 * the open ticket are all URL state, so the view is shareable/refresh-safe
 * and the drawer never causes a route navigation
 * (FRONTEND_IMPLEMENTATION_PLAN.md Phase 3 acceptance criteria).
 *
 * Search/Priority/Source were enabled during the final review pass —
 * Backend Tier 0 made all three real; see FRONTEND_GAP_REPORT.md's Phase 3
 * revalidation entry for the live verification that preceded this change.
 */
export function TicketsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const page = Number(searchParams.get("page") ?? "1");
  const status = (searchParams.get("status") ?? "") as TicketStatus | "";
  const categoryId = searchParams.get("category") ?? "";
  const priority = (searchParams.get("priority") ?? "") as Priority | "";
  const source = (searchParams.get("source") ?? "") as TicketSource | "";
  const q = searchParams.get("q") ?? "";
  const openTicketId = searchParams.get("ticket");

  // Local, instant input state — the URL/query only update after the
  // debounce settles, so typing doesn't fire a request or a history entry
  // per keystroke.
  const [searchInput, setSearchInput] = useState(q);
  const debouncedSearch = useDebouncedValue(searchInput, 300);

  useEffect(() => {
    if (debouncedSearch === q) return;
    updateParams({ q: debouncedSearch, page: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const hasActiveFilter = status !== "" || categoryId !== "" || priority !== "" || source !== "" || q !== "";

  const categoriesQuery = useCategoriesQuery();
  const ticketsQuery = useTicketsQuery({ page, status, categoryId, priority, source, q });

  function updateParams(patch: Record<string, string | null>) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(patch)) {
        if (value === null || value === "") next.delete(key);
        else next.set(key, value);
      }
      return next;
    });
  }

  function handleFilterChange(next: TicketFilterState) {
    updateParams({ status: next.status, category: next.categoryId, priority: next.priority, source: next.source, page: null });
  }

  function clearFilters() {
    setSearchInput("");
    updateParams({ status: null, category: null, priority: null, source: null, q: null, page: null });
  }

  function openTicket(ticket: TicketListItem) {
    updateParams({ ticket: ticket.id });
  }

  function closeTicket() {
    updateParams({ ticket: null });
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-foreground">Tickets</h1>
        <Button variant="primary" onClick={() => navigate("/tickets/new")}>
          + New Ticket
        </Button>
      </div>

      <div className="flex flex-col gap-3">
        <TicketSearch value={searchInput} onChange={setSearchInput} />
        <TicketFilters
          value={{ status, categoryId, priority, source }}
          onChange={handleFilterChange}
          categories={categoriesQuery.data ?? []}
        />
      </div>

      {ticketsQuery.isError ? (
        <ErrorState
          severity="degraded"
          title="Couldn't load tickets"
          description={
            ticketsQuery.error instanceof ApiError ? ticketsQuery.error.message : "Something went wrong. Please try again."
          }
          retry={() => ticketsQuery.refetch()}
        />
      ) : (
        <>
          <TicketTable
            tickets={ticketsQuery.data?.items ?? []}
            isLoading={ticketsQuery.isLoading}
            onRowClick={openTicket}
            emptyState={
              hasActiveFilter ? (
                <EmptyState
                  icon={SearchX}
                  title="No tickets match these filters"
                  description="Try a different status, category, priority, source, or search term."
                  action={{ label: "Clear filters", onClick: clearFilters }}
                />
              ) : (
                <EmptyState
                  icon={Inbox}
                  title="No tickets yet"
                  description="Tickets from the web form or voice agent will appear here."
                  action={{ label: "+ New Ticket", onClick: () => navigate("/tickets/new") }}
                />
              )
            }
          />

          {ticketsQuery.data && ticketsQuery.data.total > 0 ? (
            <Pagination
              page={ticketsQuery.data.page}
              pageSize={PAGE_SIZE}
              total={ticketsQuery.data.total}
              onPageChange={(nextPage) => updateParams({ page: String(nextPage) })}
            />
          ) : null}
        </>
      )}

      <TicketDrawer ticketId={openTicketId} onClose={closeTicket} />
    </div>
  );
}

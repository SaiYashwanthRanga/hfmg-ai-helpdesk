import { useMemo, useState } from "react";
import { ageLabel, assigneeOptions, AI_PRIORITIES, hasFilters, NO_FILTERS, visibleTickets } from "../../lib/ticketReport/view";
import type { TicketFilters } from "../../lib/ticketReport/view";
import type { Analysis } from "../../types/ticketReport";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Select } from "../ui/Select";
import { AiPriorityBadge, AiTag } from "./AiPriorityBadge";

/**
 * What to work on next. By default only the short list (every Critical and High ticket and a few Medium ones),
 * in the order the server stored them; "Show all" opens every open ticket with filters.
 */
export function ActionItems({ analysis }: { analysis: Analysis }) {
  const [showAll, setShowAll] = useState(false);
  const [filters, setFilters] = useState<TicketFilters>(NO_FILTERS);
  const hasAssignee = analysis.summary_fields.includes("assignee");
  const people = useMemo(() => assigneeOptions(analysis.tickets), [analysis.tickets]);
  const shown = useMemo(() => visibleTickets(analysis.tickets, analysis.action_ids, { showAll, filters }), [analysis, showAll, filters]);
  const set = (patch: Partial<TicketFilters>) => setFilters((prev) => ({ ...prev, ...patch }));
  const total = analysis.tickets.length;
  const s = analysis.summary;
  const noUrgent = s.ai_analyzed > 0 && !s.ai_critical && !s.ai_high;
  const searching = showAll || hasFilters(filters);

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="flex items-center gap-2 text-base font-semibold text-stone-900">Action items <AiTag /></h2>
        <p className="mt-1 text-xs text-stone-500">
          Work these in order. Priorities are AI recommendations for a person to review; the Freshworks priority is unchanged.
        </p>
        {noUrgent ? (
          <p className="mt-2 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-700">
            No ticket was recommended as Critical or High. The list below is based on overdue status, operational impact and other available evidence.
          </p>
        ) : null}
      </div>

      {searching ? (
        <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="flex flex-col gap-1.5 lg:col-span-2">
            <label htmlFor="ticket-search" className="text-sm font-medium text-stone-800">Search</label>
            <input id="ticket-search" value={filters.query} onChange={(e) => set({ query: e.target.value })} placeholder="Ticket ID, subject or person"
              className="h-10 rounded-sm border border-stone-200 bg-white px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800" />
          </div>
          <Select label="AI priority" value={filters.priority} onChange={(e) => set({ priority: e.target.value as TicketFilters["priority"] })}
            options={[{ label: "All", value: "all" }, ...AI_PRIORITIES.map((p) => ({ label: p, value: p })), { label: "No recommendation", value: "none" }]} />
          {hasAssignee ? (
            <Select label="Assigned to" value={filters.assignee} onChange={(e) => set({ assignee: e.target.value })}
              options={[{ label: "Everyone", value: "all" }, { label: "Unassigned", value: "unassigned" }, ...people.map((p) => ({ label: p, value: p }))]} />
          ) : null}
          <label className="flex h-10 items-center gap-2 text-sm text-stone-800">
            <input type="checkbox" checked={filters.overdueOnly} onChange={(e) => set({ overdueOnly: e.target.checked })} className="size-4 accent-emerald-800" />
            Overdue only
          </label>
        </div>
      ) : null}

      {total === 0 ? (
        <p className="py-6 text-center text-sm text-stone-600">There are no open or unresolved tickets in this file.</p>
      ) : shown.length === 0 ? (
        <div className="py-6 text-center text-sm text-stone-600">
          {searching ? "No ticket matches these filters. " : "No ticket has an AI recommendation. "}
          {searching ? <Button variant="ghost" size="xs" onClick={() => setFilters(NO_FILTERS)}>Clear filters</Button> : null}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border border-stone-200">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Open tickets in the order to work them</caption>
            <thead className="bg-stone-50 text-left text-xs text-stone-500">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Ticket</th>
                <th scope="col" className="px-3 py-2 font-medium">Assigned to</th>
                <th scope="col" className="px-3 py-2 font-medium">Subject</th>
                <th scope="col" className="px-3 py-2 font-medium">Freshworks</th>
                <th scope="col" className="px-3 py-2 font-medium">AI recommends</th>
                <th scope="col" className="px-3 py-2 font-medium">Why</th>
                <th scope="col" className="px-3 py-2 font-medium">Next action</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((t) => (
                <tr key={t.ticket_id} className="border-t border-stone-100 align-top">
                  <td className="px-3 py-2 font-medium whitespace-nowrap">
                    #{t.ticket_id}
                    {t.overdue ? <span className="mt-1 block" title={t.age_days !== null ? `${ageLabel(t.age_days)} old` : undefined}><Badge label="Overdue" color="danger" size="sm" /></span> : null}
                  </td>
                  <td className="px-3 py-2">
                    {hasAssignee ? t.assignee ?? <span className="italic text-stone-500">Unassigned</span> : <span className="text-stone-500">Not available</span>}
                  </td>
                  <td className="max-w-xs px-3 py-2 break-words">{t.subject || <span className="text-stone-500">(no subject)</span>}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{t.priority ?? <span className="text-stone-500">Not set</span>}</td>
                  <td className="px-3 py-2">
                    <AiPriorityBadge priority={t.ai.priority} />
                    {t.ai.limited_information ? <span className="mt-1 block text-xs text-stone-500">Limited information</span> : null}
                  </td>
                  <td className="max-w-xs px-3 py-2 break-words text-stone-700">{t.ai.reason ?? <span className="text-stone-500">—</span>}</td>
                  <td className="max-w-xs px-3 py-2 break-words text-stone-700">{t.ai.next_action ?? <span className="text-stone-500">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > 0 ? (
      <div className="flex flex-wrap items-center gap-3 text-xs text-stone-500">
        <span>
          {searching ? `Showing ${shown.length} of ${total} open tickets.` : `Showing the ${shown.length} priority items of ${total} open tickets.`}
        </span>
        {total > analysis.action_ids.length || searching ? (
          <Button variant="secondary" size="xs" onClick={() => { setShowAll((v) => !v); setFilters(NO_FILTERS); }}>
            {searching ? "Show priority items only" : `Show all ${total} open tickets`}
          </Button>
        ) : null}
      </div>
      ) : null}
    </Card>
  );
}

import { useState } from "react";
import type { ReactNode } from "react";
import { daysOpenLabel, FOLLOWUP_ROWS, followupNote, limitRows, vendorStatusLabel } from "../../lib/ticketReport/followup";
import type { Analysis, DailyWorkItem, ProviderItem, VendorItem } from "../../types/ticketReport";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { AiPriorityBadge, AiTag } from "./AiPriorityBadge";

interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
}

const owner = (name: string | null) => (name ? name : <span className="italic text-stone-500">Unassigned</span>);
const text = (value: string | null | undefined) => value || <span className="text-stone-500">—</span>;

/** One group: a heading with its count, how it is decided, and a table (or an empty state) of at most ten rows. */
function Group<T extends { ticket_id: string }>({
  title, explanation, rows, columns, empty,
}: { title: string; explanation: string; rows: T[]; columns: Column<T>[]; empty: string }) {
  const [showAll, setShowAll] = useState(false);
  const shown = limitRows(rows, showAll);
  return (
    <section aria-label={title} className="flex flex-col gap-2">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-stone-900">
          {title} <Badge label={String(rows.length)} color={rows.length ? "primary" : "muted"} size="sm" />
        </h3>
        <p className="text-xs text-stone-500">{explanation}</p>
      </div>
      {rows.length === 0 ? (
        <p className="rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-600">{empty}</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-stone-200">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">{title}</caption>
            <thead className="bg-stone-50 text-left text-xs text-stone-500">
              <tr>{columns.map((c) => <th key={c.header} scope="col" className="px-3 py-2 font-medium">{c.header}</th>)}</tr>
            </thead>
            <tbody>
              {shown.map((row) => (
                <tr key={row.ticket_id} className="border-t border-stone-100 align-top">
                  {columns.map((c) => <td key={c.header} className={`px-3 py-2 break-words ${c.className ?? ""}`}>{c.cell(row)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > FOLLOWUP_ROWS ? (
        <div>
          <Button variant="secondary" size="xs" onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Show fewer" : `Show all ${rows.length}`}
          </Button>
        </div>
      ) : null}
    </section>
  );
}

const ticketCell = (r: { ticket_id: string }) => <span className="font-medium whitespace-nowrap">#{r.ticket_id}</span>;
const subjectCell = (r: { subject: string }) => r.subject || <span className="text-stone-500">(no subject)</span>;
const daysCell = (r: { days_open: number | null }) => <span className="whitespace-nowrap">{daysOpenLabel(r.days_open)}</span>;

const VENDOR_COLUMNS: Column<VendorItem>[] = [
  { header: "Ticket", cell: ticketCell },
  { header: "Assigned to", cell: (r) => owner(r.assignee) },
  { header: "Issue", cell: subjectCell, className: "max-w-xs" },
  { header: "Days open", cell: daysCell },
  { header: "Status", cell: (r) => <Badge label={vendorStatusLabel(r.status)} color={r.status === "waiting_on_vendor" ? "warning" : "muted"} size="sm" /> },
  { header: "Evidence", cell: (r) => text(r.evidence), className: "max-w-xs" },
  { header: "Next action", cell: (r) => text(r.next_action), className: "max-w-xs" },
];

const PROVIDER_COLUMNS: Column<ProviderItem>[] = [
  { header: "Ticket", cell: ticketCell },
  { header: "Assigned to", cell: (r) => owner(r.assignee) },
  { header: "OM or user issue", cell: (r) => r.issue, className: "max-w-xs" },
  { header: "Days open", cell: daysCell },
  { header: "Business impact", cell: (r) => text(r.impact), className: "max-w-xs" },
  { header: "AI priority", cell: (r) => <AiPriorityBadge priority={r.priority} /> },
  { header: "Next action", cell: (r) => text(r.next_action), className: "max-w-xs" },
];

const DAILY_COLUMNS: Column<DailyWorkItem>[] = [
  { header: "Ticket", cell: (r) => <>{ticketCell(r)}{r.overdue ? <span className="mt-1 block"><Badge label="Overdue" color="danger" size="sm" /></span> : null}</> },
  { header: "Assigned to", cell: (r) => owner(r.assignee) },
  { header: "Issue", cell: subjectCell, className: "max-w-xs" },
  { header: "Days open", cell: daysCell },
  { header: "AI priority", cell: (r) => <AiPriorityBadge priority={r.priority} /> },
  { header: "Freshworks", cell: (r) => r.freshworks_priority ?? <span className="text-stone-500">Not set</span> },
  { header: "Why", cell: (r) => text(r.reason), className: "max-w-xs" },
  { header: "Next action", cell: (r) => text(r.next_action), className: "max-w-xs" },
];

/**
 * The weekly follow-up: what is waiting on a vendor, what affects providers, and what blocks daily work. Dashboard
 * only (the email does not carry it). A ticket can be in more than one group.
 */
export function WeeklyFollowUp({ analysis }: { analysis: Analysis }) {
  const followup = analysis.followup;
  if (!followup) {
    return (
      <Card>
        <h2 className="text-base font-semibold text-stone-900">Weekly follow-up</h2>
        <p className="mt-1 text-sm text-stone-600">This report was made before the weekly follow-up groups existed. Upload the file again and analyze it to see them.</p>
      </Card>
    );
  }
  const note = followupNote(followup);
  return (
    <Card className="flex flex-col gap-5">
      <div>
        <h2 className="flex items-center gap-2 text-base font-semibold text-stone-900">Weekly follow-up <AiTag /></h2>
        <p className="mt-1 text-xs text-stone-500">
          Open tickets to follow up this week, from the uploaded file. These are AI recommendations for a person to review; a
          ticket can appear in more than one group, and nothing is changed in Freshworks.
        </p>
        {note ? <p role="status" className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">{note}</p> : null}
      </div>

      <Group
        title="Waiting on vendor"
        explanation="Tickets that show they are blocked on an outside organization. A ticket that only mentions a vendor is marked Needs review, not Waiting."
        rows={followup.vendor} columns={VENDOR_COLUMNS}
        empty="No ticket shows evidence of waiting on a vendor." />
      <Group
        title="OM or user issues"
        explanation="Issues that affect a provider's ability to work. Mentioning a doctor does not make a ticket urgent: the impact described is what counts."
        rows={followup.provider} columns={PROVIDER_COLUMNS}
        empty="No OM or user issue was found." />
      <Group
        title="Priority issues affecting daily work"
        explanation="Tickets the AI recommended as Critical or High, in the order to work them: overdue first, then the oldest."
        rows={followup.daily_work} columns={DAILY_COLUMNS}
        empty="No ticket was recommended as Critical or High." />
    </Card>
  );
}

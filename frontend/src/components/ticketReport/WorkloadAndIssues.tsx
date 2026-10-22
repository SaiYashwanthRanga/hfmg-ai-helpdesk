import type { Analysis } from "../../types/ticketReport";
import { Card } from "../ui/Card";
import { AiTag } from "./AiPriorityBadge";

/**
 * The main section: who has what. Every open ticket is in exactly one row, so the rows add up to the summary's
 * open total. Tickets with no agent are in the "Unassigned" row, never attributed to a person.
 */
export function EmployeeWorkload({ analysis }: { analysis: Analysis }) {
  const rows = analysis.employees;
  return (
    <Card className="flex flex-col gap-3">
      <div>
        <h2 className="text-base font-semibold text-stone-900">Employee workload</h2>
        <p className="mt-1 text-xs text-stone-500">
          Busiest first: Critical and High tickets, then overdue, then total. Critical, High and Remaining are the AI's recommendations.
        </p>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-stone-600">Not available: the file has no Assigned-to column.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-stone-200">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Open tickets per employee</caption>
            <thead className="bg-stone-50 text-left text-xs text-stone-500">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Employee</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Open tickets</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Overdue</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">AI Critical</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">AI High</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Remaining</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.unassigned ? "unassigned" : e.name} className={`border-t border-stone-100 ${e.unassigned ? "bg-amber-50/40" : ""}`}>
                  <th scope="row" className="px-3 py-2 text-left font-medium">
                    {e.unassigned ? <span className="italic">Unassigned</span> : e.name}
                    {e.unassigned ? <span className="block text-xs font-normal text-stone-500">No agent in the file</span> : null}
                  </th>
                  <td className="px-3 py-2 text-right">{e.total}</td>
                  <td className="px-3 py-2 text-right">{e.overdue}</td>
                  <td className={`px-3 py-2 text-right ${e.critical ? "font-semibold text-rose-800" : ""}`}>{e.critical}</td>
                  <td className={`px-3 py-2 text-right ${e.high ? "font-semibold text-amber-800" : ""}`}>{e.high}</td>
                  <td className="px-3 py-2 text-right text-stone-600">{e.remaining}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/** Optional and collapsed: at most three recurring issues, each with its tickets and one short note. */
export function RecurringIssues({ analysis }: { analysis: Analysis }) {
  const issues = analysis.recurring_issues.slice(0, 3);
  return (
    <details className="rounded-xl border border-stone-200/80 bg-white shadow-sm">
      <summary className="flex cursor-pointer items-center gap-2 px-5 py-3 text-sm font-semibold text-stone-900">
        Recurring issues ({issues.length}) <AiTag />
      </summary>
      <div className="border-t border-stone-200 px-5 py-3">
        {issues.length === 0 ? (
          <p className="text-sm text-stone-600">{analysis.recurring_note || "No recurring issue was found."}</p>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {issues.map((issue) => (
              <li key={issue.title}>
                <span className="font-medium text-stone-900">{issue.title}</span>
                <span className="text-stone-500"> · {issue.ticket_ids.map((id) => `#${id}`).join(", ")}</span>
                {issue.note ? <p className="text-stone-700">{issue.note} <span className="text-stone-500">(a possibility, not established)</span></p> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold text-stone-700">{title}</h3>
      <ul className="mt-0.5 list-disc space-y-0.5 pl-5 text-xs text-stone-700">{items.map((item) => <li key={item}>{item}</li>)}</ul>
    </div>
  );
}

/** Collapsed: what the file could not tell us, and the assumptions behind the numbers. */
export function DataLimitations({ analysis }: { analysis: Analysis }) {
  const q = analysis.data_quality;
  const count = q.warnings.length + q.unavailable.length + q.limitations.length;
  return (
    <details className="rounded-xl border border-stone-200/80 bg-white shadow-sm">
      <summary className="cursor-pointer px-5 py-3 text-sm font-semibold text-stone-900">Data limitations ({count})</summary>
      <div className="flex flex-col gap-3 border-t border-stone-200 px-5 py-3">
        <List title="Found in the file" items={q.warnings} />
        <List title="Not available" items={q.unavailable} />
        <List title="Limitations" items={q.limitations} />
        <List title="Assumptions" items={q.assumptions} />
      </div>
    </details>
  );
}

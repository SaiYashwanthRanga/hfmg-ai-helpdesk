import { CircleAlert, TriangleAlert } from "lucide-react";
import type { TicketReportRecord, ValidationIssue } from "../../types/ticketReport";
import { Badge } from "../ui/Badge";

function Issues({ issues, tone }: { issues: ValidationIssue[]; tone: "error" | "warning" }) {
  if (!issues.length) return null;
  const Icon = tone === "error" ? CircleAlert : TriangleAlert;
  const box = tone === "error" ? "border-rose-200 bg-rose-50 text-rose-900" : "border-amber-200 bg-amber-50 text-amber-900";
  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${box}`} role={tone === "error" ? "alert" : undefined}>
      <p className="flex items-center gap-1.5 font-medium">
        <Icon className="size-4" aria-hidden="true" />
        {tone === "error" ? `${issues.length} problem${issues.length === 1 ? "" : "s"} to fix` : `${issues.length} thing${issues.length === 1 ? "" : "s"} to know`}
      </p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5">
        {issues.map((issue) => <li key={issue.code + issue.message}>{issue.message}</li>)}
      </ul>
    </div>
  );
}

/** What was found in the uploaded file: rows, how each column was read, and what is wrong with it. */
export function ValidationSummary({ report }: { report: TicketReportRecord }) {
  const { validation } = report;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-sm text-stone-700">
        <Badge label={validation.valid ? "File is usable" : "File cannot be analyzed"} color={validation.valid ? "success" : "danger"} />
        <span>{report.source_row_count} row{report.source_row_count === 1 ? "" : "s"} in the file</span>
        {validation.valid ? <span>· {report.row_count} ticket{report.row_count === 1 ? "" : "s"} will be analyzed</span> : null}
      </div>

      <Issues issues={validation.errors} tone="error" />
      <Issues issues={validation.warnings} tone="warning" />

      <details className="rounded-md border border-stone-200 bg-white">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-stone-800">Detected columns ({report.columns.length})</summary>
        <div className="overflow-x-auto border-t border-stone-200">
          <table className="w-full text-sm">
            <thead className="bg-stone-50 text-left text-xs text-stone-500">
              <tr><th className="px-3 py-1.5 font-medium">Column in the file</th><th className="px-3 py-1.5 font-medium">Used by the report as</th></tr>
            </thead>
            <tbody>
              {report.columns.map((column, index) => (
                <tr key={`${column.name}-${index}`} className="border-t border-stone-100">
                  <td className="px-3 py-1.5 break-words">{column.name}</td>
                  <td className="px-3 py-1.5 text-stone-600">
                    {column.used ? column.field?.replace(/_/g, " ") : column.note ?? "not used"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

import { formatCount } from "../../lib/ticketReport/view";
import type { Analysis } from "../../types/ticketReport";
import { Card } from "../ui/Card";

function Stat({ label, value, note, attention }: { label: string; value: string; note?: string; attention?: boolean }) {
  const unavailable = value === "Not available";
  return (
    <div className="rounded-lg border border-stone-200 bg-white p-4">
      <dt className="text-xs font-medium text-stone-500">{label}</dt>
      <dd className={`mt-1 ${unavailable ? "text-sm text-stone-500" : `text-2xl font-semibold ${attention ? "text-rose-800" : "text-stone-900"}`}`}>{value}</dd>
      {note ? <p className="mt-1 text-xs text-stone-500">{note}</p> : null}
    </div>
  );
}

/**
 * The top of the report: six figures and at most three short statements. The counts come from the file, and
 * Critical / High from the AI's validated recommendations; a figure that cannot be calculated says "Not available".
 */
export function ExecutiveSummary({ analysis }: { analysis: Analysis }) {
  const s = analysis.summary;
  const unanalyzed = analysis.ai.tickets_not_analyzed + analysis.ai.tickets_unavailable;
  const aiNote = s.ai_analyzed ? (unanalyzed ? `Of ${s.ai_analyzed} analyzed; ${unanalyzed} not` : `Of ${s.ai_analyzed} analyzed`) : undefined;
  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-base font-semibold text-stone-900">Summary</h2>
        <p className="mt-1 text-xs text-stone-500">As of {new Date(analysis.as_of).toLocaleString()}. Critical and High are AI recommendations for a person to review.</p>
      </div>

      <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Total tickets" value={String(s.total)} />
        <Stat label="Open / unresolved" value={formatCount(s.unresolved)} note={s.unknown_status ? `${s.unknown_status} with a status not read` : undefined} />
        <Stat label="Overdue" value={formatCount(s.overdue)} attention={!!s.overdue} />
        <Stat label="Unassigned" value={formatCount(s.unassigned)} attention={!!s.unassigned} />
        <Stat label="AI Critical" value={formatCount(s.ai_critical)} note={aiNote} attention={!!s.ai_critical} />
        <Stat label="AI High" value={formatCount(s.ai_high)} note={aiNote} attention={!!s.ai_high} />
      </dl>

      {analysis.highlights.length ? (
        <ul className="list-disc space-y-1 pl-5 text-sm text-stone-800">
          {analysis.highlights.slice(0, 3).map((line) => <li key={line}>{line}</li>)}
        </ul>
      ) : null}
    </Card>
  );
}

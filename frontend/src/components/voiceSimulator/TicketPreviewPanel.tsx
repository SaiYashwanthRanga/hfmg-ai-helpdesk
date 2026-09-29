import { CheckCircle2, CircleDashed, ExternalLink, Loader2 } from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import type { Priority } from "../../types/ticket";
import type { SimulatorSession } from "../../types/voiceSimulator";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { PriorityBadge } from "../tickets/PriorityBadge";

const CONFIDENCE: Record<string, { label: string; score: number; color: "success" | "warning" | "danger" }> = {
  high: { label: "High", score: 3, color: "success" },
  medium: { label: "Medium", score: 2, color: "warning" },
  low: { label: "Low", score: 1, color: "danger" },
};

const OUTCOME: Record<string, string> = {
  CONFIRM_SUMMARY: "Reading it back",
  COMPLETED: "Intake completed",
  ESCALATED: "Escalated to a person",
  ABANDONED: "Caller hung up",
};

function Field({ label, value, placeholder = "Not collected yet", multiline }: {
  label: string;
  value: string | null | undefined;
  placeholder?: string;
  multiline?: boolean;
}) {
  const filled = Boolean(value && value.trim());
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-stone-500 uppercase">
        {filled ? (
          <CheckCircle2 className="size-3 text-emerald-600" aria-hidden="true" />
        ) : (
          <CircleDashed className="size-3 text-stone-300" aria-hidden="true" />
        )}
        {label}
        <span className="sr-only">{filled ? "(collected)" : "(not collected)"}</span>
      </dt>
      <dd className={cn("text-sm", filled ? "text-stone-900" : "text-stone-400 italic", multiline && "whitespace-pre-wrap")}>
        {filled ? value : placeholder}
      </dd>
    </div>
  );
}

/**
 * What the agent has understood so far, updated every turn from the
 * orchestrator's collected slots, then the created ticket.
 */
export function TicketPreviewPanel({ session }: { session: SimulatorSession | null }) {
  const collected = session?.collected;
  const confidence = collected?.category_confidence ? CONFIDENCE[collected.category_confidence] : null;
  const ticket = session?.ticket ?? null;
  const priority = (ticket?.priority ?? collected?.priority ?? null) as Priority | null;

  return (
    <Card padding="none" className="flex flex-col">
      <div className="flex items-center justify-between border-b border-stone-100 px-5 py-3">
        <h2 className="text-sm font-semibold text-stone-900">Live ticket</h2>
        {session?.state && OUTCOME[session.state] ? <Badge label={OUTCOME[session.state]} color="muted" size="sm" /> : null}
      </div>
      <dl className="flex flex-col gap-3 p-5" aria-live="polite">
        <Field
          label="Name"
          value={collected?.caller_name ? `${collected.caller_name}${collected.name_confidence === "low" ? " (being checked)" : ""}` : null}
        />
        <Field
          label="Department"
          value={collected?.department ? `${collected.department}${collected.department_verified === false ? " (not on the department list)" : ""}` : null}
        />
        <Field label="Email" value={collected?.email} />
        <Field label="Phone" value={collected?.phone_number} />
        <Field label="Issue" value={collected?.description} multiline />
        <Field label="Started" value={collected?.started} />
        <Field
          label="Work blocked"
          value={
            collected?.work_blocked === true
              ? "Yes — can't work"
              : collected?.work_blocked === false
                ? "No — can still work"
                : null
          }
        />
        <Field label="Category" value={collected?.category} />
        <div className="flex flex-col gap-0.5">
          <dt className="text-[11px] font-semibold tracking-wide text-stone-500 uppercase">Priority</dt>
          <dd className="flex flex-col gap-1">
            {priority ? <PriorityBadge priority={priority} /> : <span className="text-sm text-stone-400 italic">Not set yet</span>}
            {priority && collected?.priority_reason ? (
              <span className="text-xs text-stone-600">Because {collected.priority_reason}</span>
            ) : null}
          </dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="text-[11px] font-semibold tracking-wide text-stone-500 uppercase">Category confidence</dt>
          <dd className="flex items-center gap-2">
            {confidence ? (
              <>
                <Badge label={confidence.label} color={confidence.color} size="sm" />
                <span className="text-xs text-stone-500" aria-hidden="true">
                  {"●".repeat(confidence.score)}
                  {"○".repeat(3 - confidence.score)}
                </span>
                {confidence.score < 3 ? <span className="text-[11px] text-stone-500">Agent will confirm</span> : null}
              </>
            ) : (
              <span className="text-sm text-stone-400 italic">—</span>
            )}
          </dd>
        </div>
        {collected?.impact ? <Field label="Impact" value={collected.impact} /> : null}
      </dl>

      <div className="border-t border-stone-100 p-5">
        {ticket ? (
          <div className="flex flex-col gap-2 rounded-lg border border-emerald-200/80 bg-emerald-50/60 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-sm font-semibold text-emerald-900">{ticket.ticket_number}</span>
              <Badge label={ticket.status} color="primary" size="sm" />
            </div>
            <p className="text-xs text-stone-600">
              {ticket.category} · created as a <strong>SIMULATOR</strong> ticket (hidden from the queue and dashboards)
            </p>
            <p className="flex items-center gap-1.5 text-xs text-stone-600">
              AI summary:{" "}
              {ticket.ai_summary_status === "PENDING" ? (
                <>
                  <Loader2 className="size-3 animate-spin" aria-hidden="true" /> generating…
                </>
              ) : (
                ticket.ai_summary_status.toLowerCase()
              )}
            </p>
            {ticket.ai_summary ? <p className="text-xs text-stone-700">{ticket.ai_summary}</p> : null}
            <Link
              to={`/tickets?source=SIMULATOR&ticket=${ticket.id}`}
              className="inline-flex items-center gap-1 text-xs font-medium text-emerald-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800"
            >
              Open ticket <ExternalLink className="size-3" aria-hidden="true" />
            </Link>
          </div>
        ) : (
          <p className="text-xs text-stone-500">
            {session?.state === "ABANDONED"
              ? "No ticket: the call ended before a problem and callback number were collected."
              : "The ticket is created once the agent has the problem, a name and a way to reach the caller."}
          </p>
        )}
      </div>
    </Card>
  );
}

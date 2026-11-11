import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import type { InsightTicketRef } from "../../types/aiInsights";

export interface TicketChipsProps {
  tickets: InsightTicketRef[];
  tone?: "light" | "dark";
  className?: string;
}

/** The tickets an AI statement is based on; each opens that ticket's drawer. */
export function TicketChips({ tickets, tone = "light", className }: TicketChipsProps) {
  if (tickets.length === 0) return null;
  return (
    <ul className={cn("flex flex-wrap gap-1.5", className)} aria-label="Based on tickets">
      {tickets.map((ticket) => (
        <li key={ticket.id}>
          <Link
            to={`/tickets?ticket=${ticket.id}`}
            className={cn(
              "inline-flex h-6 items-center rounded-md px-1.5 font-mono text-[11px] font-medium tabular-nums transition-colors",
              tone === "dark"
                ? "bg-white/10 text-emerald-50 hover:bg-white/20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
                : "border border-stone-200 bg-stone-50 text-stone-700 hover:border-emerald-800/40 hover:bg-emerald-50 hover:text-emerald-900",
            )}
          >
            {ticket.ticket_number}
          </Link>
        </li>
      ))}
    </ul>
  );
}

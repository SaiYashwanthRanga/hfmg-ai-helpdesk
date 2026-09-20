import type { LucideIcon } from "lucide-react";
import { Lock } from "lucide-react";
import { Card } from "../ui/Card";

export interface BlockedInsightCardProps {
  icon: LucideIcon;
  title: string;
  blockedReason: string;
}

/**
 * Shared shell for every AI Insights section the backend deliberately
 * doesn't build (WIREFRAMES.md §11 open decisions). Renders the server's
 * own `blocked_reason` verbatim — never a fabricated summary, sample data,
 * or placeholder metric standing in for it.
 */
export function BlockedInsightCard({ icon: Icon, title, blockedReason }: BlockedInsightCardProps) {
  return (
    <Card>
      <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
        {title}
      </h2>
      <div className="flex items-start gap-2 rounded-sm bg-card-hover p-3">
        <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <p className="text-sm text-muted-foreground">{blockedReason}</p>
      </div>
    </Card>
  );
}

import { History } from "lucide-react";

/**
 * Activity Timeline drawer section. Permanently blocked, not empty: an
 * `audit_log` table does not exist anywhere in the current schema
 * (backend/app/db/models.py) — it's Phase 3 backend/auth scope per
 * IMPLEMENTATION_PLAN.md, unrelated to this frontend phase. Rendering an
 * empty list here would wrongly imply "no history exists yet" when the
 * truth is "history isn't tracked at all" (COMPONENTS.md TicketTimeline).
 */
export function TicketTimeline() {
  return (
    <section className="border-b border-border px-6 py-4">
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground">
        <History className="size-4 text-muted-foreground" aria-hidden="true" />
        Activity Timeline
      </h3>
      <p className="text-sm text-muted-foreground">
        Activity history isn't tracked yet — this requires an audit log, which is Phase 3 backend scope.
      </p>
    </section>
  );
}

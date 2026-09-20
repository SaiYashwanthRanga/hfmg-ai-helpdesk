import { History } from "lucide-react";

/**
 * State-history section. Permanently blocked, not empty: `voice_call_sessions`
 * stores only the *current* `state`, never a history of prior states or
 * their timestamps (confirmed against backend/app/db/models.py) — there is
 * no data to render, not just no data yet.
 */
export function CallTimeline() {
  return (
    <section className="border-b border-border px-6 py-4">
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground">
        <History className="size-4 text-muted-foreground" aria-hidden="true" />
        State History
      </h3>
      <p className="text-sm text-muted-foreground">
        Only the current state is stored — prior state transitions aren't recorded, so a history can't be shown here.
      </p>
    </section>
  );
}

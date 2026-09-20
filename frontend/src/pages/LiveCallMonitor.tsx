import { NotYetAvailable } from "../components/ui/NotYetAvailable";

/**
 * Placeholder for `/calls/live/:callId`. Not a top-level nav item, but the
 * route is registered now (FRONTEND_IMPLEMENTATION_PLAN.md Phase 2) so a
 * direct link never 404s. Real implementation is a Phase 5 stretch item
 * (WIREFRAMES.md §6) — polling-based, never claiming a live socket exists.
 */
export function LiveCallMonitor() {
  return (
    <NotYetAvailable
      title="Live Call Monitoring is not yet built"
      description="This screen ships alongside Calls in Phase 5, once the voice-calls detail endpoint exists."
    />
  );
}

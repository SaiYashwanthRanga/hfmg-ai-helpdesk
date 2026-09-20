import type { ReactNode } from "react";

/**
 * Structural guard, not just a layout wrapper. DESIGN.md §12: this system
 * has no authentication in front of it, so a Settings page that can edit
 * OPENAI_API_KEY/TWILIO_AUTH_TOKEN/SENDGRID_API_KEY is a live vulnerability,
 * not a missing feature. No component rendered inside this panel may
 * contain an `<input>`, a `<form>`, a save/submit button, or any call to a
 * settings-mutation endpoint — there isn't one to call (see api/settings.ts).
 * If a future change needs to add editing here, it must land alongside
 * Phase 3 backend authentication, not before it. A code reviewer checking
 * whether a write control snuck into Settings should start here.
 */
export function ReadOnlyConfigPanel({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-4 md:grid-cols-2">{children}</div>;
}

import { CircleHelp, Globe, Mail, Phone, User } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { SemanticColor } from "../ui/Badge";
import { Badge } from "../ui/Badge";
import type { TicketSource } from "../../types/ticket";

// DESIGN_SYSTEM.md §2.5 — PHONE ties to the AI accent color since every
// phone ticket passed through the voice agent (VOICE_AGENT_DESIGN.md).
const SOURCE_COLOR: Record<TicketSource, SemanticColor> = {
  WEB: "primary",
  PHONE: "ai-accent",
  EMAIL: "info",
  WALK_IN: "muted",
};

const SOURCE_ICON: Record<TicketSource, LucideIcon> = {
  WEB: Globe,
  PHONE: Phone,
  EMAIL: Mail,
  WALK_IN: User,
};

const SOURCE_LABEL: Record<TicketSource, string> = {
  WEB: "Web",
  PHONE: "Phone",
  EMAIL: "Email",
  WALK_IN: "Walk-in",
};

/**
 * Static source pill. `source` is optional because the running backend
 * does not serialize this field on ticket responses yet even though the
 * column and API_SPEC.md's documented contract both have it (see
 * types/ticket.ts) — renders an honest "Unknown" badge rather than
 * guessing WEB.
 */
export function SourceBadge({ source }: { source?: TicketSource }) {
  if (!source) {
    return <Badge label="Unknown" color="muted" icon={CircleHelp} />;
  }
  return <Badge label={SOURCE_LABEL[source]} color={SOURCE_COLOR[source]} icon={SOURCE_ICON[source]} />;
}

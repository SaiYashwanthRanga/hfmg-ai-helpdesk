import { Flame, Minus, Circle, TriangleAlert } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge } from "../ui/Badge";
import type { SemanticColor } from "../ui/Badge";
import type { AiPriority } from "../../types/ticketReport";

const COLOR: Record<AiPriority, SemanticColor> = { Critical: "danger", High: "warning", Medium: "info", Low: "muted" };
const ICON: Record<AiPriority, LucideIcon> = { Critical: Flame, High: TriangleAlert, Medium: Circle, Low: Minus };

/** The AI's recommended priority. The word and an icon carry the level; colour only reinforces it. */
export function AiPriorityBadge({ priority }: { priority: AiPriority | null }) {
  if (!priority) return <span className="text-xs text-stone-500">No recommendation</span>;
  return <Badge label={priority} color={COLOR[priority]} icon={ICON[priority]} size="sm" />;
}

/** Marks text that is the AI's interpretation, as opposed to a figure calculated from the file. */
export function AiTag() {
  return <Badge label="AI" color="ai-accent" size="sm" />;
}

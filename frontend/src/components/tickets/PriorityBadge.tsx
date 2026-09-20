import { Flame, Minus, Circle, TriangleAlert } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { SemanticColor } from "../ui/Badge";
import { Badge } from "../ui/Badge";
import type { Priority } from "../../types/ticket";

// DESIGN_SYSTEM.md §2.5 / §9.4 — priority → color, with a leading icon so
// severity is never signaled by color alone.
const PRIORITY_COLOR: Record<Priority, SemanticColor> = {
  LOW: "muted",
  MEDIUM: "info",
  HIGH: "warning",
  URGENT: "danger",
};

const PRIORITY_ICON: Record<Priority, LucideIcon> = {
  LOW: Minus,
  MEDIUM: Circle,
  HIGH: TriangleAlert,
  URGENT: Flame,
};

const PRIORITY_LABEL: Record<Priority, string> = {
  LOW: "Low",
  MEDIUM: "Medium",
  HIGH: "High",
  URGENT: "Urgent",
};

/** Static priority pill with a severity icon (DESIGN_SYSTEM.md §9.4). */
export function PriorityBadge({ priority }: { priority: Priority }) {
  return <Badge label={PRIORITY_LABEL[priority]} color={PRIORITY_COLOR[priority]} icon={PRIORITY_ICON[priority]} />;
}

import type { SemanticColor } from "../ui/Badge";
import { Badge } from "../ui/Badge";
import type { TicketStatus } from "../../types/ticket";

// DESIGN_SYSTEM.md §2.5 — ticket status → semantic color mapping.
const STATUS_COLOR: Record<TicketStatus, SemanticColor> = {
  NEW: "info",
  OPEN: "primary",
  IN_PROGRESS: "warning",
  ON_HOLD: "muted",
  RESOLVED: "success",
  CLOSED: "secondary",
  CANCELLED: "danger",
};

const STATUS_LABEL: Record<TicketStatus, string> = {
  NEW: "New",
  OPEN: "Open",
  IN_PROGRESS: "In Progress",
  ON_HOLD: "On Hold",
  RESOLVED: "Resolved",
  CLOSED: "Closed",
  CANCELLED: "Cancelled",
};

/** Static status pill (DESIGN_SYSTEM.md §9.2). For the editable control, see TicketStatusControl. */
export function StatusBadge({ status }: { status: TicketStatus }) {
  return <Badge label={STATUS_LABEL[status]} color={STATUS_COLOR[status]} />;
}

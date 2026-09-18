import type { Priority, TicketStatus } from "../types/ticket";

const STATUS_COLORS: Record<TicketStatus, string> = {
  NEW: "#2563eb",
  OPEN: "#0891b2",
  IN_PROGRESS: "#7c3aed",
  ON_HOLD: "#ca8a04",
  RESOLVED: "#16a34a",
  CLOSED: "#4b5563",
  CANCELLED: "#dc2626",
};

const PRIORITY_COLORS: Record<Priority, string> = {
  LOW: "#4b5563",
  MEDIUM: "#0891b2",
  HIGH: "#ca8a04",
  URGENT: "#dc2626",
};

function badgeStyle(color: string): React.CSSProperties {
  return {
    display: "inline-block",
    padding: "2px 10px",
    borderRadius: 999,
    fontSize: 12,
    fontWeight: 600,
    color: "#fff",
    backgroundColor: color,
    whiteSpace: "nowrap",
  };
}

export function StatusBadge({ status }: { status: TicketStatus }) {
  return <span style={badgeStyle(STATUS_COLORS[status])}>{status.replace("_", " ")}</span>;
}

export function PriorityBadge({ priority }: { priority: Priority }) {
  return <span style={badgeStyle(PRIORITY_COLORS[priority])}>{priority}</span>;
}

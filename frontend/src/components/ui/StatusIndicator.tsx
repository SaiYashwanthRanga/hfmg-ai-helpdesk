import { cn } from "../../lib/cn";

export type DependencyStatus = "operational" | "degraded" | "down" | "unknown";

export interface StatusIndicatorProps {
  status: DependencyStatus;
  label: string;
  lastChecked?: string;
}

const STATUS_DOT_CLASSES: Record<DependencyStatus, string> = {
  operational: "bg-success",
  degraded: "bg-warning",
  down: "bg-danger",
  unknown: "bg-muted-foreground",
};

const STATUS_TEXT: Record<DependencyStatus, string> = {
  operational: "Operational",
  degraded: "Degraded",
  down: "Down",
  unknown: "Unknown",
};

/**
 * Dot + label primitive (DESIGN_SYSTEM.md §9.1). Color is always paired with
 * a text label — never the only signal (DESIGN_SYSTEM.md §2.5).
 */
export function StatusIndicator({ status, label, lastChecked }: StatusIndicatorProps) {
  const title = lastChecked
    ? `Last checked ${lastChecked}`
    : status === "unknown"
      ? "Not yet checked — dependency health endpoint is not available"
      : undefined;

  return (
    <div className="flex items-center gap-2 text-sm" title={title}>
      <span
        className={cn("size-2 shrink-0 rounded-full", STATUS_DOT_CLASSES[status])}
        aria-hidden="true"
      />
      <span className="text-foreground">{label}</span>
      <span className="text-muted-foreground">{STATUS_TEXT[status]}</span>
    </div>
  );
}

import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";

export type DependencyStatus = "operational" | "degraded" | "down" | "unknown";

export interface StatusIndicatorProps {
  status: DependencyStatus;
  label: string;
  lastChecked?: string;
  to?: string;
}

const STATUS_PILL_CONFIG: Record<
  DependencyStatus,
  { dot: string; bg: string; text: string; labelText: string }
> = {
  operational: {
    dot: "bg-emerald-500",
    bg: "bg-emerald-50/80 border-emerald-200/80 text-emerald-800 hover:bg-emerald-100/70",
    text: "Operational",
    labelText: "text-stone-800 font-medium",
  },
  degraded: {
    dot: "bg-amber-500",
    bg: "bg-amber-50/80 border-amber-200/80 text-amber-800 hover:bg-amber-100/70",
    text: "Degraded",
    labelText: "text-stone-800 font-medium",
  },
  down: {
    dot: "bg-rose-500 animate-pulse",
    bg: "bg-rose-50/80 border-rose-200/80 text-rose-800 hover:bg-rose-100/70",
    text: "Down",
    labelText: "text-rose-900 font-medium",
  },
  unknown: {
    dot: "bg-stone-400",
    bg: "bg-stone-100 border-stone-200 text-stone-600 hover:bg-stone-200/70",
    text: "Unknown",
    labelText: "text-stone-800 font-medium",
  },
};

/**
 * Soft pill badge for header system health strip (Warm Minimalist Executive).
 */
export function StatusIndicator({ status, label, lastChecked, to }: StatusIndicatorProps) {
  const config = STATUS_PILL_CONFIG[status] || STATUS_PILL_CONFIG.unknown;
  const title = lastChecked
    ? `Last checked ${lastChecked}. Click to view details.`
    : status === "unknown"
      ? "Not yet checked — dependency health endpoint is not available"
      : "Click to view details";

  const pill = (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors shadow-2xs select-none",
        config.bg,
        to && "cursor-pointer"
      )}
      title={title}
    >
      <span className={cn("size-1.5 shrink-0 rounded-full", config.dot)} aria-hidden="true" />
      <span className={config.labelText}>{label}</span>
      <span className="text-[11px] opacity-80">{config.text}</span>
    </div>
  );

  if (to) {
    return (
      <Link to={to} className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800">
        {pill}
      </Link>
    );
  }

  return pill;
}


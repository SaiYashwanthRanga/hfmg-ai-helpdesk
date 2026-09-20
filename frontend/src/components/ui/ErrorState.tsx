import { AlertTriangle, OctagonAlert } from "lucide-react";
import { Button } from "./Button";

export type ErrorSeverity = "full-outage" | "degraded";

export interface ErrorStateProps {
  severity: ErrorSeverity;
  title: string;
  description: string;
  retry?: () => void;
}

/**
 * Shared error-state primitive (WIREFRAMES.md §12). An error reads as "the
 * system handled this gracefully," never a raw stack trace or "Error 500"
 * (DESIGN.md §2.3) — `full-outage` (e.g. database down) is visually more
 * severe than `degraded` (e.g. AI/email paused but tickets still work).
 */
export function ErrorState({ severity, title, description, retry }: ErrorStateProps) {
  const Icon = severity === "full-outage" ? OctagonAlert : AlertTriangle;
  const iconColor = severity === "full-outage" ? "text-danger" : "text-warning";

  return (
    <div className="flex flex-col items-center gap-2 py-12 text-center">
      <Icon className={iconColor} aria-hidden="true" />
      <p className="text-base font-medium text-foreground">{title}</p>
      <p className="max-w-sm text-sm text-muted-foreground">{description}</p>
      {retry ? (
        <Button variant="secondary" onClick={retry} className="mt-2">
          Retry
        </Button>
      ) : null}
    </div>
  );
}

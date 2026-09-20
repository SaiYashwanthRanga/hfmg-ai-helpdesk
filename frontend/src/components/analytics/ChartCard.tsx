import type { ReactNode } from "react";
import { Card } from "../ui/Card";
import { ErrorState } from "../ui/ErrorState";
import { LoadingState } from "../ui/LoadingState";

export interface ChartCardProps {
  title: string;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  isEmpty?: boolean;
  emptyMessage?: string;
  children: ReactNode;
}

/**
 * Analytics Card shell (DESIGN_SYSTEM.md §12): title top-left, chart body
 * below. Each chart has an independent loading/error state — one failed
 * endpoint never blanks the whole Analytics page (FRONTEND_IMPLEMENTATION_PLAN.md
 * Phase 6 acceptance criteria).
 */
export function ChartCard({ title, isLoading, isError, onRetry, isEmpty, emptyMessage, children }: ChartCardProps) {
  return (
    <Card>
      <h3 className="mb-4 text-sm font-semibold text-foreground">{title}</h3>
      {isLoading ? (
        <LoadingState variant="chart" />
      ) : isError ? (
        <ErrorState severity="degraded" title="Couldn't load this chart" description="Try again shortly." retry={onRetry} />
      ) : isEmpty ? (
        <p className="py-8 text-center text-sm text-muted-foreground">{emptyMessage ?? "No data in this range."}</p>
      ) : (
        children
      )}
    </Card>
  );
}

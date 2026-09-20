import { cn } from "../../lib/cn";

export type LoadingVariant = "table-rows" | "card" | "chart" | "page";

export interface LoadingStateProps {
  variant: LoadingVariant;
  count?: number;
}

function Shimmer({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-sm bg-card-hover", className)} />;
}

/** Shared skeleton/shimmer primitive (DESIGN_SYSTEM.md §19 loading token). */
export function LoadingState({ variant, count = 5 }: LoadingStateProps) {
  if (variant === "table-rows") {
    return (
      <div role="status" aria-label="Loading" className="flex flex-col gap-2">
        {Array.from({ length: count }).map((_, index) => (
          <Shimmer key={index} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (variant === "card") {
    return (
      <div role="status" aria-label="Loading" className="flex flex-col gap-3 rounded-lg border border-border bg-card p-6">
        <Shimmer className="h-4 w-1/3" />
        <Shimmer className="h-8 w-1/2" />
      </div>
    );
  }

  if (variant === "chart") {
    return (
      <div role="status" aria-label="Loading" className="rounded-lg border border-border bg-card p-6">
        <Shimmer className="h-4 w-1/4 mb-4" />
        <Shimmer className="h-48 w-full" />
      </div>
    );
  }

  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-4 p-6">
      <Shimmer className="h-8 w-1/3" />
      <Shimmer className="h-40 w-full" />
    </div>
  );
}

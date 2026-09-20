import { Construction } from "lucide-react";

export interface NotYetAvailableProps {
  title: string;
  description: string;
}

/**
 * "This page isn't built yet" — distinct from EmptyState's "there's no data
 * yet" (WIREFRAMES.md §11): an empty state implies the feature works and
 * there's simply nothing to show, which would be false here. Used by every
 * placeholder page until its own FRONTEND_IMPLEMENTATION_PLAN.md phase lands.
 */
export function NotYetAvailable({ title, description }: NotYetAvailableProps) {
  return (
    <div className="flex flex-col items-center gap-2 py-24 text-center">
      <Construction className="size-10 text-muted-foreground" strokeWidth={1.25} aria-hidden="true" />
      <p className="text-base font-medium text-foreground">{title}</p>
      <p className="max-w-sm text-sm text-muted-foreground">{description}</p>
    </div>
  );
}

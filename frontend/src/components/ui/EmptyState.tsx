import type { LucideIcon } from "lucide-react";
import { Button } from "./Button";

export interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
}

/** Shared empty-state primitive: icon + sentence + optional action (WIREFRAMES.md §11). */
export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-2 py-16 text-center">
      <Icon className="size-10 text-muted-foreground" strokeWidth={1.25} aria-hidden="true" />
      <p className="text-base font-medium text-foreground">{title}</p>
      {description ? <p className="max-w-xs text-sm text-muted-foreground">{description}</p> : null}
      {action ? (
        <Button variant="primary" onClick={action.onClick} className="mt-2">
          {action.label}
        </Button>
      ) : null}
    </div>
  );
}

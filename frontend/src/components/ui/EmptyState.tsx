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
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <div className="flex size-11 items-center justify-center rounded-full border border-stone-200 bg-stone-50 text-stone-400 shadow-2xs">
        <Icon className="size-5 text-stone-400" strokeWidth={1.5} aria-hidden="true" />
      </div>
      <p className="mt-1 text-sm font-semibold text-stone-900">{title}</p>
      {description ? <p className="max-w-xs text-xs text-stone-500">{description}</p> : null}
      {action ? (
        <Button variant="primary" onClick={action.onClick} className="mt-2 text-xs">
          {action.label}
        </Button>
      ) : null}
    </div>
  );
}


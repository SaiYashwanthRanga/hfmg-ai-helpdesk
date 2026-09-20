import { Search, X } from "lucide-react";
import { cn } from "../../lib/cn";

export interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  disabledReason?: string;
}

/**
 * Search input with leading icon + clear button (DESIGN_SYSTEM.md §11).
 * `disabled` + `disabledReason` are used when the backing `q` query param
 * doesn't exist yet — the control stays visible but inert, with an
 * explanation, rather than silently doing nothing (FRONTEND_IMPLEMENTATION_PLAN.md Phase 3).
 */
export function SearchBar({ value, onChange, placeholder = "Search…", disabled, disabledReason }: SearchBarProps) {
  return (
    <div className="relative" title={disabled ? disabledReason : undefined}>
      <Search
        className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <input
        type="search"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        placeholder={disabled ? (disabledReason ?? placeholder) : placeholder}
        aria-label={placeholder}
        className={cn(
          "h-10 w-full rounded-sm border border-border bg-card pr-9 pl-9 text-sm text-foreground placeholder:text-muted-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          "disabled:cursor-not-allowed disabled:opacity-40",
        )}
      />
      {value && !disabled ? (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => onChange("")}
          className="absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

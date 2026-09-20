import { ChevronDown } from "lucide-react";
import type { SelectHTMLAttributes } from "react";
import { useId } from "react";
import { cn } from "../../lib/cn";

export interface SelectOption {
  label: string;
  value: string;
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "multiple"> {
  label?: string;
  options: SelectOption[];
  multiple?: boolean;
}

/**
 * Dropdown/multi-select primitive (DESIGN_SYSTEM.md §11). Uses the native
 * `<select>` element for full keyboard/screen-reader support without
 * reimplementing listbox semantics by hand.
 */
export function Select({ label, options, id, className, ...rest }: SelectProps) {
  const generatedId = useId();
  const selectId = id ?? generatedId;

  return (
    <div className="flex flex-col gap-1.5">
      {label ? (
        <label htmlFor={selectId} className="text-sm font-medium text-foreground">
          {label}
        </label>
      ) : null}
      <div className="relative">
        <select
          id={selectId}
          className={cn(
            "h-10 w-full appearance-none rounded-sm border border-border bg-card px-3 pr-9 text-sm text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            "disabled:cursor-not-allowed disabled:opacity-40",
            className,
          )}
          {...rest}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <ChevronDown
          className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
      </div>
    </div>
  );
}

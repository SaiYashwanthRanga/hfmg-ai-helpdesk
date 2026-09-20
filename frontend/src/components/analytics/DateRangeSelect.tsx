import { Select } from "../ui/Select";

const OPTIONS = [
  { label: "Last 7 days", value: "7" },
  { label: "Last 30 days", value: "30" },
  { label: "Last 90 days", value: "90" },
];

export interface DateRangeSelectProps {
  days: number;
  onChange: (days: number) => void;
}

/** Shared date-range control for every Analytics chart (WIREFRAMES.md §7). Default 30 days. */
export function DateRangeSelect({ days, onChange }: DateRangeSelectProps) {
  return (
    <Select
      aria-label="Date range"
      value={String(days)}
      onChange={(event) => onChange(Number(event.target.value))}
      options={OPTIONS}
      className="w-40"
    />
  );
}

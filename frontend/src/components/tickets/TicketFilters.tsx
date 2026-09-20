import { Select } from "../ui/Select";
import type { Category, Priority, TicketSource, TicketStatus } from "../../types/ticket";
import { PRIORITY_OPTIONS, SOURCE_OPTIONS, STATUS_OPTIONS } from "../../types/ticket";

export interface TicketFilterState {
  status: TicketStatus | "";
  categoryId: string;
  priority: Priority | "";
  source: TicketSource | "";
}

export interface TicketFiltersProps {
  value: TicketFilterState;
  onChange: (next: TicketFilterState) => void;
  categories: Category[];
}

/**
 * Status, Category, Priority, and Source all filter the real
 * `GET /api/v1/tickets` endpoint (backend/app/api/v1/tickets.py) — Priority
 * and Source were enabled during the final review pass once Backend Tier 0
 * added their query params (verified live via `curl` before enabling; see
 * FRONTEND_GAP_REPORT.md's Phase 3 revalidation entry). `sort` is still not
 * a real param, so there is no sortable-column control here.
 */
export function TicketFilters({ value, onChange, categories }: TicketFiltersProps) {
  return (
    <div className="flex flex-wrap gap-3">
      <Select
        aria-label="Filter by status"
        value={value.status}
        onChange={(event) => onChange({ ...value, status: event.target.value as TicketStatus | "" })}
        options={[{ label: "All statuses", value: "" }, ...STATUS_OPTIONS.map((s) => ({ label: s.replace("_", " "), value: s }))]}
        className="w-44"
      />

      <Select
        aria-label="Filter by category"
        value={value.categoryId}
        onChange={(event) => onChange({ ...value, categoryId: event.target.value })}
        options={[{ label: "All categories", value: "" }, ...categories.map((c) => ({ label: c.name, value: c.id }))]}
        className="w-48"
      />

      <Select
        aria-label="Filter by priority"
        value={value.priority}
        onChange={(event) => onChange({ ...value, priority: event.target.value as Priority | "" })}
        options={[{ label: "All priorities", value: "" }, ...PRIORITY_OPTIONS.map((p) => ({ label: p, value: p }))]}
        className="w-40"
      />

      <Select
        aria-label="Filter by source"
        value={value.source}
        onChange={(event) => onChange({ ...value, source: event.target.value as TicketSource | "" })}
        options={[{ label: "All sources", value: "" }, ...SOURCE_OPTIONS.map((s) => ({ label: s, value: s }))]}
        className="w-40"
      />
    </div>
  );
}

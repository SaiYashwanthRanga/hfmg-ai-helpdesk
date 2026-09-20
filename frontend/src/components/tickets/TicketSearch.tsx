import { SearchBar } from "../ui/SearchBar";

export interface TicketSearchProps {
  value: string;
  onChange: (value: string) => void;
}

/**
 * Free-text ticket search. `GET /api/v1/tickets`'s `q` param is real
 * (Backend Tier 0) — matches via `ILIKE` across caller_name/ticket_number/
 * description/ai_summary, not the `to_tsvector` full-text index
 * `DATABASE_DESIGN.md` originally described (that index doesn't exist in
 * either database — see the backend's DOCS_GAP_REPORT.md). Functionally
 * equivalent at this system's documented volume. Enabled during the final
 * review pass; verified live via `curl` before enabling.
 */
export function TicketSearch({ value, onChange }: TicketSearchProps) {
  return <SearchBar value={value} onChange={onChange} placeholder="Search tickets…" />;
}

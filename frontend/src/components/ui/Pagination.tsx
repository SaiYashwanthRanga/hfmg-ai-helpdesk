import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./Button";
import { Select } from "./Select";

export interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
}

const PAGE_SIZE_OPTIONS = ["10", "25", "50", "100"];

/** Pagination footer shared by TicketTable/CallTable (DESIGN_SYSTEM.md §13). */
export function Pagination({ page, pageSize, total, onPageChange, onPageSizeChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 border-t border-table-border px-4 py-3 text-sm text-muted-foreground">
      {onPageSizeChange ? (
        <div className="flex items-center gap-2">
          <span>Rows per page</span>
          <Select
            aria-label="Rows per page"
            options={PAGE_SIZE_OPTIONS.map((size) => ({ label: size, value: size }))}
            value={String(pageSize)}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
            className="h-8 w-20"
          />
        </div>
      ) : (
        <span />
      )}

      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft className="size-4" aria-hidden="true" />
        </Button>
        <span>
          Page {page} of {totalPages}
        </span>
        <Button
          variant="ghost"
          aria-label="Next page"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight className="size-4" aria-hidden="true" />
        </Button>
      </div>

      <span>{total.toLocaleString()} total</span>
    </div>
  );
}

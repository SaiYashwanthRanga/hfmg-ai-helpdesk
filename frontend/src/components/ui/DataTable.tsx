import { ChevronDown, ChevronUp } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { LoadingState } from "./LoadingState";

export interface ColumnDef<T> {
  id: string;
  header: string;
  sortable?: boolean;
  render: (row: T) => ReactNode;
  align?: "left" | "right";
}

export interface SortState {
  columnId: string;
  direction: "asc" | "desc";
}

export interface DataTableProps<T> {
  columns: ColumnDef<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  isLoading?: boolean;
  sort?: SortState;
  onSortChange?: (columnId: string) => void;
  onRowClick?: (row: T) => void;
  emptyState: ReactNode;
}

/**
 * Generic sortable table shell underlying TicketTable/CallTable
 * (DESIGN_SYSTEM.md §13, COMPONENTS.md "DataTable"). Uses semantic
 * `<table>` markup — never a `<div>` grid styled to look like one
 * (DESIGN_SYSTEM.md §20.3).
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  isLoading = false,
  sort,
  onSortChange,
  onRowClick,
  emptyState,
}: DataTableProps<T>) {
  if (isLoading) {
    return <LoadingState variant="table-rows" count={8} />;
  }

  if (rows.length === 0) {
    return <div className="rounded-md border border-table-border bg-card">{emptyState}</div>;
  }

  return (
    <div className="overflow-x-auto rounded-md border border-table-border">
      <table className="w-full border-collapse text-sm">
        <thead className="bg-table-header">
          <tr>
            {columns.map((column) => {
              const isSorted = sort?.columnId === column.id;
              return (
                <th
                  key={column.id}
                  scope="col"
                  className={cn(
                    "h-11 px-4 text-xs font-semibold tracking-wide text-muted-foreground uppercase",
                    column.align === "right" ? "text-right" : "text-left",
                  )}
                >
                  {column.sortable ? (
                    <button
                      type="button"
                      onClick={() => onSortChange?.(column.id)}
                      className="inline-flex items-center gap-1 hover:text-foreground"
                    >
                      {column.header}
                      {isSorted ? (
                        sort?.direction === "asc" ? (
                          <ChevronUp className="size-3" aria-hidden="true" />
                        ) : (
                          <ChevronDown className="size-3" aria-hidden="true" />
                        )
                      ) : null}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              tabIndex={onRowClick ? 0 : undefined}
              onClick={() => onRowClick?.(row)}
              onKeyDown={(event) => {
                if (onRowClick && (event.key === "Enter" || event.key === " ")) {
                  event.preventDefault();
                  onRowClick(row);
                }
              }}
              className={cn(
                "h-14 border-t border-table-border bg-table-row",
                onRowClick && "cursor-pointer hover:bg-table-row-hover focus-visible:bg-table-row-hover",
              )}
            >
              {columns.map((column) => (
                <td
                  key={column.id}
                  className={cn("px-4 text-foreground", column.align === "right" ? "text-right" : "text-left")}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

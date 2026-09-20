import { Select } from "../ui/Select";
import { useUpdateTicketStatusMutation } from "../../api/tickets";
import { ApiError } from "../../api/client";
import { toast } from "../../lib/toastStore";
import type { TicketStatus } from "../../types/ticket";
import { VALID_STATUS_TRANSITIONS } from "../../types/ticket";
import { StatusBadge } from "./StatusBadge";

const STATUS_LABEL: Record<TicketStatus, string> = {
  NEW: "New",
  OPEN: "Open",
  IN_PROGRESS: "In Progress",
  ON_HOLD: "On Hold",
  RESOLVED: "Resolved",
  CLOSED: "Closed",
  CANCELLED: "Cancelled",
};

export interface TicketStatusControlProps {
  ticketId: string;
  status: TicketStatus;
}

/**
 * Interactive Status Controls section (WIREFRAMES.md §4). Options are
 * restricted to `VALID_STATUS_TRANSITIONS` — the same map the backend
 * enforces (backend/app/db/models.py) — so this never offers a transition
 * that `POST /tickets/{id}/status` would reject with a 422.
 */
export function TicketStatusControl({ ticketId, status }: TicketStatusControlProps) {
  const mutation = useUpdateTicketStatusMutation(ticketId);
  const nextOptions = VALID_STATUS_TRANSITIONS[status];
  const isTerminal = nextOptions.length === 0;

  function handleChange(next: TicketStatus) {
    if (next === status) return;
    mutation.mutate(next, {
      onSuccess: () => toast.success(`Ticket status updated to ${STATUS_LABEL[next]}`),
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : "Failed to update ticket status"),
    });
  }

  return (
    <section className="flex flex-col gap-2 border-b border-border px-6 py-4">
      <h3 className="text-sm font-semibold text-foreground">Status Controls</h3>
      <div className="flex items-center gap-3">
        <StatusBadge status={status} />
        {isTerminal ? (
          <span className="text-sm text-muted-foreground">No further transitions are available.</span>
        ) : (
          <Select
            aria-label="Change ticket status"
            value=""
            disabled={mutation.isPending}
            onChange={(event) => handleChange(event.target.value as TicketStatus)}
            options={[
              { label: mutation.isPending ? "Updating…" : "Change status to…", value: "" },
              ...nextOptions.map((s) => ({ label: STATUS_LABEL[s], value: s })),
            ]}
            className="w-48"
          />
        )}
      </div>
    </section>
  );
}

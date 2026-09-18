import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { PriorityBadge, StatusBadge } from "../components/Badge";
import type { Ticket, TicketStatus } from "../types/ticket";
import { STATUS_OPTIONS } from "../types/ticket";

const AI_SUMMARY_COPY: Record<Ticket["ai_summary_status"], string> = {
  DISABLED: "AI summaries are turned off for this deployment.",
  PENDING: "Generating summary…",
  COMPLETED: "",
  FAILED: "AI summary generation failed. You can rely on the description above.",
};

export function TicketDetailPage() {
  const { ticketId } = useParams<{ ticketId: string }>();
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusUpdating, setStatusUpdating] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!ticketId) return;
    api
      .getTicket(ticketId)
      .then(setTicket)
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : "Failed to load ticket"));
  }, [ticketId]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while an AI summary is still pending so it appears without a manual refresh.
  useEffect(() => {
    if (ticket?.ai_summary_status !== "PENDING") return;
    const interval = setInterval(load, 3000);
    return () => clearInterval(interval);
  }, [ticket?.ai_summary_status, load]);

  async function handleStatusChange(newStatus: TicketStatus) {
    if (!ticketId) return;
    setStatusUpdating(true);
    setStatusError(null);
    try {
      const updated = await api.updateTicketStatus(ticketId, newStatus);
      setTicket(updated);
    } catch (err) {
      setStatusError(err instanceof ApiError ? err.message : "Failed to update status");
    } finally {
      setStatusUpdating(false);
    }
  }

  if (error) return <p style={{ color: "#dc2626" }}>{error}</p>;
  if (!ticket) return <p>Loading…</p>;

  return (
    <div style={{ maxWidth: 720 }}>
      <Link to="/">&larr; Back to tickets</Link>
      <h1>
        {ticket.ticket_number} <PriorityBadge priority={ticket.priority} /> <StatusBadge status={ticket.status} />
      </h1>

      <dl style={dlStyle}>
        <dt style={dtStyle}>Caller Name</dt>
        <dd style={ddStyle}>{ticket.caller_name}</dd>

        <dt style={dtStyle}>Phone Number</dt>
        <dd style={ddStyle}>{ticket.phone_number}</dd>

        <dt style={dtStyle}>Email</dt>
        <dd style={ddStyle}>{ticket.email ?? "—"}</dd>

        <dt style={dtStyle}>Category</dt>
        <dd style={ddStyle}>{ticket.category.name}</dd>

        <dt style={dtStyle}>Created</dt>
        <dd style={ddStyle}>{new Date(ticket.created_at).toLocaleString()}</dd>
      </dl>

      <h2>Description</h2>
      <p style={{ whiteSpace: "pre-wrap" }}>{ticket.description}</p>

      <h2>AI Summary</h2>
      {ticket.ai_summary ? (
        <p style={{ whiteSpace: "pre-wrap" }}>{ticket.ai_summary}</p>
      ) : (
        <p style={{ color: "#6b7280", fontStyle: "italic" }}>{AI_SUMMARY_COPY[ticket.ai_summary_status]}</p>
      )}

      <h2>Update Status</h2>
      <select
        value={ticket.status}
        disabled={statusUpdating}
        onChange={(e) => handleStatusChange(e.target.value as TicketStatus)}
      >
        {STATUS_OPTIONS.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      {statusError && <p style={{ color: "#dc2626" }}>{statusError}</p>}
    </div>
  );
}

const dlStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "160px 1fr",
  rowGap: 6,
  margin: "16px 0",
};
const dtStyle: React.CSSProperties = { fontWeight: 600, color: "#4b5563" };
const ddStyle: React.CSSProperties = { margin: 0 };

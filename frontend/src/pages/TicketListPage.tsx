import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { PriorityBadge, StatusBadge } from "../components/Badge";
import type { TicketPage, TicketStatus } from "../types/ticket";
import { STATUS_OPTIONS } from "../types/ticket";

export function TicketListPage() {
  const [data, setData] = useState<TicketPage | null>(null);
  const [statusFilter, setStatusFilter] = useState<TicketStatus | "">("");
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listTickets({ page, status: statusFilter || undefined })
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load tickets");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, statusFilter]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h1>Tickets</h1>
        <Link to="/tickets/new" className="button">
          + New Ticket
        </Link>
      </div>

      <div style={{ marginBottom: 16 }}>
        <label>
          Status:{" "}
          <select
            value={statusFilter}
            onChange={(e) => {
              setPage(1);
              setStatusFilter(e.target.value as TicketStatus | "");
            }}
          >
            <option value="">All</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <p style={{ color: "#dc2626" }}>{error}</p>}
      {loading && <p>Loading…</p>}

      {data && !loading && (
        <>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "2px solid #e5e7eb" }}>
                <th style={cellStyle}>Ticket #</th>
                <th style={cellStyle}>Caller</th>
                <th style={cellStyle}>Category</th>
                <th style={cellStyle}>Priority</th>
                <th style={cellStyle}>Status</th>
                <th style={cellStyle}>AI Summary</th>
                <th style={cellStyle}>Created</th>
              </tr>
            </thead>
            <tbody>
              {data.items.length === 0 && (
                <tr>
                  <td colSpan={7} style={{ ...cellStyle, textAlign: "center", padding: 24 }}>
                    No tickets found.
                  </td>
                </tr>
              )}
              {data.items.map((ticket) => (
                <tr key={ticket.id} style={{ borderBottom: "1px solid #e5e7eb" }}>
                  <td style={cellStyle}>
                    <Link to={`/tickets/${ticket.id}`}>{ticket.ticket_number}</Link>
                  </td>
                  <td style={cellStyle}>{ticket.caller_name}</td>
                  <td style={cellStyle}>{ticket.category.name}</td>
                  <td style={cellStyle}>
                    <PriorityBadge priority={ticket.priority} />
                  </td>
                  <td style={cellStyle}>
                    <StatusBadge status={ticket.status} />
                  </td>
                  <td style={cellStyle}>{ticket.ai_summary_status}</td>
                  <td style={cellStyle}>{new Date(ticket.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div style={{ marginTop: 16, display: "flex", gap: 8, alignItems: "center" }}>
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </button>
            <span>
              Page {data.page} of {Math.max(data.total_pages, 1)} ({data.total} total)
            </span>
            <button disabled={page >= data.total_pages} onClick={() => setPage((p) => p + 1)}>
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}

const cellStyle: React.CSSProperties = { padding: "8px 12px" };

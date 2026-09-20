import { Navigate, useParams } from "react-router-dom";

/**
 * `/tickets/:ticketId` predates the Ticket Intelligence Drawer
 * (WIREFRAMES.md §4) — ticket detail now opens as a slide-over on
 * `/tickets` via the `?ticket=` param instead of a dedicated page/route.
 * This redirect keeps any existing bookmark or notification link pointing
 * at the old URL working, rather than 404ing it outright.
 */
export function TicketDetailRedirect() {
  const { ticketId } = useParams<{ ticketId: string }>();
  return <Navigate to={`/tickets?ticket=${ticketId}`} replace />;
}

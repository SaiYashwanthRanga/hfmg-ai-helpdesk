import { Link, Route, Routes } from "react-router-dom";
import { NewTicketPage } from "./pages/NewTicketPage";
import { TicketDetailPage } from "./pages/TicketDetailPage";
import { TicketListPage } from "./pages/TicketListPage";

function App() {
  return (
    <div style={{ maxWidth: 1000, margin: "0 auto", padding: "24px 16px", fontFamily: "system-ui, sans-serif" }}>
      <header style={{ marginBottom: 24 }}>
        <Link to="/" style={{ textDecoration: "none", color: "inherit" }}>
          <strong>HFMG IT Help Desk</strong>
        </Link>
      </header>
      <Routes>
        <Route path="/" element={<TicketListPage />} />
        <Route path="/tickets/new" element={<NewTicketPage />} />
        <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
      </Routes>
    </div>
  );
}

export default App;

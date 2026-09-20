import { Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { PageContainer } from "./components/layout/PageContainer";
import { AIInsightsPage } from "./pages/AIInsightsPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { CallsPage } from "./pages/CallsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LiveCallMonitor } from "./pages/LiveCallMonitor";
import { NewTicketPage } from "./pages/NewTicketPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TicketDetailRedirect } from "./pages/TicketDetailRedirect";
import { TicketsPage } from "./pages/TicketsPage";
import { DevComponentsPage } from "./routes/DevComponentsPage";

// Route order matches the six-item nav in DESIGN.md §4, plus the /calls/live
// sub-route and the internal-only component playground.
function App() {
  return (
    <AppShell>
      <PageContainer>
        <Routes>
          <Route path="/" element={<DashboardPage />} />

          <Route path="/tickets" element={<TicketsPage />} />
          <Route path="/tickets/new" element={<NewTicketPage />} />
          <Route path="/tickets/:ticketId" element={<TicketDetailRedirect />} />

          <Route path="/calls" element={<CallsPage />} />
          <Route path="/calls/live/:callId" element={<LiveCallMonitor />} />

          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/ai-insights" element={<AIInsightsPage />} />
          <Route path="/settings" element={<SettingsPage />} />

          {/* Internal-only, not linked from navigation — see DevComponentsPage. */}
          <Route path="/dev/components" element={<DevComponentsPage />} />
        </Routes>
      </PageContainer>
    </AppShell>
  );
}

export default App;

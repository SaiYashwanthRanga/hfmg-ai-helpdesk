import { useQuery } from "@tanstack/react-query";
import { fetchDependencyHealth } from "../../api/health";
import { Card } from "../ui/Card";
import { StatusIndicator } from "../ui/StatusIndicator";

/**
 * Dashboard-scale rendering of the same four dependency checks Header's
 * StatusBar shows compactly (DESIGN.md §6.1). Shares StatusBar's query key
 * so the two never drift or double-fetch.
 */
export function SystemHealthPanel() {
  const { data } = useQuery({
    queryKey: ["health", "dependencies"],
    queryFn: fetchDependencyHealth,
    refetchInterval: 30_000,
  });

  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-foreground">System Health</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatusIndicator status={data?.openai.status ?? "unknown"} label="OpenAI" lastChecked={data?.openai.lastChecked} />
        <StatusIndicator status={data?.twilio.status ?? "unknown"} label="Twilio" lastChecked={data?.twilio.lastChecked} />
        <StatusIndicator status={data?.database.status ?? "unknown"} label="Database" lastChecked={data?.database.lastChecked} />
        <StatusIndicator status={data?.email.status ?? "unknown"} label="Email" lastChecked={data?.email.lastChecked} />
      </div>
    </Card>
  );
}

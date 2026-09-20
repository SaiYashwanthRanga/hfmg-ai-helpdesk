import { useQuery } from "@tanstack/react-query";
import { fetchDependencyHealth } from "../../api/health";
import { StatusIndicator } from "../ui/StatusIndicator";

/**
 * The always-visible four-way OpenAI/Twilio/Database/Email health strip
 * (DESIGN.md §5/§6.1). Renders "unknown" for every dependency until the
 * backend's health-dependencies endpoint exists — see api/health.ts.
 */
export function StatusBar() {
  const { data } = useQuery({
    queryKey: ["health", "dependencies"],
    queryFn: fetchDependencyHealth,
    refetchInterval: 30_000,
  });

  return (
    <div className="flex flex-wrap items-center gap-4">
      <StatusIndicator status={data?.openai.status ?? "unknown"} label="OpenAI" lastChecked={data?.openai.lastChecked} />
      <StatusIndicator status={data?.twilio.status ?? "unknown"} label="Twilio" lastChecked={data?.twilio.lastChecked} />
      <StatusIndicator status={data?.database.status ?? "unknown"} label="Database" lastChecked={data?.database.lastChecked} />
      <StatusIndicator status={data?.email.status ?? "unknown"} label="Email" lastChecked={data?.email.lastChecked} />
    </div>
  );
}

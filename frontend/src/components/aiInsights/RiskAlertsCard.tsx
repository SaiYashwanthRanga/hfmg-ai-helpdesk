import { TriangleAlert } from "lucide-react";
import { BlockedInsightCard } from "./BlockedInsightCard";

export function RiskAlertsCard({ blockedReason }: { blockedReason: string }) {
  return <BlockedInsightCard icon={TriangleAlert} title="High Risk Alerts" blockedReason={blockedReason} />;
}

import { TrendingUp } from "lucide-react";
import { BlockedInsightCard } from "./BlockedInsightCard";

export function TrendingIssuesCard({ blockedReason }: { blockedReason: string }) {
  return <BlockedInsightCard icon={TrendingUp} title="Trending Issues" blockedReason={blockedReason} />;
}

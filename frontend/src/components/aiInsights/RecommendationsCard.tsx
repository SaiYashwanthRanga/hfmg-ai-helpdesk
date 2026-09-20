import { Lightbulb } from "lucide-react";
import { BlockedInsightCard } from "./BlockedInsightCard";

export function RecommendationsCard({ blockedReason }: { blockedReason: string }) {
  return <BlockedInsightCard icon={Lightbulb} title="AI Recommendations" blockedReason={blockedReason} />;
}

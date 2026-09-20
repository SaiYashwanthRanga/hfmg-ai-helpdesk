import { Repeat } from "lucide-react";
import { BlockedInsightCard } from "./BlockedInsightCard";

export function RepeatedProblemsCard({ blockedReason }: { blockedReason: string }) {
  return <BlockedInsightCard icon={Repeat} title="Repeated Problems" blockedReason={blockedReason} />;
}

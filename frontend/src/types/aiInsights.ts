import type { CategoryBreakdownItem } from "./analytics";

export interface CategoryBreakdownInsight {
  status: "ready";
  items: CategoryBreakdownItem[];
}

/** Every section WIREFRAMES.md names that the backend doesn't build ships
 * this shape instead of a fabricated list — `blocked_reason` is always a
 * real, specific explanation (API_SPEC.md §14). There is deliberately no
 * `items` field here, so a component can't mistake "not built" for
 * "no results". */
export interface BlockedInsightSection {
  status: "blocked";
  blocked_reason: string;
}

export interface AIInsightsResponse {
  category_breakdown: CategoryBreakdownInsight;
  trending_issues: BlockedInsightSection;
  repeated_problems: BlockedInsightSection;
  high_risk_alerts: BlockedInsightSection;
  recommendations: BlockedInsightSection;
}

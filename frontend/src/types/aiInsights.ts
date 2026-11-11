import type { CategoryBreakdownItem } from "./analytics";
import type { Priority } from "./ticket";

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

/** A ticket an AI statement is based on. Every theme, attention item and
 * recommendation carries at least one, so a claim always opens onto its evidence. */
export interface InsightTicketRef {
  id: string;
  ticket_number: string;
}

/** A recurring problem. Only `title` and `note` are the model's words; the
 * counts, priority and categories are computed server-side from the cited tickets. */
export interface InsightTheme {
  title: string;
  note: string;
  ticket_count: number;
  open_count: number;
  top_priority: Priority;
  categories: string[];
  tickets: InsightTicketRef[];
}

export interface InsightAttention {
  title: string;
  reason: string;
  severity: "high" | "medium";
  tickets: InsightTicketRef[];
}

export interface InsightRecommendation {
  action: string;
  rationale: string;
  tickets: InsightTicketRef[];
}

export interface CategoryMovementItem {
  category: string;
  count: number;
  previous_count: number;
}

/** GET /ai-insights/analysis. `status` covers the AI part only: the counts
 * and `category_movement` are real whatever it says. */
export interface AIAnalysisResponse {
  status: "ready" | "empty" | "unavailable";
  reason: "not_configured" | "disabled" | "failed" | null;
  generated_at: string | null;
  days: number;
  tickets_in_period: number;
  open_in_period: number;
  tickets_in_previous_period: number;
  tickets_analyzed: number;
  headline: string;
  briefing: string;
  themes: InsightTheme[];
  attention: InsightAttention[];
  recommendations: InsightRecommendation[];
  category_movement: CategoryMovementItem[];
}

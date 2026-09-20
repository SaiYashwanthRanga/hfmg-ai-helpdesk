// DESIGN_SYSTEM.md §17 — the one categorical palette every chart in this
// product draws from. Six slots is enough for every categorical dataset
// here (6 ticket categories, 4 priorities, 4 sources); if a dataset ever
// needs a 7th, group the tail into "Other" rather than adding a color.
export const CHART_COLORS = [
  "#3B82F6", // primary
  "#38BDF8", // info
  "#8B5CF6", // ai-accent
  "#22C55E", // success
  "#F59E0B", // warning
  "#94A3B8", // muted
];

export const CHART_GRID_COLOR = "#27324A"; // --border
export const CHART_AXIS_COLOR = "#94A3B8"; // --muted-foreground
export const CHART_TOOLTIP_BG = "#1F2937"; // --card

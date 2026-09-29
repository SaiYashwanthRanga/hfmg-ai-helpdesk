import { cn } from "../../lib/cn";
import type { SimulatorStatus } from "../../types/voiceSimulator";

const STATUS: Record<SimulatorStatus, { label: string; dot: string; pill: string; hint: string }> = {
  idle: { label: "Idle", dot: "bg-stone-400", pill: "border-stone-200 bg-stone-100 text-stone-700", hint: "No call in progress." },
  connecting: {
    label: "Connecting",
    dot: "bg-amber-500 animate-pulse",
    pill: "border-amber-200/80 bg-amber-50 text-amber-800",
    hint: "Starting the call.",
  },
  listening: {
    label: "Listening",
    dot: "bg-emerald-500 animate-pulse",
    pill: "border-emerald-200/80 bg-emerald-50 text-emerald-800",
    hint: "Your turn to speak.",
  },
  thinking: {
    label: "Thinking",
    dot: "bg-amber-500 animate-pulse",
    pill: "border-amber-200/80 bg-amber-50 text-amber-800",
    hint: "The agent is processing what you said.",
  },
  speaking: {
    label: "Speaking",
    dot: "bg-emerald-700",
    pill: "border-emerald-200/80 bg-emerald-50 text-emerald-900",
    hint: "The agent is talking.",
  },
  disconnected: {
    label: "Disconnected",
    dot: "bg-stone-500",
    pill: "border-stone-200 bg-stone-100 text-stone-700",
    hint: "The call has ended.",
  },
  error: { label: "Error", dot: "bg-rose-600", pill: "border-rose-200/80 bg-rose-50 text-rose-800", hint: "Something failed." },
};

/**
 * Call status pill. Owns the page's polite live region, so screen reader
 * users hear "Listening", "Thinking"… as the call moves. Colour is never the
 * only signal: the label is always shown.
 */
export function CallStatusIndicator({ status }: { status: SimulatorStatus }) {
  const config = STATUS[status];
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-medium shadow-2xs", config.pill)}
      title={config.hint}
    >
      <span className={cn("size-2 shrink-0 rounded-full", config.dot)} aria-hidden="true" />
      <span>{config.label}</span>
      <span className="sr-only">. {config.hint}</span>
    </div>
  );
}

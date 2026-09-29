import { Bot, Info, Keyboard, Mic, TriangleAlert, User, Workflow } from "lucide-react";
import { useEffect, useRef } from "react";
import { cn } from "../../lib/cn";
import type { InputMode, TimelineEntry } from "../../types/voiceSimulator";

function formatTime(iso: string) {
  const date = new Date(iso.length > 24 ? iso.slice(0, 24) : iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const INPUT_ICON: Record<InputMode, typeof Mic> = { voice: Mic, text: Keyboard, mock: Workflow, system: Bot };
const INPUT_LABEL: Record<InputMode, string> = { voice: "spoken", text: "typed", mock: "mock caller", system: "system" };

export interface ConversationTimelineProps {
  entries: TimelineEntry[];
  selectedTurnId: string | null;
  onSelectTurn: (turnClientId: string) => void;
}

/**
 * Chat-style transcript. `role="log"` makes screen readers announce new
 * messages as they arrive; each message selects its turn in the inspector.
 */
export function ConversationTimeline({ entries, selectedTurnId, onSelectTurn }: ConversationTimelineProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [entries.length]);

  if (entries.length === 0) {
    return (
      <div className="flex h-full min-h-48 flex-col items-center justify-center gap-2 px-6 text-center">
        <Bot className="size-6 text-stone-300" aria-hidden="true" />
        <p className="text-sm font-medium text-stone-700">No conversation yet</p>
        <p className="max-w-xs text-xs text-stone-500">Start a call. The agent greets you, then asks what the problem is.</p>
      </div>
    );
  }

  return (
    <div role="log" aria-label="Conversation" aria-live="polite" className="flex flex-col gap-3 p-4">
      {entries.map((entry) => {
        if (entry.kind === "system") {
          const Icon = entry.tone === "info" ? Info : TriangleAlert;
          return (
            <div
              key={entry.key}
              className={cn(
                "mx-auto flex max-w-[90%] items-start gap-2 rounded-lg px-3 py-1.5 text-xs",
                entry.tone === "error" && "bg-rose-50 text-rose-800",
                entry.tone === "warning" && "bg-amber-50 text-amber-800",
                entry.tone === "info" && "bg-stone-100 text-stone-600",
              )}
            >
              <Icon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              <span>{entry.text}</span>
            </div>
          );
        }

        const isCaller = entry.kind === "caller";
        const selected = selectedTurnId === entry.turnClientId;
        const InputIcon = isCaller ? INPUT_ICON[entry.inputMode] : Bot;
        return (
          <div key={entry.key} className={cn("flex gap-2", isCaller ? "flex-row-reverse" : "flex-row")}>
            <span
              className={cn(
                "mt-5 flex size-7 shrink-0 items-center justify-center rounded-full",
                isCaller ? "bg-stone-200 text-stone-700" : "bg-emerald-800 text-white",
              )}
              aria-hidden="true"
            >
              {isCaller ? <User className="size-3.5" /> : <Bot className="size-3.5" />}
            </span>
            <div className={cn("flex max-w-[80%] flex-col gap-1", isCaller ? "items-end" : "items-start")}>
              <div className="flex items-center gap-1.5 text-[11px] text-stone-500">
                <span className="font-semibold text-stone-700">{isCaller ? "You" : "HFMG IT Help Desk"}</span>
                <time dateTime={entry.at}>{formatTime(entry.at)}</time>
                {isCaller ? (
                  <span className="inline-flex items-center gap-0.5" title={`Input: ${INPUT_LABEL[entry.inputMode]}`}>
                    <InputIcon className="size-3" aria-hidden="true" />
                    <span className="sr-only">{INPUT_LABEL[entry.inputMode]}</span>
                  </span>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => onSelectTurn(entry.turnClientId)}
                aria-pressed={selected}
                aria-label={`${isCaller ? "You said" : "Agent said"}: ${entry.text}. Inspect this turn.`}
                className={cn(
                  "rounded-2xl px-3.5 py-2 text-left text-sm leading-relaxed transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800",
                  isCaller ? "rounded-tr-sm bg-stone-800 text-white" : "rounded-tl-sm border border-stone-200/80 bg-white text-stone-900",
                  isCaller && entry.pending && "opacity-70",
                  selected && "ring-2 ring-emerald-600 ring-offset-1",
                )}
              >
                {entry.text}
                {isCaller && entry.pending ? <span className="ml-1 animate-pulse">…</span> : null}
              </button>
            </div>
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}

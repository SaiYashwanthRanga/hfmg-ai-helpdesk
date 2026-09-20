import { cn } from "../../lib/cn";
import type { VoiceCallTurn } from "../../types/voiceCall";

export interface TranscriptViewerProps {
  turns: VoiceCallTurn[];
}

/**
 * Renders the ordered `turns` transcript as a chat-style log
 * (WIREFRAMES.md §5/§6). No audio playback anywhere — text transcript is
 * the only call record; recording is off by design (TWILIO_ARCHITECTURE.md §9).
 */
export function TranscriptViewer({ turns }: TranscriptViewerProps) {
  if (turns.length === 0) {
    return <p className="text-sm text-muted-foreground">No transcript recorded for this call yet.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {turns.map((turn, index) => {
        const isAgent = turn.role === "agent";
        return (
          <div key={index} className={cn("flex flex-col gap-0.5", isAgent ? "items-start" : "items-end")}>
            <span className="text-xs font-medium text-muted-foreground">{isAgent ? "Agent" : "Caller"}</span>
            <p
              className={cn(
                "max-w-[85%] rounded-md px-3 py-2 text-sm text-foreground",
                // AI accent marks the agent's AI-generated speech
                // (DESIGN_SYSTEM.md §2.2) — the caller's turns are human,
                // not AI output, so they get the neutral surface instead.
                isAgent ? "bg-ai-subtle" : "bg-card-hover",
              )}
            >
              {turn.text}
            </p>
            {turn.confidence !== null ? (
              <span className="text-xs text-muted-foreground">confidence: {Math.round(turn.confidence * 100)}%</span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

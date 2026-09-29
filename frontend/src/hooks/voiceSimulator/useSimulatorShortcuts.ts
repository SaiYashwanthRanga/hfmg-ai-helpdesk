import { useEffect, useRef } from "react";

export interface SimulatorShortcutHandlers {
  pushToTalkDown: () => void;
  pushToTalkUp: () => void;
  toggleMute: () => void;
  toggleCall: () => void;
  clear: () => void;
  focusText: () => void;
  toggleInspector: () => void;
  toggleListenMode: () => void;
  showHelp: () => void;
}

export const SHORTCUTS: Array<{ keys: string; action: string }> = [
  { keys: "Space (hold)", action: "Push-to-talk" },
  { keys: "M", action: "Mute / unmute the microphone" },
  { keys: "Shift + S", action: "Start the call, or stop it if one is active" },
  { keys: "Shift + C", action: "Clear the session" },
  { keys: "T", action: "Type instead of speaking" },
  { keys: "D", action: "Open / close the conversation inspector" },
  { keys: "L", action: "Switch push-to-talk / continuous listening" },
  { keys: "?", action: "Show these shortcuts" },
];

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable || target.closest("[role='dialog']") !== null;
}

/**
 * Page-level keyboard shortcuts. Ignored while typing in a field or inside
 * a dialog, and never combined with Ctrl/Cmd/Alt so browser and screen
 * reader shortcuts keep working.
 */
export function useSimulatorShortcuts(handlers: SimulatorShortcutHandlers) {
  const ref = useRef(handlers);
  useEffect(() => {
    ref.current = handlers;
  }, [handlers]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || isTypingTarget(event.target)) return;
      const h = ref.current;
      if (event.code === "Space") {
        // Buttons handle their own Space; only take it when nothing interactive is focused.
        if (event.target instanceof HTMLButtonElement) return;
        event.preventDefault();
        if (!event.repeat) h.pushToTalkDown();
        return;
      }
      if (event.repeat) return;
      const key = event.key.toLowerCase();
      if (event.shiftKey && key === "s") h.toggleCall();
      else if (event.shiftKey && key === "c") h.clear();
      else if (event.key === "?") h.showHelp();
      else if (event.shiftKey) return;
      else if (key === "m") h.toggleMute();
      else if (key === "t") {
        event.preventDefault();
        h.focusText();
      } else if (key === "d") h.toggleInspector();
      else if (key === "l") h.toggleListenMode();
      else return;
      event.preventDefault();
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code === "Space" && !(event.target instanceof HTMLButtonElement) && !isTypingTarget(event.target)) {
        event.preventDefault();
        ref.current.pushToTalkUp();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, []);
}

import { SendHorizontal } from "lucide-react";
import { forwardRef, useState } from "react";
import type { FormEvent } from "react";
import { Button } from "../ui/Button";

export interface TextUtteranceInputProps {
  disabled: boolean;
  disabledReason: string;
  onSubmit: (text: string) => boolean;
}

/**
 * Typed alternative to speaking (design D7): tests the agent without a
 * microphone and isolates NLU behaviour from speech-to-text errors.
 */
export const TextUtteranceInput = forwardRef<HTMLInputElement, TextUtteranceInputProps>(function TextUtteranceInput(
  { disabled, disabledReason, onSubmit },
  ref,
) {
  const [text, setText] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (disabled) return;
    if (onSubmit(text.trim())) setText("");
  }

  return (
    <form onSubmit={handleSubmit} className="flex items-center gap-2 border-t border-stone-100 p-3">
      <label htmlFor="sim-text-input" className="sr-only">
        Type what the caller says
      </label>
      <input
        ref={ref}
        id="sim-text-input"
        value={text}
        onChange={(event) => setText(event.target.value)}
        maxLength={2000}
        autoComplete="off"
        placeholder={disabled ? disabledReason : "Type what the caller says…"}
        disabled={disabled}
        className="h-10 min-w-0 flex-1 rounded-lg border border-stone-200/80 bg-stone-50/50 px-3 text-sm text-stone-900 placeholder:text-stone-400 focus:border-emerald-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-emerald-800 disabled:cursor-not-allowed disabled:opacity-60"
      />
      <Button type="submit" size="md" disabled={disabled} icon={<SendHorizontal className="size-4" aria-hidden="true" />}>
        <span className="sr-only sm:not-sr-only">Send</span>
      </Button>
    </form>
  );
});

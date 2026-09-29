import { useEffect, useRef } from "react";
import { readLevel } from "../../lib/voiceSimulator/audio";

export interface VadSettings {
  /** RMS level (0..1) that counts as speech. */
  threshold: number;
  /** Continuous speech needed before an utterance opens. */
  minSpeechMs: number;
  /** Silence after speech that closes the utterance. */
  silenceMs: number;
}

export const DEFAULT_VAD: VadSettings = { threshold: 0.02, minSpeechMs: 250, silenceMs: 800 };

interface Options {
  analyser: AnalyserNode | null;
  enabled: boolean;
  settings: VadSettings;
  onSpeechStart: () => void;
  /** `speechEndedAt` is performance.now() when the caller actually stopped talking. */
  onSpeechEnd: (speechEndedAt: number) => void;
}

/**
 * Energy-based voice activity detection over the mic analyser, for
 * continuous listening. Checks every 30ms off a timer (not rAF) so it keeps
 * working in a background tab. Deliberately simple: good enough for a quiet
 * room, adjustable in the page's advanced settings for a noisy one.
 */
export function useVoiceActivity({ analyser, enabled, settings, onSpeechStart, onSpeechEnd }: Options) {
  const callbacks = useRef({ onSpeechStart, onSpeechEnd });
  useEffect(() => {
    callbacks.current = { onSpeechStart, onSpeechEnd };
  }, [onSpeechStart, onSpeechEnd]);

  useEffect(() => {
    if (!analyser || !enabled) return;
    const buffer = new Float32Array(analyser.fftSize);
    let speaking = false;
    let aboveSince: number | null = null;
    let lastVoice = 0;

    const timer = window.setInterval(() => {
      const level = readLevel(analyser, buffer);
      const now = performance.now();
      if (level >= settings.threshold) {
        lastVoice = now;
        aboveSince ??= now;
        if (!speaking && now - aboveSince >= settings.minSpeechMs) {
          speaking = true;
          callbacks.current.onSpeechStart();
        }
      } else {
        aboveSince = null;
        if (speaking && now - lastVoice >= settings.silenceMs) {
          speaking = false;
          callbacks.current.onSpeechEnd(lastVoice);
        }
      }
    }, 30);
    return () => window.clearInterval(timer);
  }, [analyser, enabled, settings.threshold, settings.minSpeechMs, settings.silenceMs]);
}

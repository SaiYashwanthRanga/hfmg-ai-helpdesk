import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getAudioContext } from "../../lib/voiceSimulator/audio";

export interface PlaybackTiming {
  /** Reply received → first audio frame out (stream first bytes + decode + output latency). */
  startMs: number;
  /** performance.now() of the first audio frame. */
  firstFrameAt: number;
  durationMs: number;
}

/**
 * Plays the agent's reply by streaming it from the backend's speech
 * endpoint: the browser starts playback on the first bytes, while the rest
 * is still being synthesized. One <audio> element is reused for the page and
 * routed through a speaker AnalyserNode so the page can meter what the
 * caller hears. Nothing is cached or stored.
 */
export function useAudioPlayer() {
  const elementRef = useRef<HTMLAudioElement | null>(null);
  const cancelRef = useRef<(() => void) | null>(null);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);

  const ensureElement = useCallback(() => {
    if (!elementRef.current) {
      const element = new Audio();
      // Needed to route a cross-origin stream through Web Audio (the meter).
      element.crossOrigin = "anonymous";
      element.preload = "auto";
      const context = getAudioContext();
      const source = context.createMediaElementSource(element);
      const node = context.createAnalyser();
      node.fftSize = 1024;
      source.connect(node);
      node.connect(context.destination);
      elementRef.current = element;
      setAnalyser(node);
    }
    return elementRef.current;
  }, []);

  const stop = useCallback(() => {
    cancelRef.current?.();
    cancelRef.current = null;
    const element = elementRef.current;
    if (element) {
      element.pause();
      element.removeAttribute("src");
      element.load();
    }
  }, []);

  /**
   * Resolves with timings once playback finishes, or null if it was stopped
   * or the audio could not be loaded (the reply is still shown as text).
   */
  const play = useCallback(
    async (
      url: string,
      receivedAt: number,
      onFirstFrame?: (timing: Omit<PlaybackTiming, "durationMs">) => void,
    ): Promise<PlaybackTiming | null> => {
      stop();
      const context = getAudioContext();
      if (context.state === "suspended") await context.resume();
      const element = ensureElement();
      const outputLatencyMs = ((context.outputLatency || 0) + (context.baseLatency || 0)) * 1000;

      return new Promise((resolve) => {
        let first: Omit<PlaybackTiming, "durationMs"> | null = null;
        let settled = false;

        function onPlaying() {
          if (first) return;
          const firstFrameAt = performance.now() + outputLatencyMs;
          first = { startMs: Math.max(0, firstFrameAt - receivedAt), firstFrameAt };
          onFirstFrame?.(first);
        }
        function onEnded() {
          finish(first ? { ...first, durationMs: performance.now() - first.firstFrameAt } : null);
        }
        function onError() {
          finish(null);
        }
        function detach() {
          element.removeEventListener("playing", onPlaying);
          element.removeEventListener("ended", onEnded);
          element.removeEventListener("error", onError);
        }
        function finish(value: PlaybackTiming | null) {
          if (settled) return;
          settled = true;
          detach();
          resolve(value);
        }

        element.addEventListener("playing", onPlaying);
        element.addEventListener("ended", onEnded);
        element.addEventListener("error", onError);
        cancelRef.current = () => finish(null);
        element.src = url;
        element.play().catch(() => finish(null));
      });
    },
    [ensureElement, stop],
  );

  /**
   * Speak with the browser's built-in voice (Web Speech API): no network
   * round trip, so audio starts almost immediately. Lower quality, and not
   * routed through the speaker meter (browsers don't expose it as a stream).
   */
  const speak = useCallback(
    (
      text: string,
      receivedAt: number,
      onFirstFrame?: (timing: Omit<PlaybackTiming, "durationMs">) => void,
    ): Promise<PlaybackTiming | null> => {
      stop();
      if (!browserVoiceAvailable()) return Promise.resolve(null);
      window.speechSynthesis.cancel();
      return new Promise((resolve) => {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = "en-US";
        const voice = window.speechSynthesis.getVoices().find((v) => v.lang === "en-US" && v.localService);
        if (voice) utterance.voice = voice;
        let first: Omit<PlaybackTiming, "durationMs"> | null = null;
        let settled = false;
        const finish = (value: PlaybackTiming | null) => {
          if (settled) return;
          settled = true;
          resolve(value);
        };
        utterance.onstart = () => {
          const firstFrameAt = performance.now();
          first = { startMs: Math.max(0, firstFrameAt - receivedAt), firstFrameAt };
          onFirstFrame?.(first);
        };
        utterance.onend = () => finish(first ? { ...first, durationMs: performance.now() - first.firstFrameAt } : null);
        utterance.onerror = () => finish(null);
        cancelRef.current = () => {
          window.speechSynthesis.cancel();
          finish(null);
        };
        window.speechSynthesis.speak(utterance);
      });
    },
    [stop],
  );

  useEffect(() => () => stop(), [stop]);

  return useMemo(() => ({ play, speak, stop, analyser }), [play, speak, stop, analyser]);
}

export function browserVoiceAvailable(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
}

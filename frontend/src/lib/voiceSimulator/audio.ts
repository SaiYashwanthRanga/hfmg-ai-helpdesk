/**
 * Browser audio helpers shared by the simulator's hooks.
 *
 * One AudioContext for the page, created on the Start Call click so browser
 * autoplay policies allow playback without a second gesture.
 */

let sharedContext: AudioContext | null = null;

export function getAudioContext(): AudioContext {
  if (!sharedContext || sharedContext.state === "closed") {
    sharedContext = new AudioContext();
  }
  return sharedContext;
}

export async function resumeAudioContext(): Promise<AudioContext> {
  const context = getAudioContext();
  if (context.state === "suspended") await context.resume();
  return context;
}

/**
 * The first MediaRecorder type this browser supports, in the order the
 * backend's transcription provider handles best. Chrome/Edge/Firefox give
 * webm/opus, Safari gives mp4.
 */
export function pickRecorderMimeType(): string | null {
  if (typeof MediaRecorder === "undefined") return null;
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

export function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/** Root-mean-square level of the analyser's current window, 0..1. */
export function readLevel(analyser: AnalyserNode, buffer: Float32Array<ArrayBuffer>): number {
  analyser.getFloatTimeDomainData(buffer);
  let sum = 0;
  for (let i = 0; i < buffer.length; i += 1) sum += buffer[i] * buffer[i];
  return Math.sqrt(sum / buffer.length);
}

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

export function microphoneErrorMessage(err: unknown): string {
  const name = err instanceof DOMException ? err.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Microphone access was blocked. Allow it from the address bar's site settings, then try again — or type your turns instead.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone was found. Connect one and try again, or type your turns instead.";
    case "NotReadableError":
      return "The microphone is in use by another app. Close it and try again, or type your turns instead.";
    default:
      return "The microphone could not be started. You can still type your turns.";
  }
}

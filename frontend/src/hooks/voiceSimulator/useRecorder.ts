import { useCallback, useMemo, useRef } from "react";
import { pickRecorderMimeType } from "../../lib/voiceSimulator/audio";

export interface Recording {
  blob: Blob;
  /** Wall time the recorder ran, in ms. */
  durationMs: number;
}

/**
 * MediaRecorder wrapper for one utterance at a time. The blob lives only in
 * memory until it is uploaded; nothing is written to disk or storage.
 */
export function useRecorder(stream: MediaStream | null) {
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedRef = useRef(0);

  const isRecording = useCallback(() => recorderRef.current?.state === "recording", []);

  const start = useCallback((): boolean => {
    if (!stream || recorderRef.current?.state === "recording") return false;
    const mimeType = pickRecorderMimeType();
    if (mimeType === null) return false;
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunksRef.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.start();
    recorderRef.current = recorder;
    startedRef.current = performance.now();
    return true;
  }, [stream]);

  const stop = useCallback((): Promise<Recording | null> => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") return Promise.resolve(null);
    return new Promise((resolve) => {
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        chunksRef.current = [];
        recorderRef.current = null;
        resolve({ blob, durationMs: performance.now() - startedRef.current });
      };
      recorder.stop();
    });
  }, []);

  /** Stop and throw the audio away (hang-up, mode switch). */
  const cancel = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      recorder.stop();
    }
    chunksRef.current = [];
    recorderRef.current = null;
  }, []);

  // Stable identity: consumers use the recorder in effect dependencies.
  return useMemo(() => ({ start, stop, cancel, isRecording }), [start, stop, cancel, isRecording]);
}

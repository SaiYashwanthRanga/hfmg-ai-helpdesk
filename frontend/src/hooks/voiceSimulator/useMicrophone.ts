import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getAudioContext, microphoneErrorMessage } from "../../lib/voiceSimulator/audio";

export type MicrophoneStatus = "off" | "requesting" | "on" | "error" | "unsupported";

export interface MicrophoneDevice {
  deviceId: string;
  label: string;
}

/**
 * Owns the mic stream and an AnalyserNode on it (for the level meter,
 * waveform and voice-activity detection). Mute disables the track rather
 * than stopping it, so unmuting is instant and needs no new permission.
 */
/** False on insecure origins (plain http other than localhost) and very old browsers. */
function canRecord(): boolean {
  return typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getUserMedia === "function";
}

async function listInputDevices(): Promise<MicrophoneDevice[]> {
  if (typeof navigator.mediaDevices?.enumerateDevices !== "function") return [];
  const all = await navigator.mediaDevices.enumerateDevices();
  return all
    .filter((d) => d.kind === "audioinput")
    .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Microphone ${i + 1}` }));
}

export function useMicrophone() {
  const [status, setStatus] = useState<MicrophoneStatus>(() => (canRecord() ? "off" : "unsupported"));
  const [error, setError] = useState<string | null>(null);
  const [muted, setMutedState] = useState(false);
  const [devices, setDevices] = useState<MicrophoneDevice[]>([]);
  const [deviceId, setDeviceId] = useState<string>("");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const refreshDevices = useCallback(() => {
    listInputDevices().then(setDevices, () => undefined);
  }, []);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    sourceRef.current?.disconnect();
    streamRef.current = null;
    sourceRef.current = null;
    setStream(null);
    setAnalyser(null);
    setStatus((current) => (current === "unsupported" ? current : "off"));
  }, []);

  const start = useCallback(
    async (requestedDeviceId?: string): Promise<boolean> => {
      if (!canRecord()) {
        setStatus("unsupported");
        setError("This browser can't record audio here (microphones need HTTPS or localhost). Type your turns instead.");
        return false;
      }
      stop();
      setStatus("requesting");
      setError(null);
      try {
        const id = requestedDeviceId ?? deviceId;
        const media = await navigator.mediaDevices.getUserMedia({
          audio: {
            deviceId: id ? { exact: id } : undefined,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        const context = getAudioContext();
        const source = context.createMediaStreamSource(media);
        const node = context.createAnalyser();
        node.fftSize = 2048;
        node.smoothingTimeConstant = 0.6;
        source.connect(node);
        media.getAudioTracks().forEach((track) => {
          track.enabled = !muted;
        });
        streamRef.current = media;
        sourceRef.current = source;
        setStream(media);
        setAnalyser(node);
        setStatus("on");
        // Labels are only readable once permission is granted.
        void refreshDevices();
        return true;
      } catch (err) {
        setStatus("error");
        setError(microphoneErrorMessage(err));
        return false;
      }
    },
    [deviceId, muted, refreshDevices, stop],
  );

  const setMuted = useCallback((next: boolean) => {
    setMutedState(next);
    streamRef.current?.getAudioTracks().forEach((track) => {
      track.enabled = !next;
    });
  }, []);

  const selectDevice = useCallback(
    async (id: string) => {
      setDeviceId(id);
      if (streamRef.current) await start(id);
    },
    [start],
  );

  useEffect(() => {
    let cancelled = false;
    listInputDevices().then(
      (list) => {
        if (!cancelled) setDevices(list);
      },
      () => undefined,
    );
    navigator.mediaDevices?.addEventListener?.("devicechange", refreshDevices);
    return () => {
      cancelled = true;
      navigator.mediaDevices?.removeEventListener?.("devicechange", refreshDevices);
    };
  }, [refreshDevices]);

  useEffect(() => () => stop(), [stop]);

  return useMemo(
    () => ({ status, error, muted, setMuted, devices, deviceId, selectDevice, stream, analyser, start, stop }),
    [status, error, muted, setMuted, devices, deviceId, selectDevice, stream, analyser, start, stop],
  );
}

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { clearApiCallLog, simulatorApi, speechUrl } from "../../api/voiceSimulator";
import { resumeAudioContext } from "../../lib/voiceSimulator/audio";
import { initialSimulatorState, isCallActive, simulatorReducer } from "../../lib/voiceSimulator/sessionReducer";
import type { PendingTurn } from "../../lib/voiceSimulator/sessionReducer";
import type { AgentReply, ListenMode, MockCaller } from "../../types/voiceSimulator";
import { browserVoiceAvailable, useAudioPlayer } from "./useAudioPlayer";
import { useMicrophone } from "./useMicrophone";
import { useRecorder } from "./useRecorder";
import type { Recording } from "./useRecorder";
import { DEFAULT_VAD, useVoiceActivity } from "./useVoiceActivity";
import type { VadSettings } from "./useVoiceActivity";

export type VoiceEngine = "server" | "browser";

export interface StartOptions {
  tts: boolean;
  /**
   * "server": OpenAI TTS streamed from the backend (natural voice, ~1-2 s to
   * first audio). "browser": the browser's built-in voice (robotic, but
   * starts almost instantly). Production uses neither -- the SIP gateway speaks with its own TTS.
   */
  voice: VoiceEngine;
  useMicrophone: boolean;
  label: string;
  callerId: string;
  sendNotifications: boolean;
}

/** Per-turn browser timings, keyed by turn_client_id until reported. */
interface TurnClock {
  /** performance.now() when the caller stopped talking (or pressed Send). */
  speechEndedAt: number;
  utteranceMs: number | null;
  captureMs: number | null;
}

/** The gather timeout (settings.voice_gather_timeout), for the optional phone-like silence rule. */
const PHONE_SILENCE_TIMEOUT_MS = 6000;
/** Shorter than this and a push-to-talk press is treated as an accidental tap. */
const MIN_PTT_MS = 250;
/** Continuous mode restarts the recorder if nobody speaks for this long, to bound the blob. */
const MAX_IDLE_RECORDING_MS = 30_000;
const SUMMARY_POLL_MS = 3000;
const SUMMARY_POLL_LIMIT = 20;

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.status ? `${err.message} (HTTP ${err.status})` : err.message;
  // fetch() rejects with a bare TypeError when there is no readable response:
  // the server is down, or it crashed with a 500 that carried no CORS headers.
  if (err instanceof TypeError) {
    return "No response from the server — it may be down, or it hit an unhandled error. Check the backend terminal for a traceback.";
  }
  if (err instanceof Error) return err.message;
  return "Something went wrong";
}

function nowIso() {
  return new Date().toISOString();
}

/**
 * Drives one simulated call: the reducer (sessionReducer.ts) owns what state
 * the call is in; this hook owns the side effects — microphone, recorder,
 * voice-activity detection, API calls, playback and timing capture.
 */
export function useSimulatorSession() {
  const [state, dispatch] = useReducer(simulatorReducer, initialSimulatorState);
  const mic = useMicrophone();
  const recorder = useRecorder(mic.stream);
  const player = useAudioPlayer();

  const [listenMode, setListenMode] = useState<ListenMode>("push_to_talk");
  const [vad, setVad] = useState<VadSettings>(DEFAULT_VAD);
  const [phoneSilenceTimeout, setPhoneSilenceTimeout] = useState(false);
  const [pushToTalkActive, setPushToTalkActive] = useState(false);
  const [autopilot, setAutopilot] = useState<MockCaller | null>(null);
  // A mock caller only ever drives a live call; once the call is over it is inert.
  const activeAutopilot = state.status === "disconnected" || state.status === "idle" ? null : autopilot;

  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const clocks = useRef(new Map<string, TurnClock>());
  /** Which voice this call uses; fixed at Start. */
  const voiceRef = useRef<VoiceEngine | "off">("off");
  /** Audio of the last failed voice turn, kept only so Retry can re-upload it. */
  const failedRecording = useRef<{ turnClientId: string; recording: Recording } | null>(null);
  const heardSpeech = useRef(false);

  // --- reporting ----------------------------------------------------------

  const reportClientMetrics = useCallback(
    (sessionId: string, turnClientId: string, extra: { playbackStartMs?: number; playbackDurationMs?: number; firstOutputAt: number }) => {
      const clock = clocks.current.get(turnClientId);
      clocks.current.delete(turnClientId);
      void simulatorApi
        .clientMetrics(sessionId, {
          turn_client_id: turnClientId,
          utterance_ms: clock?.utteranceMs ?? null,
          capture_ms: clock?.captureMs ?? null,
          playback_start_ms: extra.playbackStartMs ?? null,
          playback_duration_ms: extra.playbackDurationMs ?? null,
          turn_total_ms: clock ? Math.max(0, extra.firstOutputAt - clock.speechEndedAt) : null,
        })
        .then(() => dispatch({ type: "METRICS_CHANGED" }))
        .catch(() => undefined); // metrics are best-effort; never interrupt the call
    },
    [],
  );

  /** Whether this reply will be heard: streamed server speech, or the browser voice. */
  const willSpeak = useCallback(
    (reply: AgentReply) =>
      reply.speech_path !== null || (voiceRef.current === "browser" && browserVoiceAvailable() && reply.text.length > 0),
    [],
  );

  const playReply = useCallback(
    async (sessionId: string, turnClientId: string, reply: AgentReply, receivedAt: number) => {
      let startMs: number | undefined;
      let firstFrameAt = receivedAt;
      const onFirstFrame = (t: { startMs: number; firstFrameAt: number }) => {
        startMs = t.startMs;
        firstFrameAt = t.firstFrameAt;
      };
      const timing = reply.speech_path
        ? await player.play(speechUrl(reply.speech_path), receivedAt, onFirstFrame)
        : await player.speak(reply.text, receivedAt, onFirstFrame);
      if (timing === null && startMs === undefined) {
        dispatch({ type: "NOTE", text: "The reply audio could not be played; showing text only.", tone: "warning", at: nowIso() });
      }
      reportClientMetrics(sessionId, turnClientId, {
        playbackStartMs: startMs,
        playbackDurationMs: timing?.durationMs,
        firstOutputAt: firstFrameAt,
      });
      dispatch({ type: "PLAYBACK_ENDED" });
    },
    [player, reportClientMetrics],
  );

  // --- a turn -------------------------------------------------------------

  const runTurn = useCallback(
    async (turn: PendingTurn, recording: Recording | null) => {
      const session = stateRef.current.session;
      if (!session) return;
      const sessionId = session.id;
      try {
        if (recording) {
          const clock = clocks.current.get(turn.turnClientId);
          if (clock) clock.captureMs = Math.max(0, performance.now() - clock.speechEndedAt);
          const transcript = await simulatorApi.audio(sessionId, turn.turnClientId, recording.blob);
          dispatch({ type: "TRANSCRIBED", turnClientId: turn.turnClientId, text: transcript.transcript });
        }
        const response = await simulatorApi.process({
          session_id: sessionId,
          turn_client_id: turn.turnClientId,
          utterance: turn.utterance,
          input_mode: turn.inputMode,
        });
        const receivedAt = performance.now();
        failedRecording.current = null;
        const speaks = willSpeak(response.reply);
        dispatch({ type: "TURN_SUCCEEDED", response, willPlay: speaks, at: nowIso() });
        if (speaks) {
          await playReply(sessionId, turn.turnClientId, response.reply, receivedAt);
        } else {
          reportClientMetrics(sessionId, turn.turnClientId, { firstOutputAt: receivedAt });
        }
      } catch (err) {
        if (recording) failedRecording.current = { turnClientId: turn.turnClientId, recording };
        dispatch({ type: "TURN_FAILED", turnClientId: turn.turnClientId, message: errorMessage(err), at: nowIso() });
      }
    },
    [playReply, reportClientMetrics, willSpeak],
  );

  const submitText = useCallback(
    (text: string, inputMode: "text" | "mock" = "text") => {
      if (stateRef.current.status !== "listening") return false;
      const turn: PendingTurn = { turnClientId: crypto.randomUUID(), utterance: text, inputMode };
      clocks.current.set(turn.turnClientId, { speechEndedAt: performance.now(), utteranceMs: null, captureMs: null });
      recorder.cancel();
      dispatch({ type: "TURN_SUBMITTED", turn, at: nowIso() });
      void runTurn(turn, null);
      return true;
    },
    [recorder, runTurn],
  );

  const submitRecording = useCallback(
    (recording: Recording, speechEndedAt: number) => {
      if (stateRef.current.status !== "listening") return;
      const turn: PendingTurn = { turnClientId: crypto.randomUUID(), utterance: null, inputMode: "voice" };
      clocks.current.set(turn.turnClientId, { speechEndedAt, utteranceMs: Math.round(recording.durationMs), captureMs: null });
      dispatch({ type: "TURN_SUBMITTED", turn, at: nowIso() });
      void runTurn(turn, recording);
    },
    [runTurn],
  );

  const retry = useCallback(() => {
    const failed = stateRef.current.failed;
    if (!failed) return;
    const recording =
      failedRecording.current?.turnClientId === failed.turnClientId ? failedRecording.current.recording : null;
    const clock = clocks.current.get(failed.turnClientId);
    if (clock) clock.speechEndedAt = performance.now();
    dispatch({ type: "TURN_SUBMITTED", turn: failed, at: nowIso() });
    void runTurn(failed, recording);
  }, [runTurn]);

  const dismissError = useCallback(() => dispatch({ type: "ERROR_DISMISSED" }), []);

  // --- call lifecycle -----------------------------------------------------

  const start = useCallback(
    async (options: StartOptions) => {
      if (isCallActive(stateRef.current) || stateRef.current.status === "connecting") return;
      // Created/resumed inside the click handler so autoplay rules allow playback.
      await resumeAudioContext().catch(() => undefined);
      if (options.useMicrophone) {
        const ok = await mic.start();
        if (!ok) {
          dispatch({ type: "NOTE", text: "Microphone unavailable — continuing with typed input.", tone: "warning", at: nowIso() });
        }
      }
      clocks.current.clear();
      failedRecording.current = null;
      setAutopilot(null);
      voiceRef.current = options.tts ? options.voice : "off";
      dispatch({ type: "START_REQUESTED" });
      const requestedAt = performance.now();
      try {
        const response = await simulatorApi.start({
          // The browser voice needs no server speech at all.
          tts: options.tts && options.voice === "server",
          label: options.label.trim() || null,
          caller_id: options.callerId.trim() || null,
          send_notifications: options.sendNotifications,
        });
        const receivedAt = performance.now();
        const speaks = willSpeak(response.greeting);
        clocks.current.set(response.turn.turn_client_id, { speechEndedAt: requestedAt, utteranceMs: null, captureMs: null });
        dispatch({ type: "START_SUCCEEDED", response, willPlay: speaks, at: nowIso() });
        if (speaks) {
          await playReply(response.session.id, response.turn.turn_client_id, response.greeting, receivedAt);
        } else {
          reportClientMetrics(response.session.id, response.turn.turn_client_id, { firstOutputAt: receivedAt });
        }
      } catch (err) {
        mic.stop();
        dispatch({ type: "START_FAILED", message: errorMessage(err), at: nowIso() });
      }
    },
    [mic, playReply, reportClientMetrics, willSpeak],
  );

  const hangUp = useCallback(async () => {
    const session = stateRef.current.session;
    if (!session || session.ended_at) return;
    setAutopilot(null);
    recorder.cancel();
    player.stop();
    try {
      const ended = await simulatorApi.end(session.id, "user_hangup");
      dispatch({ type: "ENDED", session: ended, at: nowIso(), note: "You hung up." });
    } catch (err) {
      dispatch({ type: "ENDED", session: null, at: nowIso(), note: `Hang-up could not be confirmed: ${errorMessage(err)}` });
    }
  }, [player, recorder]);

  const clear = useCallback(async () => {
    const session = stateRef.current.session;
    setAutopilot(null);
    recorder.cancel();
    player.stop();
    mic.stop();
    if (session && !session.ended_at) {
      await simulatorApi.end(session.id, "cleared").catch(() => undefined);
    }
    clocks.current.clear();
    failedRecording.current = null;
    clearApiCallLog();
    dispatch({ type: "CLEARED" });
  }, [mic, player, recorder]);

  // --- push-to-talk -------------------------------------------------------

  const pushToTalkDown = useCallback(() => {
    if (stateRef.current.status !== "listening" || mic.status !== "on" || mic.muted) return;
    if (recorder.start()) setPushToTalkActive(true);
  }, [mic.muted, mic.status, recorder]);

  const pushToTalkUp = useCallback(async () => {
    if (!recorder.isRecording()) {
      setPushToTalkActive(false);
      return;
    }
    const releasedAt = performance.now();
    const recording = await recorder.stop();
    setPushToTalkActive(false);
    if (!recording) return;
    if (recording.durationMs < MIN_PTT_MS) {
      dispatch({ type: "NOTE", text: "Hold to talk — that press was too short to record.", tone: "info", at: nowIso() });
      return;
    }
    submitRecording(recording, releasedAt);
  }, [recorder, submitRecording]);

  // --- continuous listening -----------------------------------------------

  const continuousActive =
    listenMode === "continuous" && state.status === "listening" && mic.status === "on" && !mic.muted && activeAutopilot === null;

  useEffect(() => {
    if (!continuousActive) {
      if (listenMode === "continuous") recorder.cancel();
      return;
    }
    heardSpeech.current = false;
    recorder.start();
    const listeningSince = performance.now();
    const timer = window.setInterval(() => {
      if (heardSpeech.current) return;
      const idle = performance.now() - listeningSince;
      if (phoneSilenceTimeout && idle >= PHONE_SILENCE_TIMEOUT_MS) {
        // Mirrors the gateway: a caller who says nothing is re-prompted, and it counts.
        window.clearInterval(timer);
        submitText("", "text");
      } else if (idle >= MAX_IDLE_RECORDING_MS && recorder.isRecording()) {
        recorder.cancel();
        recorder.start();
      }
    }, 500);
    return () => {
      window.clearInterval(timer);
      recorder.cancel();
    };
  }, [continuousActive, listenMode, phoneSilenceTimeout, recorder, submitText]);

  const onSpeechStart = useCallback(() => {
    heardSpeech.current = true;
  }, []);

  const onSpeechEnd = useCallback(
    async (speechEndedAt: number) => {
      if (!heardSpeech.current || stateRef.current.status !== "listening") return;
      const recording = await recorder.stop();
      if (recording) submitRecording(recording, speechEndedAt);
    },
    [recorder, submitRecording],
  );

  useVoiceActivity({ analyser: mic.analyser, enabled: continuousActive, settings: vad, onSpeechStart, onSpeechEnd });

  // --- mock caller autopilot ----------------------------------------------

  useEffect(() => {
    if (!activeAutopilot || state.status !== "listening" || !state.session) return;
    const answer = activeAutopilot.answers[state.session.state];
    const timer = window.setTimeout(() => {
      // No scripted answer for this question: hand control back to the tester.
      if (answer === undefined) setAutopilot(null);
      else submitText(answer, "mock");
    }, 700);
    return () => window.clearTimeout(timer);
  }, [activeAutopilot, state.status, state.session, submitText]);

  // --- release the mic when the call is over ------------------------------

  const stopMic = mic.stop;
  const cancelRecording = recorder.cancel;
  useEffect(() => {
    if (state.status === "disconnected") {
      cancelRecording();
      stopMic();
    }
  }, [state.status, stopMic, cancelRecording]);

  // --- AI summary lands after the ticket; poll briefly for it -------------

  const sessionId = state.session?.id ?? null;
  const summaryPending = state.session?.ticket?.ai_summary_status === "PENDING";
  useEffect(() => {
    if (!sessionId || !summaryPending) return;
    let polls = 0;
    const timer = window.setInterval(async () => {
      polls += 1;
      try {
        const detail = await simulatorApi.session(sessionId);
        dispatch({ type: "SESSION_REFRESHED", session: detail.session });
        if (detail.session.ticket?.ai_summary_status !== "PENDING") window.clearInterval(timer);
      } catch {
        // transient; try again next tick
      }
      if (polls >= SUMMARY_POLL_LIMIT) window.clearInterval(timer);
    }, SUMMARY_POLL_MS);
    return () => window.clearInterval(timer);
  }, [sessionId, summaryPending]);

  // --- leaving the page hangs up ------------------------------------------

  useEffect(() => {
    const onUnload = () => {
      const session = stateRef.current.session;
      if (session && !session.ended_at) simulatorApi.endOnUnload(session.id);
    };
    window.addEventListener("pagehide", onUnload);
    return () => {
      window.removeEventListener("pagehide", onUnload);
      onUnload();
    };
  }, []);

  return {
    state,
    mic,
    speakerAnalyser: player.analyser,
    listenMode,
    setListenMode,
    vad,
    setVad,
    phoneSilenceTimeout,
    setPhoneSilenceTimeout,
    pushToTalkActive,
    autopilot: activeAutopilot,
    setAutopilot,
    start,
    hangUp,
    clear,
    submitText,
    retry,
    dismissError,
    pushToTalkDown,
    pushToTalkUp,
    isActive: isCallActive(state),
  };
}

export type SimulatorController = ReturnType<typeof useSimulatorSession>;

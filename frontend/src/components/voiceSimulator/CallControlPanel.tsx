import { Mic, MicOff, Phone, PhoneOff, RotateCcw, Settings2 } from "lucide-react";
import { useState } from "react";
import { cn } from "../../lib/cn";
import { browserVoiceAvailable } from "../../hooks/voiceSimulator/useAudioPlayer";
import type { SimulatorController, StartOptions } from "../../hooks/voiceSimulator/useSimulatorSession";
import type { ListenMode, SimulatorConfig } from "../../types/voiceSimulator";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { LevelMeter } from "./LevelMeter";
import { WaveformCanvas } from "./WaveformCanvas";

export interface CallControlPanelProps {
  sim: SimulatorController;
  config: SimulatorConfig | undefined;
  startOptions: StartOptions;
  onStartOptionsChange: (options: StartOptions) => void;
  onStart: () => void;
  onRequestClear: () => void;
}

const MODES: Array<{ value: ListenMode; label: string; hint: string }> = [
  { value: "push_to_talk", label: "Push-to-talk", hint: "Hold the button or Space while you speak." },
  { value: "continuous", label: "Continuous", hint: "Just talk; a pause of about a second ends your turn." },
];

function Toggle({ id, label, checked, disabled, onChange, hint }: {
  id: string;
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
  hint?: string;
}) {
  return (
    <label htmlFor={id} className={cn("flex items-start gap-2 text-xs text-stone-700", disabled && "opacity-50")}>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 size-4 rounded border-stone-300 accent-emerald-800"
      />
      <span>
        <span className="font-medium">{label}</span>
        {hint ? <span className="block text-[11px] text-stone-500">{hint}</span> : null}
      </span>
    </label>
  );
}

/**
 * The phone itself: call options before dialling, then call controls,
 * listening mode, push-to-talk, waveform and level meters.
 */
export function CallControlPanel({ sim, config, startOptions, onStartOptionsChange, onStart, onRequestClear }: CallControlPanelProps) {
  const { state, mic } = sim;
  const [showAdvanced, setShowAdvanced] = useState(false);
  const inCall = sim.isActive;
  const canStart = !inCall && state.status !== "connecting";
  const speechAvailable = config?.speech_configured ?? false;
  const browserVoice = browserVoiceAvailable();
  const micUsable = mic.status === "on";
  const pttEnabled = inCall && micUsable && !mic.muted && state.status === "listening" && sim.listenMode === "push_to_talk" && !sim.autopilot;
  const waveformSource = state.status === "speaking" ? "speaker" : micUsable ? "mic" : "none";
  const set = (patch: Partial<StartOptions>) => onStartOptionsChange({ ...startOptions, ...patch });

  return (
    <Card padding="none" className="flex flex-col">
      <div className="flex flex-col items-center gap-4 border-b border-stone-100 p-5">
        <div className="flex flex-wrap items-center justify-center gap-2">
          {inCall ? (
            <Button variant="danger" size="lg" onClick={() => void sim.hangUp()} icon={<PhoneOff className="size-5" aria-hidden="true" />}>
              Stop call
            </Button>
          ) : (
            <Button
              size="lg"
              onClick={onStart}
              disabled={!canStart}
              loading={state.status === "connecting"}
              icon={<Phone className="size-5" aria-hidden="true" />}
            >
              Start call
            </Button>
          )}
          <Button
            variant="secondary"
            size="lg"
            onClick={() => mic.setMuted(!mic.muted)}
            disabled={!micUsable}
            aria-pressed={mic.muted}
            icon={mic.muted ? <MicOff className="size-5" aria-hidden="true" /> : <Mic className="size-5" aria-hidden="true" />}
          >
            {mic.muted ? "Unmute" : "Mute"}
          </Button>
          <Button
            variant="ghost"
            size="lg"
            onClick={onRequestClear}
            disabled={state.status === "idle" && state.timeline.length === 0}
            icon={<RotateCcw className="size-5" aria-hidden="true" />}
          >
            Clear
          </Button>
        </div>

        {state.status === "error" ? (
          <div role="alert" className="flex w-full flex-col gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
            <p>{state.error}</p>
            <div className="flex gap-2">
              {state.failed ? (
                <Button size="xs" variant="secondary" onClick={sim.retry}>
                  Retry turn
                </Button>
              ) : null}
              <Button size="xs" variant="ghost" onClick={sim.dismissError}>
                Dismiss
              </Button>
            </div>
          </div>
        ) : null}

        {mic.error && inCall ? (
          <p role="alert" className="w-full rounded-lg bg-amber-50 p-3 text-xs text-amber-800">
            {mic.error}
          </p>
        ) : null}
      </div>

      {!inCall ? (
        <fieldset className="flex flex-col gap-3 border-b border-stone-100 p-5">
          <legend className="sr-only">Call options</legend>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-xs font-medium text-stone-700">
              Session label
              <input
                value={startOptions.label}
                onChange={(event) => set({ label: event.target.value })}
                maxLength={200}
                placeholder="e.g. QA: printer happy path"
                className="h-9 rounded-lg border border-stone-200/80 bg-stone-50/50 px-3 text-sm font-normal focus:border-emerald-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-emerald-800"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium text-stone-700">
              Simulated caller ID
              <input
                value={startOptions.callerId}
                onChange={(event) => set({ callerId: event.target.value })}
                maxLength={32}
                inputMode="tel"
                placeholder="Blank = withheld (agent asks)"
                className="h-9 rounded-lg border border-stone-200/80 bg-stone-50/50 px-3 text-sm font-normal focus:border-emerald-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-emerald-800"
              />
            </label>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Toggle
              id="sim-use-mic"
              label="Use microphone"
              checked={startOptions.useMicrophone && speechAvailable}
              disabled={!speechAvailable}
              onChange={(v) => set({ useMicrophone: v })}
              hint={speechAvailable ? undefined : "Speech-to-text isn't configured on the server."}
            />
            <Toggle
              id="sim-tts"
              label="Speak replies"
              checked={startOptions.tts && (speechAvailable || browserVoice)}
              disabled={!speechAvailable && !browserVoice}
              onChange={(v) => set({ tts: v })}
            />
            <Toggle
              id="sim-notify"
              label="Send ticket email"
              checked={startOptions.sendNotifications && Boolean(config?.notifications_allowed)}
              disabled={!config?.notifications_allowed}
              onChange={(v) => set({ sendNotifications: v })}
              hint={
                config?.notifications_allowed
                  ? "Off by default — test tickets shouldn't email the help desk."
                  : "Disabled on this server (SIMULATOR_ALLOW_NOTIFICATIONS=false)."
              }
            />
          </div>
          {startOptions.tts ? (
            <div role="radiogroup" aria-label="Agent voice" className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-stone-700">Agent voice</span>
              {(
                [
                  {
                    value: "server",
                    label: "Natural (OpenAI, streamed)",
                    hint: "About 1–2 s before the reply starts.",
                    disabled: !speechAvailable,
                  },
                  {
                    value: "browser",
                    label: "Instant (browser voice)",
                    hint: "Starts immediately; robotic, and not shown on the speaker meter.",
                    disabled: !browserVoice,
                  },
                ] as const
              ).map((option) => (
                <label key={option.value} className={cn("flex items-start gap-2 text-xs text-stone-700", option.disabled && "opacity-50")}>
                  <input
                    type="radio"
                    name="sim-voice"
                    value={option.value}
                    checked={startOptions.voice === option.value}
                    disabled={option.disabled}
                    onChange={() => set({ voice: option.value })}
                    className="mt-0.5 accent-emerald-800"
                  />
                  <span>
                    <span className="font-medium">{option.label}</span>
                    <span className="block text-[11px] text-stone-500">{option.hint}</span>
                  </span>
                </label>
              ))}
              <span className="text-[11px] text-stone-500">Real phone calls use the SIP gateway's voice, not either of these.</span>
            </div>
          ) : null}
        </fieldset>
      ) : null}

      <div className="flex flex-col gap-4 p-5">
        <div role="radiogroup" aria-label="Listening mode" className="grid grid-cols-2 gap-1 rounded-lg bg-stone-100 p-1">
          {MODES.map((mode) => (
            <button
              key={mode.value}
              type="button"
              role="radio"
              aria-checked={sim.listenMode === mode.value}
              title={mode.hint}
              onClick={() => sim.setListenMode(mode.value)}
              className={cn(
                "h-8 rounded-md text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800",
                sim.listenMode === mode.value ? "bg-white text-stone-900 shadow-2xs" : "text-stone-500 hover:text-stone-800",
              )}
            >
              {mode.label}
            </button>
          ))}
        </div>

        {sim.listenMode === "push_to_talk" ? (
          <button
            type="button"
            disabled={!pttEnabled && !sim.pushToTalkActive}
            aria-pressed={sim.pushToTalkActive}
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              sim.pushToTalkDown();
            }}
            onPointerUp={() => void sim.pushToTalkUp()}
            onPointerCancel={() => void sim.pushToTalkUp()}
            onKeyDown={(event) => {
              if ((event.key === "Enter" || event.key === " ") && !event.repeat) {
                event.preventDefault();
                sim.pushToTalkDown();
              }
            }}
            onKeyUp={(event) => {
              if (event.key === "Enter" || event.key === " ") void sim.pushToTalkUp();
            }}
            className={cn(
              "flex h-24 w-full select-none flex-col items-center justify-center gap-1 rounded-2xl border-2 text-sm font-semibold transition-all touch-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40",
              sim.pushToTalkActive
                ? "scale-[0.98] border-emerald-700 bg-emerald-700 text-white shadow-inner"
                : "border-stone-200 bg-stone-50 text-stone-700 hover:border-emerald-700",
            )}
          >
            <Mic className="size-6" aria-hidden="true" />
            {sim.pushToTalkActive ? "Recording — release to send" : "Hold to talk"}
            <span className="text-[11px] font-normal opacity-80">or hold Space</span>
          </button>
        ) : (
          <div className="flex h-24 flex-col items-center justify-center gap-1 rounded-2xl border-2 border-dashed border-stone-200 text-center text-xs text-stone-600">
            <p className="font-semibold">
              {state.status === "listening" && micUsable && !mic.muted ? "Listening — just talk" : "Continuous listening"}
            </p>
            <p className="text-[11px] text-stone-500">A pause of {(sim.vad.silenceMs / 1000).toFixed(1)}s ends your turn. The mic is paused while the agent speaks.</p>
          </div>
        )}

        <WaveformCanvas analyser={waveformSource === "speaker" ? sim.speakerAnalyser : waveformSource === "mic" ? mic.analyser : null} source={waveformSource} />

        <div className="flex flex-col gap-2">
          <LevelMeter label="Mic" analyser={micUsable && !mic.muted ? mic.analyser : null} inactiveLabel={mic.muted ? "Muted" : "Off"} />
          <LevelMeter label="Speaker" analyser={sim.speakerAnalyser} />
        </div>

        <div>
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            aria-expanded={showAdvanced}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-stone-500 hover:text-stone-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800"
          >
            <Settings2 className="size-3.5" aria-hidden="true" />
            Audio settings
          </button>
          {showAdvanced ? (
            <div className="mt-3 flex flex-col gap-3 rounded-lg bg-stone-50 p-3">
              <label className="flex flex-col gap-1 text-xs font-medium text-stone-700">
                Microphone
                <select
                  value={mic.deviceId}
                  onChange={(event) => void mic.selectDevice(event.target.value)}
                  className="h-9 rounded-lg border border-stone-200/80 bg-white px-2 text-sm font-normal focus:outline-none focus:ring-1 focus:ring-emerald-800"
                >
                  <option value="">System default</option>
                  {mic.devices.map((device) => (
                    <option key={device.deviceId} value={device.deviceId}>
                      {device.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs font-medium text-stone-700">
                Speech threshold ({sim.vad.threshold.toFixed(3)})
                <input
                  type="range"
                  min={0.005}
                  max={0.1}
                  step={0.005}
                  value={sim.vad.threshold}
                  onChange={(event) => sim.setVad({ ...sim.vad, threshold: Number(event.target.value) })}
                  className="accent-emerald-800"
                />
                <span className="text-[11px] font-normal text-stone-500">Raise it in a noisy room if background sound starts turns.</span>
              </label>
              <label className="flex flex-col gap-1 text-xs font-medium text-stone-700">
                End-of-turn pause ({sim.vad.silenceMs} ms)
                <input
                  type="range"
                  min={400}
                  max={2500}
                  step={100}
                  value={sim.vad.silenceMs}
                  onChange={(event) => sim.setVad({ ...sim.vad, silenceMs: Number(event.target.value) })}
                  className="accent-emerald-800"
                />
              </label>
              <Toggle
                id="sim-silence-timeout"
                label="Phone-style silence timeout"
                checked={sim.phoneSilenceTimeout}
                onChange={sim.setPhoneSilenceTimeout}
                hint="Continuous mode: saying nothing for 6s counts as a missed answer, like on the phone."
              />
            </div>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

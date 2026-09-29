import { useEffect, useRef, useState } from "react";
import { prefersReducedMotion, readLevel } from "../../lib/voiceSimulator/audio";

export interface LevelMeterProps {
  label: string;
  analyser: AnalyserNode | null;
  /** Shown instead of the bar when there is nothing to meter. */
  inactiveLabel?: string;
}

const SEGMENTS = 20;
/** Speech rarely exceeds ~0.3 RMS; scale so normal talking fills most of the bar. */
const FULL_SCALE = 0.3;

/**
 * Segmented level meter. Drawn by direct DOM updates in an animation frame
 * loop — never React state per frame — and exposed to assistive tech as a
 * `meter` whose value is updated a few times a second.
 */
export function LevelMeter({ label, analyser, inactiveLabel = "Off" }: LevelMeterProps) {
  const barRef = useRef<HTMLDivElement>(null);
  const [ariaValue, setAriaValue] = useState(0);

  useEffect(() => {
    const bar = barRef.current;
    if (!analyser || !bar) {
      if (bar) bar.style.width = "0%";
      return;
    }
    const buffer = new Float32Array(analyser.fftSize);
    let frame = 0;
    let lastAria = 0;
    const reduced = prefersReducedMotion();

    const tick = (time: number) => {
      const level = Math.min(1, readLevel(analyser, buffer) / FULL_SCALE);
      const segments = Math.round(level * SEGMENTS);
      bar.style.width = `${(segments / SEGMENTS) * 100}%`;
      bar.dataset.level = level > 0.85 ? "hot" : level > 0.05 ? "on" : "off";
      if (time - lastAria > 250) {
        lastAria = time;
        setAriaValue(Math.round(level * 100));
      }
      frame = reduced ? window.setTimeout(() => tick(performance.now()), 200) : requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(frame);
    };
  }, [analyser]);

  return (
    <div className="flex items-center gap-3">
      <span className="w-16 shrink-0 text-xs font-medium text-stone-600">{label}</span>
      <div
        role="meter"
        aria-label={`${label} level`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={analyser ? ariaValue : 0}
        aria-valuetext={analyser ? `${ariaValue} percent` : inactiveLabel}
        className="relative h-2.5 flex-1 overflow-hidden rounded-full bg-stone-100"
        style={{
          backgroundImage: `repeating-linear-gradient(90deg, transparent 0 calc(${100 / SEGMENTS}% - 2px), white calc(${100 / SEGMENTS}% - 2px) ${100 / SEGMENTS}%)`,
        }}
      >
        <div
          ref={barRef}
          className="h-full rounded-full bg-emerald-600 transition-[width] duration-75 data-[level=hot]:bg-amber-500"
          style={{ width: "0%" }}
        />
      </div>
      {!analyser ? <span className="w-8 text-right text-[11px] text-stone-400">{inactiveLabel}</span> : <span className="w-8" />}
    </div>
  );
}

import { useEffect, useRef } from "react";
import { prefersReducedMotion } from "../../lib/voiceSimulator/audio";

export interface WaveformCanvasProps {
  analyser: AnalyserNode | null;
  /** Which source is being drawn, for the caption. */
  source: "mic" | "speaker" | "none";
}

/**
 * Live oscilloscope of whichever side is talking. Decorative (the level
 * meters carry the same information accessibly), so it is aria-hidden.
 * Colours come from the canvas's computed CSS colour, so it follows the
 * page theme without hard-coding a palette here.
 */
export function WaveformCanvas({ analyser, source }: WaveformCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const resize = () => {
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(canvas.clientWidth * ratio));
      canvas.height = Math.max(1, Math.floor(canvas.clientHeight * ratio));
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    const drawFlat = () => {
      const { width, height } = canvas;
      context.clearRect(0, 0, width, height);
      context.strokeStyle = getComputedStyle(canvas).color;
      context.globalAlpha = 0.35;
      context.lineWidth = Math.max(1, window.devicePixelRatio || 1);
      context.beginPath();
      context.moveTo(0, height / 2);
      context.lineTo(width, height / 2);
      context.stroke();
      context.globalAlpha = 1;
    };

    if (!analyser || prefersReducedMotion()) {
      drawFlat();
      return () => observer.disconnect();
    }

    const buffer = new Float32Array(analyser.fftSize);
    let frame = 0;
    const draw = () => {
      const { width, height } = canvas;
      analyser.getFloatTimeDomainData(buffer);
      context.clearRect(0, 0, width, height);
      context.strokeStyle = getComputedStyle(canvas).color;
      context.lineWidth = 2 * (window.devicePixelRatio || 1);
      context.lineJoin = "round";
      context.beginPath();
      const step = buffer.length / width;
      for (let x = 0; x < width; x += 1) {
        const sample = buffer[Math.floor(x * step)] ?? 0;
        const y = height / 2 + sample * (height / 2) * 1.8;
        if (x === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      }
      context.stroke();
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [analyser]);

  return (
    <figure className="flex flex-col gap-1">
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className={
          source === "speaker"
            ? "h-20 w-full rounded-lg bg-stone-50 text-emerald-800"
            : "h-20 w-full rounded-lg bg-stone-50 text-emerald-600"
        }
      />
      <figcaption className="text-center text-[11px] text-stone-500">
        {source === "mic" ? "Your microphone" : source === "speaker" ? "Agent audio" : "No audio"}
      </figcaption>
    </figure>
  );
}

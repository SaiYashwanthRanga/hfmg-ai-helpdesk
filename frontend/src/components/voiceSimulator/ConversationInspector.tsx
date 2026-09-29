import { ChevronDown, ChevronRight, Copy } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { formatMs } from "../../lib/voiceSimulator/latency";
import { toast } from "../../lib/toastStore";
import type { ApiCallRecord, CollectedSlots, SimulatorTurn } from "../../types/voiceSimulator";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";

function Json({ value }: { value: unknown }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard?.writeText(text).then(() => toast.info("Copied to clipboard"));
        }}
        className="absolute top-1.5 right-1.5 rounded p-1 text-stone-400 hover:bg-stone-200 hover:text-stone-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800"
        aria-label="Copy"
      >
        <Copy className="size-3.5" aria-hidden="true" />
      </button>
      <pre className="max-h-80 overflow-auto rounded-lg bg-stone-900 p-3 pr-8 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-stone-100">
        {text}
      </pre>
    </div>
  );
}

function Section({ title, count, defaultOpen = false, children }: { title: string; count?: number; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <section className="border-b border-stone-100 last:border-b-0">
      <h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2 px-5 py-2.5 text-left text-xs font-semibold text-stone-800 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-800"
        >
          {open ? <ChevronDown className="size-3.5" aria-hidden="true" /> : <ChevronRight className="size-3.5" aria-hidden="true" />}
          {title}
          {count !== undefined ? <span className="text-stone-400">({count})</span> : null}
        </button>
      </h3>
      {open ? (
        <div id={id} className="px-5 pb-4">
          {children}
        </div>
      ) : null}
    </section>
  );
}

function slotChanges(before: CollectedSlots | null, after: CollectedSlots | null) {
  if (!after) return [];
  return (Object.keys(after) as Array<keyof CollectedSlots>)
    .filter((key) => JSON.stringify(before?.[key] ?? null) !== JSON.stringify(after[key] ?? null))
    .map((key) => ({ key, before: before?.[key] ?? null, after: after[key] ?? null }));
}

const TIMING_ROWS: Array<{ key: keyof SimulatorTurn["timings"]; label: string }> = [
  { key: "utterance_ms", label: "Caller spoke for" },
  { key: "capture_ms", label: "Audio capture" },
  { key: "stt_ms", label: "Speech-to-text" },
  { key: "queue_wait_ms", label: "Queue wait (DB + session lock)" },
  { key: "llm_ms", label: "Agent turn (all NLU calls)" },
  { key: "ticket_create_ms", label: "Ticket creation" },
  { key: "tts_ms", label: "Text-to-speech" },
  { key: "server_total_ms", label: "Server total" },
  { key: "playback_start_ms", label: "Playback start" },
  { key: "playback_duration_ms", label: "Reply duration" },
  { key: "turn_total_ms", label: "Total turn (caller waits)" },
];

export interface ConversationInspectorProps {
  turns: SimulatorTurn[];
  selectedTurnId: string | null;
  onSelectTurn: (turnClientId: string) => void;
  apiCalls: ApiCallRecord[];
  open: boolean;
  onToggle: () => void;
}

/**
 * Everything the server recorded about one turn: what was heard, what the
 * model returned, what the orchestrator concluded and how long each step
 * took. Collapsed by default; each section opens independently.
 */
export function ConversationInspector({ turns, selectedTurnId, onSelectTurn, apiCalls, open, onToggle }: ConversationInspectorProps) {
  const panelId = useId();
  const selectedIndex = turns.findIndex((t) => t.turn_client_id === selectedTurnId);
  const index = selectedIndex === -1 ? turns.length - 1 : selectedIndex;
  const turn = turns[index] ?? null;
  const previous = index > 0 ? turns[index - 1] : null;
  const changes = turn ? slotChanges(previous?.collected_after ?? null, turn.collected_after) : [];
  const classification = turn?.llm_trace.find((c) => c.schema_name === "record_issue")?.output as Record<string, unknown> | undefined;

  return (
    <Card padding="none">
      <h2>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={onToggle}
          className="flex w-full items-center justify-between gap-2 px-5 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-800"
        >
          <span className="flex items-center gap-2 text-sm font-semibold text-stone-900">
            {open ? <ChevronDown className="size-4" aria-hidden="true" /> : <ChevronRight className="size-4" aria-hidden="true" />}
            Conversation inspector
          </span>
          <span className="text-xs font-normal text-stone-500">
            {turn ? `Turn ${turn.turn_index} of ${turns.length - 1}` : "No turns yet"} · press D
          </span>
        </button>
      </h2>

      {open ? (
        <div id={panelId} className="border-t border-stone-100">
          {turn ? (
            <>
              <div className="flex flex-wrap items-center gap-2 px-5 py-3">
                <label htmlFor={`${panelId}-turn`} className="text-xs font-medium text-stone-600">
                  Turn
                </label>
                <select
                  id={`${panelId}-turn`}
                  value={turn.turn_client_id}
                  onChange={(event) => onSelectTurn(event.target.value)}
                  className="h-8 rounded-lg border border-stone-200/80 bg-white px-2 text-xs focus:outline-none focus:ring-1 focus:ring-emerald-800"
                >
                  {turns.map((t) => (
                    <option key={t.turn_client_id} value={t.turn_client_id}>
                      {t.turn_index === 0 ? "0 — greeting" : `${t.turn_index} — ${t.intent ?? "…"}`}
                    </option>
                  ))}
                </select>
                {turn.intent ? <Badge label={`Intent: ${turn.intent}`} color="primary" size="sm" /> : null}
                {turn.state_before && turn.state_after ? (
                  <span className="font-mono text-[11px] text-stone-500">
                    {turn.state_before} → {turn.state_after}
                  </span>
                ) : null}
                {turn.errors.length ? <Badge label={`${turn.errors.length} error(s)`} color="danger" size="sm" /> : null}
              </div>

              <Section title="Caller transcript" defaultOpen>
                <p className="text-sm text-stone-800">
                  {turn.input_mode === "system" ? <em className="text-stone-500">Greeting — no caller input.</em> : turn.utterance?.trim() || <em>(silence)</em>}
                </p>
                <p className="mt-1 text-[11px] text-stone-500">
                  Input: {turn.input_mode}
                  {turn.stt_confidence !== null ? ` · STT confidence ${turn.stt_confidence}` : ""}
                </p>
              </Section>
              <Section title="AI transcript" defaultOpen>
                <p className="text-sm text-stone-800">{turn.agent_text ?? "—"}</p>
                {turn.call_ended ? <p className="mt-1 text-[11px] text-stone-500">The agent hung up after this.</p> : null}
              </Section>
              <Section title="Classification & priority">
                {classification ? (
                  <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                    {(["category", "category_confidence", "priority", "impact", "escalation_requested", "is_problem_description", "unable_to_determine", "short_issue"] as const).map((key) => (
                      <div key={key}>
                        <dt className="text-[11px] text-stone-500">{key}</dt>
                        <dd className="font-medium text-stone-900">{String(classification[key] ?? "—")}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="text-xs text-stone-500">Classification runs on the problem-description turn. This turn did not classify.</p>
                )}
                <p className="mt-2 text-[11px] text-stone-500">
                  Model output is validated server-side: an unknown category becomes "Other" and priority is mapped Critical→URGENT.
                </p>
              </Section>
              <Section title="Ticket extraction" count={changes.length}>
                {changes.length ? (
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-[11px] text-stone-500">
                        <th scope="col" className="py-1 font-medium">Slot</th>
                        <th scope="col" className="py-1 font-medium">Before</th>
                        <th scope="col" className="py-1 font-medium">After</th>
                      </tr>
                    </thead>
                    <tbody>
                      {changes.map((c) => (
                        <tr key={c.key} className="border-t border-stone-100 align-top">
                          <th scope="row" className="py-1 pr-2 text-left font-mono font-normal text-stone-600">{c.key}</th>
                          <td className="py-1 pr-2 text-stone-400 line-through">{c.before === null ? "—" : String(c.before)}</td>
                          <td className="py-1 font-medium text-stone-900">{c.after === null ? "—" : String(c.after)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="text-xs text-stone-500">No slot changed on this turn.</p>
                )}
                {turn.collected_after ? (
                  <div className="mt-3">
                    <Json value={turn.collected_after} />
                  </div>
                ) : null}
              </Section>
              <Section title="Ticket payload">
                {turn.ticket_payload ? (
                  <>
                    <p className="mb-2 text-xs text-stone-600">Exactly what the orchestrator passed to ticket_service.create_ticket on this turn.</p>
                    <Json value={turn.ticket_payload} />
                  </>
                ) : (
                  <p className="text-xs text-stone-500">No ticket was created on this turn.</p>
                )}
              </Section>
              <Section title="Raw AI response" count={turn.llm_trace.length}>
                {turn.llm_trace.length ? (
                  <ol className="flex flex-col gap-3">
                    {turn.llm_trace.map((call, i) => (
                      <li key={`${call.schema_name}-${i}`} className="flex flex-col gap-1.5">
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <span className="font-mono font-semibold text-stone-800">{call.schema_name ?? call.kind}</span>
                          {call.kind === "rule" ? <Badge label="rule — no model call" color="success" size="sm" /> : null}
                          <span className="text-stone-500">
                            {formatMs(call.duration_ms)} · {call.attempts} attempt{call.attempts === 1 ? "" : "s"} · at +{formatMs(call.started_ms)}
                          </span>
                          {call.error ? <Badge label={call.error} color="danger" size="sm" /> : null}
                        </div>
                        <Json value={call.output ?? { error: call.error }} />
                        <details className="text-xs">
                          <summary className="cursor-pointer text-stone-500 hover:text-stone-800">Prompt sent</summary>
                          <div className="mt-2 flex flex-col gap-2">
                            <Json value={`SYSTEM:\n${call.system}\n\nUSER:\n${call.user}`} />
                          </div>
                        </details>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-xs text-stone-500">No model call on this turn.</p>
                )}
              </Section>
              <Section title="Raw STT result">
                {turn.stt_raw ? <Json value={turn.stt_raw} /> : <p className="text-xs text-stone-500">Typed or mock input — no speech-to-text.</p>}
              </Section>
              <Section title="Processing times">
                <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                  {TIMING_ROWS.map((row) => (
                    <div key={row.key} className="flex justify-between gap-2 border-b border-stone-50 py-0.5">
                      <dt className="text-stone-600">{row.label}</dt>
                      <dd className="font-medium text-stone-900 tabular-nums">{formatMs(turn.timings[row.key] as number | null)}</dd>
                    </div>
                  ))}
                </dl>
              </Section>
              <Section title="Errors" count={turn.errors.length} defaultOpen={turn.errors.length > 0}>
                {turn.errors.length ? (
                  <ul className="flex flex-col gap-1 text-xs">
                    {turn.errors.map((e, i) => (
                      <li key={i} className="rounded bg-rose-50 px-2 py-1 text-rose-800">
                        <span className="font-semibold uppercase">{e.stage}</span> · {e.type}: {e.message}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-stone-500">None.</p>
                )}
              </Section>
            </>
          ) : (
            <p className="px-5 py-6 text-xs text-stone-500">Start a call to inspect its turns.</p>
          )}
          <Section title="API calls" count={apiCalls.length}>
            {apiCalls.length ? (
              <div className="max-h-64 overflow-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-left text-stone-500">
                      <th scope="col" className="py-1 font-medium">Time</th>
                      <th scope="col" className="py-1 font-medium">Request</th>
                      <th scope="col" className="py-1 text-right font-medium">Status</th>
                      <th scope="col" className="py-1 text-right font-medium">Duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...apiCalls].reverse().map((call) => (
                      <tr key={call.id} className="border-t border-stone-100" title={call.error ?? undefined}>
                        <td className="py-1 pr-2 text-stone-500 tabular-nums">{new Date(call.at).toLocaleTimeString()}</td>
                        <td className="py-1 pr-2 font-mono text-stone-800">
                          {call.method} {call.path}
                        </td>
                        <td className={cn("py-1 text-right tabular-nums", call.status && call.status < 400 ? "text-emerald-700" : "text-rose-700")}>
                          {call.status ?? "network error"}
                        </td>
                        <td className="py-1 text-right tabular-nums">{formatMs(call.durationMs)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-xs text-stone-500">No requests yet.</p>
            )}
          </Section>
        </div>
      ) : null}
    </Card>
  );
}

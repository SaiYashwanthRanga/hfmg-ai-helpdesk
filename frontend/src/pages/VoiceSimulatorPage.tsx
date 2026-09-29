import { FlaskConical, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { useApiCallLog, useSimulatorConfigQuery, useSimulatorMetricsQuery } from "../api/voiceSimulator";
import { CallControlPanel } from "../components/voiceSimulator/CallControlPanel";
import { CallStatusIndicator } from "../components/voiceSimulator/CallStatusIndicator";
import { ConversationInspector } from "../components/voiceSimulator/ConversationInspector";
import { ConversationTimeline } from "../components/voiceSimulator/ConversationTimeline";
import { LatencyPanel } from "../components/voiceSimulator/LatencyPanel";
import { LoadTestDrawer } from "../components/voiceSimulator/LoadTestDrawer";
import { ReplayResultModal } from "../components/voiceSimulator/ReplayResultModal";
import { SimulatorHealthPanel } from "../components/voiceSimulator/SimulatorHealthPanel";
import { TestingToolbar } from "../components/voiceSimulator/TestingToolbar";
import { TextUtteranceInput } from "../components/voiceSimulator/TextUtteranceInput";
import { TicketPreviewPanel } from "../components/voiceSimulator/TicketPreviewPanel";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { Modal } from "../components/ui/Modal";
import { useSimulatorSession } from "../hooks/voiceSimulator/useSimulatorSession";
import type { StartOptions } from "../hooks/voiceSimulator/useSimulatorSession";
import { SHORTCUTS, useSimulatorShortcuts } from "../hooks/voiceSimulator/useSimulatorShortcuts";
import { toast } from "../lib/toastStore";
import type { ReplayResult } from "../lib/voiceSimulator/replay";
import type { TurnTimings } from "../types/voiceSimulator";

const DEFAULT_START: StartOptions = {
  tts: true,
  voice: "server",
  useMicrophone: true,
  label: "",
  callerId: "",
  sendNotifications: false,
};

const TEXT_DISABLED_REASON: Record<string, string> = {
  idle: "Start a call to talk to the agent",
  connecting: "Connecting…",
  thinking: "The agent is thinking…",
  speaking: "The agent is speaking…",
  disconnected: "The call has ended",
  error: "Retry or dismiss the error first",
};

/**
 * AI Call Simulator (VOICE_SIMULATOR_DESIGN.md): place a browser "call" to
 * the help desk agent, with no phone provider. The agent is the production
 * orchestrator; this page only supplies speech in and plays speech out.
 */
export function VoiceSimulatorPage() {
  const configQuery = useSimulatorConfigQuery();
  const sim = useSimulatorSession();
  const { state, mic } = sim;
  const apiCalls = useApiCallLog();

  const [startOptions, setStartOptions] = useState<StartOptions>(DEFAULT_START);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [loadTestOpen, setLoadTestOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [replayResult, setReplayResult] = useState<ReplayResult | null>(null);
  const textInputRef = useRef<HTMLInputElement>(null);

  const config = configQuery.data;
  const sessionId = state.session?.id ?? null;
  const metricsQuery = useSimulatorMetricsQuery(sessionId, state.metricsVersion);

  // Prefer server-recorded timings; fall back to what the last responses carried.
  const timings: TurnTimings[] = useMemo(
    () => metricsQuery.data?.turns ?? state.turns.map((t) => t.timings),
    [metricsQuery.data, state.turns],
  );
  const selectedTurnIndex = state.turns.find((t) => t.turn_client_id === selectedTurnId)?.turn_index ?? null;

  const start = useCallback(() => {
    setSelectedTurnId(null);
    void sim.start({
      ...startOptions,
      tts: startOptions.tts && (startOptions.voice === "browser" || Boolean(config?.speech_configured)),
      useMicrophone: startOptions.useMicrophone && Boolean(config?.speech_configured),
      sendNotifications: startOptions.sendNotifications && Boolean(config?.notifications_allowed),
    });
  }, [config?.notifications_allowed, config?.speech_configured, sim, startOptions]);

  const requestClear = useCallback(() => {
    if (sim.isActive) setConfirmClear(true);
    else {
      setSelectedTurnId(null);
      void sim.clear();
    }
  }, [sim]);

  useSimulatorShortcuts({
    pushToTalkDown: sim.listenMode === "push_to_talk" ? sim.pushToTalkDown : () => undefined,
    pushToTalkUp: () => void sim.pushToTalkUp(),
    toggleMute: () => mic.status === "on" && mic.setMuted(!mic.muted),
    toggleCall: () => (sim.isActive ? void sim.hangUp() : start()),
    clear: requestClear,
    focusText: () => textInputRef.current?.focus(),
    toggleInspector: () => setInspectorOpen((v) => !v),
    toggleListenMode: () => sim.setListenMode(sim.listenMode === "push_to_talk" ? "continuous" : "push_to_talk"),
    showHelp: () => setShortcutsOpen(true),
  });

  // Announce ticket creation once per ticket.
  const ticketNumber = state.session?.ticket?.ticket_number ?? null;
  const announced = useRef<string | null>(null);
  useEffect(() => {
    if (ticketNumber && announced.current !== ticketNumber) {
      announced.current = ticketNumber;
      toast.success(`Ticket ${ticketNumber} created`);
    }
  }, [ticketNumber]);

  if (configQuery.isLoading) {
    return <LoadingState variant="page" />;
  }

  if (configQuery.isError) {
    const disabled = configQuery.error instanceof ApiError && configQuery.error.status === 404;
    return disabled ? (
      <EmptyState
        icon={FlaskConical}
        title="The AI Call Simulator is turned off"
        description="Set ENABLE_VOICE_SIMULATOR=true in the backend's .env and restart it. The simulator is never available when ENVIRONMENT=production."
      />
    ) : (
      <ErrorState
        severity="degraded"
        title="Couldn't reach the simulator"
        description={configQuery.error instanceof Error ? configQuery.error.message : "Try again shortly."}
        retry={() => void configQuery.refetch()}
      />
    );
  }

  const textDisabled = state.status !== "listening" || sim.autopilot !== null;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-stone-900">AI Call Simulator</h1>
            <p className="text-xs text-stone-500">
              Call the Horizon Family Medical Group IT Help Desk agent from your browser — same agent, no phone line.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {state.session?.label ? <span className="text-xs text-stone-500">“{state.session.label}”</span> : null}
            {sim.autopilot ? <span className="text-xs font-medium text-emerald-800">Mock caller: {sim.autopilot.caller_name}</span> : null}
            <CallStatusIndicator status={state.status} />
          </div>
        </div>
        <p className="flex items-start gap-2 rounded-lg border border-amber-200/80 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>
            Test environment. Don't use real patient information. Transcripts are kept for a limited time; audio is never stored. Tickets created here are marked SIMULATOR and kept out of the queue and dashboards.
            {config && !config.llm_configured ? " The server has no LLM key configured, so the agent can't understand anything and will escalate." : ""}
          </span>
        </p>
      </header>

      <SimulatorHealthPanel />

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(320px,380px)_minmax(260px,320px)]">
        <div className="md:col-span-2 xl:order-2 xl:col-span-1">
          <CallControlPanel
            sim={sim}
            config={config}
            startOptions={startOptions}
            onStartOptionsChange={setStartOptions}
            onStart={start}
            onRequestClear={requestClear}
          />
        </div>

        <Card padding="none" className="flex min-h-[420px] flex-col xl:order-1 xl:max-h-[760px]">
          <div className="flex items-center justify-between border-b border-stone-100 px-5 py-3">
            <h2 className="text-sm font-semibold text-stone-900">Conversation</h2>
            <span className="text-[11px] text-stone-500">{state.turns.filter((t) => t.input_mode !== "system").length} caller turns</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <ConversationTimeline entries={state.timeline} selectedTurnId={selectedTurnId} onSelectTurn={setSelectedTurnId} />
          </div>
          <TextUtteranceInput
            ref={textInputRef}
            disabled={textDisabled}
            disabledReason={sim.autopilot ? "The mock caller is answering" : (TEXT_DISABLED_REASON[state.status] ?? "")}
            onSubmit={(text) => sim.submitText(text)}
          />
        </Card>

        <div className="xl:order-3">
          <TicketPreviewPanel session={state.session} />
        </div>
      </div>

      <TestingToolbar
        sim={sim}
        categories={config?.voice_categories ?? []}
        onReplayResult={setReplayResult}
        onOpenLoadTest={() => setLoadTestOpen(true)}
        onShowShortcuts={() => setShortcutsOpen(true)}
      />

      <LatencyPanel turns={timings} selectedTurnIndex={selectedTurnIndex} isLoading={metricsQuery.isFetching} />

      <ConversationInspector
        turns={state.turns}
        selectedTurnId={selectedTurnId}
        onSelectTurn={setSelectedTurnId}
        apiCalls={apiCalls}
        open={inspectorOpen}
        onToggle={() => setInspectorOpen((v) => !v)}
      />

      <LoadTestDrawer
        open={loadTestOpen}
        onClose={() => setLoadTestOpen(false)}
        maxConcurrent={config?.max_concurrent_sessions ?? 10}
        llmConfigured={config?.llm_configured ?? false}
      />

      <ReplayResultModal result={replayResult} onClose={() => setReplayResult(null)} />

      <Modal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} variant="confirmation" title="Keyboard shortcuts">
        <dl className="flex flex-col gap-2 text-sm">
          {SHORTCUTS.map((s) => (
            <div key={s.keys} className="flex items-center justify-between gap-4">
              <dt>
                <kbd className="rounded border border-stone-300 bg-stone-50 px-1.5 py-0.5 font-mono text-xs">{s.keys}</kbd>
              </dt>
              <dd className="text-right text-stone-700">{s.action}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-4 text-xs text-stone-500">Shortcuts are ignored while you are typing in a field.</p>
      </Modal>

      <Modal open={confirmClear} onClose={() => setConfirmClear(false)} variant="confirmation" title="Clear this call?">
        <p className="text-sm text-stone-700">
          This hangs up without creating a ticket from what was said so far, and clears the page. The session's transcript stays on the server until
          the retention purge.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmClear(false)}>
            Keep call
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              setConfirmClear(false);
              setSelectedTurnId(null);
              void sim.clear();
            }}
          >
            Clear
          </Button>
        </div>
      </Modal>
    </div>
  );
}

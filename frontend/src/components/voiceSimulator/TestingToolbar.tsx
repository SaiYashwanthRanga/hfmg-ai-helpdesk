import { Bot, ChevronDown, Dices, Download, Gauge, History, Keyboard } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { simulatorApi } from "../../api/voiceSimulator";
import { toast } from "../../lib/toastStore";
import { downloadFile, exportFilename, metricsCsv, sessionJson, transcriptText } from "../../lib/voiceSimulator/exports";
import { replaySession } from "../../lib/voiceSimulator/replay";
import type { ReplayResult } from "../../lib/voiceSimulator/replay";
import type { SimulatorController } from "../../hooks/voiceSimulator/useSimulatorSession";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";

export interface TestingToolbarProps {
  sim: SimulatorController;
  categories: string[];
  onReplayResult: (result: ReplayResult) => void;
  onOpenLoadTest: () => void;
  onShowShortcuts: () => void;
}

type ExportKind = "transcript" | "metrics" | "json" | "ticket";

const EXPORTS: Array<{ kind: ExportKind; label: string; hint: string }> = [
  { kind: "transcript", label: "Transcript (.txt)", hint: "Timestamped caller and agent lines" },
  { kind: "metrics", label: "Metrics (.csv)", hint: "One row per turn, every timing" },
  { kind: "json", label: "Session (.json)", hint: "Session, turns, traces and metrics" },
  { kind: "ticket", label: "Ticket (.json)", hint: "The created ticket record" },
];

function randomSeed() {
  return Math.floor(Math.random() * 1_000_000);
}

/** Export, mock caller, random issue, replay and load-test entry points. */
export function TestingToolbar({ sim, categories, onReplayResult, onOpenLoadTest, onShowShortcuts }: TestingToolbarProps) {
  const { state } = sim;
  const session = state.session;
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [issueCategory, setIssueCategory] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [menuOpen]);

  async function exportAs(kind: ExportKind) {
    if (!session) return;
    setMenuOpen(false);
    setBusy("export");
    try {
      const detail = await simulatorApi.session(session.id);
      if (kind === "transcript") {
        downloadFile(exportFilename(session.id, "txt"), transcriptText(detail), "text/plain");
      } else if (kind === "metrics") {
        downloadFile(exportFilename(session.id, "csv"), metricsCsv(detail), "text/csv");
      } else if (kind === "json") {
        const metrics = await simulatorApi.metrics(session.id);
        downloadFile(exportFilename(session.id, "json"), sessionJson(detail, metrics), "application/json");
      } else {
        if (!detail.session.ticket) {
          toast.warning("This session has not created a ticket yet.");
          return;
        }
        const ticket = await api.getTicket(detail.session.ticket.id);
        downloadFile(`${ticket.ticket_number}.json`, JSON.stringify(ticket, null, 2), "application/json");
      }
      toast.success("Export downloaded");
    } catch (err) {
      toast.error(`Export failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }

  async function startMockCaller() {
    setBusy("mock");
    try {
      const caller = await simulatorApi.mockCaller(randomSeed(), { category: issueCategory || undefined });
      sim.setAutopilot(caller);
      toast.info(`Mock caller: ${caller.caller_name} — ${caller.persona}`);
    } catch (err) {
      toast.error(`Couldn't load a mock caller: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }

  async function sendRandomIssue() {
    setBusy("issue");
    try {
      const issue = await simulatorApi.randomIssue(randomSeed(), issueCategory || undefined);
      if (!sim.submitText(issue.utterance, "mock")) toast.warning("Wait for the agent to finish, then try again.");
      else if (issue.note) toast.info(issue.note);
    } catch (err) {
      toast.error(`Couldn't load an issue: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }

  async function replay() {
    if (!session) return;
    setBusy("replay");
    try {
      const detail = await simulatorApi.session(session.id);
      onReplayResult(await replaySession(simulatorApi, detail));
    } catch (err) {
      toast.error(`Replay failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }

  const listening = state.status === "listening";
  const callerTurns = state.turns.filter((t) => t.input_mode !== "system").length;

  return (
    <Card padding="sm" className="flex flex-wrap items-center gap-2">
      <label className="sr-only" htmlFor="sim-issue-category">
        Issue category for mock callers
      </label>
      <select
        id="sim-issue-category"
        value={issueCategory}
        onChange={(event) => setIssueCategory(event.target.value)}
        className="h-8 rounded-lg border border-stone-200/80 bg-white px-2 text-xs text-stone-700 focus:outline-none focus:ring-1 focus:ring-emerald-800"
      >
        <option value="">Any category</option>
        {categories.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => (sim.autopilot ? sim.setAutopilot(null) : void startMockCaller())}
        disabled={!sim.isActive || busy === "mock"}
        aria-pressed={sim.autopilot !== null}
        icon={<Bot className="size-3.5" aria-hidden="true" />}
        title="A scripted caller answers every question for you"
      >
        {sim.autopilot ? "Stop mock caller" : "Mock caller"}
      </Button>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => void sendRandomIssue()}
        disabled={!listening || sim.autopilot !== null || busy === "issue"}
        icon={<Dices className="size-3.5" aria-hidden="true" />}
        title="Say a random IT problem from the test bank"
      >
        Random issue
      </Button>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => void replay()}
        loading={busy === "replay"}
        disabled={!session || callerTurns === 0 || sim.isActive}
        icon={<History className="size-3.5" aria-hidden="true" />}
        title={sim.isActive ? "End the call before replaying it" : "Run the same caller lines again and compare the outcome"}
      >
        Replay
      </Button>
      <Button size="sm" variant="secondary" onClick={onOpenLoadTest} icon={<Gauge className="size-3.5" aria-hidden="true" />}>
        Load test
      </Button>

      <div className="ml-auto flex items-center gap-2">
        <Button size="sm" variant="ghost" onClick={onShowShortcuts} icon={<Keyboard className="size-3.5" aria-hidden="true" />}>
          <span className="sr-only sm:not-sr-only">Shortcuts</span>
        </Button>
        <div ref={menuRef} className="relative">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setMenuOpen((v) => !v)}
            disabled={!session}
            loading={busy === "export"}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            icon={<Download className="size-3.5" aria-hidden="true" />}
          >
            Export <ChevronDown className="size-3" aria-hidden="true" />
          </Button>
          {menuOpen ? (
            <div role="menu" className="absolute right-0 z-20 mt-1 w-56 rounded-lg border border-stone-200 bg-white p-1 shadow-lg">
              {EXPORTS.map((item) => (
                <button
                  key={item.kind}
                  type="button"
                  role="menuitem"
                  onClick={() => void exportAs(item.kind)}
                  className="flex w-full flex-col rounded-md px-3 py-2 text-left hover:bg-stone-50 focus-visible:bg-stone-100 focus-visible:outline-none"
                >
                  <span className="text-xs font-medium text-stone-900">{item.label}</span>
                  <span className="text-[11px] text-stone-500">{item.hint}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

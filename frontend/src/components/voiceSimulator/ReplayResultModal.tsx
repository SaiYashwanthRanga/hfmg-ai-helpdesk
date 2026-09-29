import type { ReplayResult } from "../../lib/voiceSimulator/replay";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";

function show(value: unknown) {
  return value === null || value === undefined ? "—" : String(value);
}

/** What changed when a recorded session's caller lines were run again. */
export function ReplayResultModal({ result, onClose }: { result: ReplayResult | null; onClose: () => void }) {
  return (
    <Modal open={result !== null} onClose={onClose} variant="escalation" title="Replay result">
      {result ? (
        <div className="flex flex-col gap-3 text-sm">
          <p className="text-stone-700">
            Replayed {result.turnsReplayed} of {result.turnsInSource} caller turns into a new session.
          </p>
          {result.divergedAt ? (
            <p role="alert" className="rounded-lg bg-amber-50 p-3 text-xs text-amber-800">
              The conversation diverged at turn {result.divergedAt.turn}: the original agent was in {result.divergedAt.expected}, the replay was in{" "}
              {result.divergedAt.actual}. Later lines were not sent, since they would answer a different question.
            </p>
          ) : null}
          {result.diffs.length === 0 ? (
            <p className="rounded-lg bg-emerald-50 p-3 text-xs text-emerald-800">Same outcome: state, escalation, ticket and every compared slot match.</p>
          ) : (
            <table className="w-full text-xs">
              <caption className="sr-only">Differences between original and replay</caption>
              <thead>
                <tr className="text-left text-[11px] text-stone-500">
                  <th scope="col" className="py-1 font-medium">Field</th>
                  <th scope="col" className="py-1 font-medium">Original</th>
                  <th scope="col" className="py-1 font-medium">Replay</th>
                </tr>
              </thead>
              <tbody>
                {result.diffs.map((d) => (
                  <tr key={d.field} className="border-t border-stone-100">
                    <th scope="row" className="py-1 pr-2 text-left font-mono font-normal text-stone-600">{d.field}</th>
                    <td className="py-1 pr-2">{show(d.before)}</td>
                    <td className="py-1 font-semibold text-stone-900">{show(d.after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-[11px] text-stone-500">
            Replay session: <span className="font-mono">{result.replaySessionId}</span>
            {result.replaySession.ticket ? ` · ticket ${result.replaySession.ticket.ticket_number}` : ""}
          </p>
          <div className="flex justify-end">
            <Button variant="secondary" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

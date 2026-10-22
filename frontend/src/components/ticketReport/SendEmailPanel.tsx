import { CircleAlert, CircleCheck, Mail } from "lucide-react";
import { formatAbsoluteTime } from "../../lib/format";
import { confirmationSummary, deliveryStatus } from "../../lib/ticketReport/workflow";
import type { Availability } from "../../lib/ticketReport/workflow";
import type { ReportConfig, TicketReportRecord } from "../../types/ticketReport";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Modal } from "../ui/Modal";

interface Props {
  report: TicketReportRecord;
  config: ReportConfig;
  availability: Availability;
  confirmOpen: boolean;
  sending: boolean;
  onOpen: () => void;
  onClose: () => void;
  onConfirm: () => void;
}

const TONE = {
  neutral: { box: "border-stone-200 bg-stone-50 text-stone-700", Icon: Mail },
  busy: { box: "border-stone-200 bg-stone-50 text-stone-700", Icon: Mail },
  success: { box: "border-emerald-200 bg-emerald-50 text-emerald-900", Icon: CircleCheck },
  error: { box: "border-rose-200 bg-rose-50 text-rose-900", Icon: CircleAlert },
} as const;

/** Step 3: nothing is emailed until the user presses Send Email and confirms the recipient. */
export function SendEmailPanel({ report, config, availability, confirmOpen, sending, onOpen, onClose, onConfirm }: Props) {
  const recipient = report.email.recipient ?? config.email.recipient;
  const status = deliveryStatus(report.email, formatAbsoluteTime);
  const { box, Icon } = TONE[status.tone];
  const retry = report.email.status === "FAILED";

  return (
    <Card className="flex flex-col gap-3">
      <div>
        <h2 className="text-base font-semibold text-stone-900">3. Send to the IT team</h2>
        <p className="mt-1 text-sm text-stone-600">
          Review the report above, then email it to {recipient ? <strong className="font-medium text-stone-900">{recipient}</strong> : "the IT team"}.
          The email contains this same analysis. The AI is not run again, and nothing is changed in Freshworks.
        </p>
      </div>

      <div role="status" aria-live="polite" className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${box}`}>
        <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <span>{status.text}</span>
      </div>

      <div className="flex flex-col gap-1">
        <div>
          <Button icon={<Mail className="size-4" aria-hidden="true" />} onClick={onOpen} disabled={!availability.enabled} loading={sending} aria-describedby="send-hint">
            {retry ? "Retry Send Email" : "Send Email"}
          </Button>
        </div>
        {!availability.enabled ? <p id="send-hint" className="text-xs text-stone-500">{availability.reason}</p> : <span id="send-hint" className="sr-only">Opens a confirmation first.</span>}
      </div>

      <Modal open={confirmOpen} onClose={onClose} variant="confirmation" title="Send this report?">
        <p className="text-sm text-stone-700">
          This will email the report to <strong className="font-medium text-stone-900 break-all">{recipient}</strong>.
        </p>
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-sm text-stone-700">
          {confirmationSummary(report).map((line) => <li key={line}>{line}</li>)}
        </ul>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={sending}>Cancel</Button>
          <Button onClick={onConfirm} loading={sending} disabled={sending}>Send report</Button>
        </div>
      </Modal>
    </Card>
  );
}

import { FileSpreadsheet, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { cn } from "../../lib/cn";
import { formatBytes } from "../../lib/ticketReport/workflow";
import type { ReportConfig, TicketReportRecord } from "../../types/ticketReport";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { ValidationSummary } from "./ValidationSummary";

interface Props {
  config: ReportConfig;
  report: TicketReportRecord | null;
  uploading: boolean;
  /** Replacing the file is not allowed while an analysis is running. */
  locked: boolean;
  onFile: (file: File) => void;
  analyze: { enabled: boolean; reason: string; busy: boolean; onClick: () => void };
}

/** Step 1 and 2: choose the Freshworks export, see whether it is usable, and start the analysis. */
export function UploadPanel({ config, report, uploading, locked, onFile, analyze }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const accept = config.accepted_extensions.join(",");

  const choose = (files: FileList | null) => {
    const file = files?.[0];
    if (file) onFile(file);
    if (input.current) input.current.value = ""; // choosing the same file again must still fire
  };

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-base font-semibold text-stone-900">1. Upload a Freshworks ticket export</h2>
        <p className="mt-1 text-sm text-stone-600">
          {config.accepted_extensions.join(" or ")} files, up to {formatBytes(config.max_upload_bytes)} and {config.max_rows} tickets.
          Requester names, emails and phone numbers are not read.
        </p>
      </div>

      <div
        onDragOver={(event) => { event.preventDefault(); if (!locked) setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); if (!locked && !uploading) choose(event.dataTransfer.files); }}
        className={cn(
          "flex flex-col items-center gap-3 rounded-lg border border-dashed p-6 text-center transition-colors",
          dragging ? "border-emerald-700 bg-emerald-50/60" : "border-stone-300 bg-stone-50/60",
        )}
      >
        {report ? (
          <div className="flex items-center gap-2 text-sm text-stone-800">
            <FileSpreadsheet className="size-5 text-emerald-800" aria-hidden="true" />
            <span className="font-medium break-all">{report.filename}</span>
            <span className="text-stone-500">· {report.source_row_count} row{report.source_row_count === 1 ? "" : "s"}</span>
          </div>
        ) : (
          <p className="text-sm text-stone-600">Drop the file here, or choose it from your computer.</p>
        )}
        <input ref={input} type="file" accept={accept} className="sr-only" id="report-file" onChange={(e) => choose(e.target.files)} />
        <Button
          variant="secondary"
          icon={<Upload className="size-4" aria-hidden="true" />}
          loading={uploading}
          disabled={locked}
          onClick={() => input.current?.click()}
        >
          {report ? "Replace file" : "Choose file"}
        </Button>
        {locked ? <p className="text-xs text-stone-500">The file can be replaced when the analysis has finished.</p> : null}
      </div>

      {report ? <ValidationSummary report={report} /> : null}

      <div className="flex flex-col gap-1 border-t border-stone-200 pt-4">
        <div>
          <Button onClick={analyze.onClick} disabled={!analyze.enabled} loading={analyze.busy} aria-describedby="analyze-hint">
            Analyze with AI
          </Button>
        </div>
        <p id="analyze-hint" className="text-xs text-stone-500">
          {analyze.enabled
            ? config.ai_configured ? "Nothing is emailed or changed in Freshworks when you analyze." : "The AI provider is not configured; the analysis will fail."
            : analyze.reason}
        </p>
      </div>
    </Card>
  );
}

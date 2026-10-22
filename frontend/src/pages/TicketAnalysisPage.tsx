import { ArrowLeft, CircleAlert, CircleCheck, X } from "lucide-react";
import { useCallback, useEffect, useReducer } from "react";
import { Link } from "react-router-dom";
import { useReportConfigQuery, useReportQuery, useTicketReportMutations } from "../api/ticketReports";
import { ApiError } from "../api/client";
import { AnalysisProgress } from "../components/ticketReport/AnalysisProgress";
import { ActionItems } from "../components/ticketReport/ActionItems";
import { ExecutiveSummary } from "../components/ticketReport/ExecutiveSummary";
import { SendEmailPanel } from "../components/ticketReport/SendEmailPanel";
import { UploadPanel } from "../components/ticketReport/UploadPanel";
import { WeeklyFollowUp } from "../components/ticketReport/WeeklyFollowUp";
import { DataLimitations, EmployeeWorkload, RecurringIssues } from "../components/ticketReport/WorkloadAndIssues";
import { Card } from "../components/ui/Card";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { toast } from "../lib/toastStore";
import { ANALYSIS_VERSION } from "../types/ticketReport";
import { analyzeAvailability, initialPageState, pageReducer, sendAvailability, validateFileClient } from "../lib/ticketReport/workflow";

const STORAGE_KEY = "ticketAnalysis.reportId";

/** The report being worked on survives a refresh or a trip to another page (session only, never the data itself). */
function readStoredId(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeId(id: string | null) {
  try {
    if (id) window.sessionStorage.setItem(STORAGE_KEY, id);
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage unavailable (private window, blocked): the page still works, it just forgets on refresh
  }
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : "Something went wrong. Please try again.");

/**
 * Ticket analysis: upload a Freshworks export, analyze it with AI, review the report, and send it to the IT team.
 * Each step is a button press; the server decides what is allowed (see lib/ticketReport/workflow.ts), so a
 * refresh, a second tab or a double click cannot analyze or send the same report twice.
 */
export function TicketAnalysisPage() {
  const config = useReportConfigQuery();
  const [state, dispatch] = useReducer(pageReducer, undefined, () => initialPageState(readStoredId()));
  const reportQuery = useReportQuery(state.reportId);
  const mutations = useTicketReportMutations();
  const report = reportQuery.data ?? null;

  useEffect(() => storeId(state.reportId), [state.reportId]);
  useEffect(() => {
    if (reportQuery.error instanceof ApiError && reportQuery.error.status === 404) dispatch({ type: "report-missing" });
  }, [reportQuery.error]);

  const onFile = useCallback((file: File) => {
    if (!config.data || state.uploading) return;
    const problem = validateFileClient(file, config.data);
    if (problem) return dispatch({ type: "upload-failed", error: problem });
    dispatch({ type: "upload-started" });
    mutations.upload.mutate(file, {
      onSuccess: (created) => dispatch({ type: "upload-finished", reportId: created.id }),
      onError: (err) => dispatch({ type: "upload-failed", error: errorText(err) }),
    });
  }, [config.data, mutations.upload, state.uploading]);

  const analyze = analyzeAvailability(report, { starting: state.starting, uploading: state.uploading });
  const onAnalyze = () => {
    if (!analyze.enabled || !state.reportId) return;
    dispatch({ type: "analyze-started" });
    mutations.analyze.mutate(state.reportId, {
      // 409: another click or tab already started it. Not an error; the report shows the running analysis.
      onSettled: (_data, err) => dispatch({ type: "analyze-settled", error: err && !(err instanceof ApiError && err.status === 409) ? errorText(err) : undefined }),
    });
  };

  const send = sendAvailability(report, config.data, state.sending);
  const onConfirmSend = () => {
    if (!send.enabled || !state.reportId) return;
    dispatch({ type: "send-started" });
    mutations.send.mutate(state.reportId, {
      onSuccess: (result) => {
        dispatch({ type: "send-settled", ok: true, text: result.message });
        toast.success(result.message);
      },
      // A refusal comes back with the server's own words; the report is re-read to show the recorded attempt.
      onError: (err) => {
        const text = errorText(err);
        dispatch({ type: "send-settled", ok: false, text });
        toast.error(text);
      },
    });
  };

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link to="/ai-insights" className="inline-flex items-center gap-1 text-sm text-stone-600 hover:text-stone-900">
          <ArrowLeft className="size-4" aria-hidden="true" /> AI Insights
        </Link>
        <h1 className="mt-1 text-2xl font-bold text-foreground">Ticket analysis</h1>
        <p className="mt-1 max-w-3xl text-sm text-stone-600">
          Upload a Freshworks ticket export, let the AI prioritize it, review the report, then send it to the IT team. Counts and
          ages are calculated from your file; recommendations and recurring issues are marked <strong className="font-medium">AI</strong>.
        </p>
      </div>

      {state.message ? (
        <div
          role={state.message.kind === "error" ? "alert" : "status"}
          className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${state.message.kind === "error" ? "border-rose-200 bg-rose-50 text-rose-900" : "border-emerald-200 bg-emerald-50 text-emerald-900"}`}
        >
          {state.message.kind === "error" ? <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> : <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
          <span className="flex-1">{state.message.text}</span>
          <button type="button" aria-label="Dismiss message" onClick={() => dispatch({ type: "dismiss-message" })} className="rounded p-0.5 hover:bg-black/5">
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>
      ) : null}

      {config.isLoading ? (
        <LoadingState variant="page" />
      ) : config.isError || !config.data ? (
        <ErrorState severity="degraded" title="Ticket analysis is not available" description={errorText(config.error)} retry={() => config.refetch()} />
      ) : !config.data.enabled ? (
        <Card>
          <h2 className="text-base font-semibold text-stone-900">Ticket analysis is turned off</h2>
          <p className="mt-1 text-sm text-stone-600">
            It is off by default because the API has no sign-in: with it on, anyone who can reach the server can use AI credit and email the
            IT team. An administrator can turn it on with <code className="rounded bg-stone-100 px-1">ENABLE_TICKET_REPORTS=true</code> and
            set <code className="rounded bg-stone-100 px-1">IT_REPORT_DISTRIBUTION_EMAIL</code> in the backend configuration.
          </p>
        </Card>
      ) : (
        <>
          <UploadPanel
            config={config.data}
            report={report}
            uploading={state.uploading}
            locked={report?.status === "ANALYZING" || state.starting}
            onFile={onFile}
            analyze={{ ...analyze, busy: state.starting || report?.status === "ANALYZING", onClick: onAnalyze }}
          />

          {state.reportId && reportQuery.isLoading ? <LoadingState variant="page" /> : null}
          {state.reportId && reportQuery.isError && !(reportQuery.error instanceof ApiError && reportQuery.error.status === 404) ? (
            <ErrorState severity="degraded" title="The report could not be loaded" description={errorText(reportQuery.error)} retry={() => reportQuery.refetch()} />
          ) : null}

          {report?.status === "ANALYZING" ? <AnalysisProgress progress={report.progress} /> : null}

          {report?.status === "FAILED" && report.error ? (
            <div role="alert" className="flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900">
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>{report.error} Nothing was sent. You can press Analyze with AI to try again.</span>
            </div>
          ) : null}

          {report?.status === "COMPLETED" && report.analysis && report.analysis.version !== ANALYSIS_VERSION ? (
            <div role="alert" className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>This report was made with an older report layout. Upload the file again to analyze it.</span>
            </div>
          ) : null}

          {report?.status === "COMPLETED" && report.analysis?.version === ANALYSIS_VERSION ? (
            <>
              <ExecutiveSummary analysis={report.analysis} />
              <WeeklyFollowUp analysis={report.analysis} />
              <EmployeeWorkload analysis={report.analysis} />
              <ActionItems analysis={report.analysis} />
              <RecurringIssues analysis={report.analysis} />
              <DataLimitations analysis={report.analysis} />
              <SendEmailPanel
                report={report}
                config={config.data}
                availability={send}
                confirmOpen={state.confirmOpen}
                sending={state.sending}
                onOpen={() => dispatch({ type: "confirm-opened" })}
                onClose={() => dispatch({ type: "confirm-closed" })}
                onConfirm={onConfirmSend}
              />
            </>
          ) : null}
        </>
      )}
    </div>
  );
}

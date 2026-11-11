import { CircleAlert, RefreshCw, Sparkles, TriangleAlert } from "lucide-react";
import { useAiAnalysisQuery, useRefreshAiAnalysis } from "../../api/aiInsights";
import { cn } from "../../lib/cn";
import { formatAbsoluteTime, formatRelativeTime } from "../../lib/format";
import type { AIAnalysisResponse, InsightAttention } from "../../types/aiInsights";
import { TicketChips } from "./TicketChips";

const UNAVAILABLE_COPY: Record<NonNullable<AIAnalysisResponse["reason"]>, { title: string; detail: string }> = {
  not_configured: {
    title: "The AI briefing needs a model key",
    detail: "Set OPENAI_API_KEY on the server and the briefing will be written from your tickets. The numbers below are unaffected.",
  },
  disabled: {
    title: "The AI briefing is switched off",
    detail: "ENABLE_AI_INSIGHTS is false on the server. The numbers below are unaffected.",
  },
  failed: {
    title: "The model didn't return a usable briefing",
    detail: "Nothing was shown rather than something unverified. Try again in a minute.",
  },
};

function sourceLine(data: AIAnalysisResponse): string {
  const window = `the last ${data.days} days`;
  if (data.tickets_analyzed < data.tickets_in_period) {
    return `Written by AI from the ${data.tickets_analyzed} most recent of ${data.tickets_in_period} tickets in ${window}`;
  }
  const noun = data.tickets_analyzed === 1 ? "ticket" : "tickets";
  return `Written by AI from ${data.tickets_analyzed} ${noun} in ${window}`;
}

function AttentionItem({ item }: { item: InsightAttention }) {
  const high = item.severity === "high";
  const Icon = high ? TriangleAlert : CircleAlert;
  return (
    <li className="flex gap-3 py-3.5 first:pt-0 last:pb-0">
      <Icon
        className={cn("mt-0.5 size-4 shrink-0", high ? "text-amber-300" : "text-emerald-200/80")}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-white">
          {item.title}
          <span className="sr-only">{high ? " (high severity)" : " (medium severity)"}</span>
        </p>
        <p className="mt-0.5 text-[13px] leading-snug text-emerald-50/75">{item.reason}</p>
        <TicketChips tickets={item.tickets} tone="dark" className="mt-2" />
      </div>
    </li>
  );
}

function Skeleton() {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-3">
      <p className="text-sm text-emerald-50/80">Reading your recent tickets. The first briefing takes a few seconds.</p>
      <div className="h-7 w-4/5 animate-pulse rounded-md bg-white/10" />
      <div className="h-7 w-3/5 animate-pulse rounded-md bg-white/10" />
      <div className="mt-2 h-4 w-full animate-pulse rounded bg-white/[0.07]" />
      <div className="h-4 w-11/12 animate-pulse rounded bg-white/[0.07]" />
    </div>
  );
}

/**
 * The AI's reading of the period, leading the page (DESIGN.md §2.1): one
 * headline, a short briefing, and what needs attention today — each attention
 * item links to the tickets it is based on. The one dark surface in the app.
 */
export function AIBriefing({ days = 30 }: { days?: number }) {
  const { data, isLoading, isError, refetch } = useAiAnalysisQuery(days);
  const refresh = useRefreshAiAnalysis(days);
  const busy = refresh.isPending;

  const ready = data?.status === "ready";
  const attention = ready ? data.attention : [];

  return (
    <section
      aria-labelledby="ai-briefing-heading"
      className="relative overflow-hidden rounded-2xl bg-emerald-950 text-white shadow-[0_18px_40px_-18px_rgba(2,44,34,0.55)]"
    >
      {/* A soft lift of green in the top corner; depth, not decoration over content. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -right-24 -top-32 size-96 rounded-full bg-emerald-700/25 blur-3xl"
      />

      <div className={cn("relative grid gap-8 p-6 sm:p-8", attention.length > 0 && "lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] lg:gap-10")}>
        <div className="flex min-w-0 flex-col">
          <div className="mb-5 flex items-center justify-between gap-3">
            <h2 id="ai-briefing-heading" className="!m-0 !text-sm flex items-center gap-2 font-semibold text-emerald-100">
              <Sparkles className="size-4" aria-hidden="true" />
              AI briefing
            </h2>
            {data && data.status !== "empty" && data.reason !== "disabled" && data.reason !== "not_configured" ? (
              <button
                type="button"
                onClick={() => refresh.mutate()}
                disabled={busy}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/15 px-2.5 text-xs font-medium text-emerald-50 transition-colors hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white disabled:opacity-60"
              >
                <RefreshCw className={cn("size-3.5", busy && "animate-spin")} aria-hidden="true" />
                {busy ? "Reading tickets" : "Refresh"}
              </button>
            ) : null}
          </div>

          {isLoading ? (
            <Skeleton />
          ) : isError || !data ? (
            <div>
              <p className="font-display text-xl font-semibold text-white">The briefing couldn't be loaded</p>
              <p className="mt-2 max-w-prose text-sm text-emerald-50/75">The server didn't respond. Your tickets and calls are unaffected.</p>
              <button
                type="button"
                onClick={() => refetch()}
                className="mt-4 inline-flex h-8 items-center rounded-lg bg-white px-3 text-xs font-semibold text-emerald-950 hover:bg-emerald-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
              >
                Try again
              </button>
            </div>
          ) : data.status === "empty" ? (
            <div>
              <p className="font-display text-xl font-semibold text-white">No tickets in the last {data.days} days</p>
              <p className="mt-2 max-w-prose text-sm text-emerald-50/75">
                Once tickets come in by phone or the web form, the briefing will say what recurs and what needs attention.
              </p>
            </div>
          ) : data.status === "unavailable" ? (
            <div>
              <p className="font-display text-xl font-semibold text-white">{UNAVAILABLE_COPY[data.reason ?? "failed"].title}</p>
              <p className="mt-2 max-w-prose text-sm text-emerald-50/75">{UNAVAILABLE_COPY[data.reason ?? "failed"].detail}</p>
            </div>
          ) : (
            // Keyed on the reading, so a fresh one resolves into focus (index.css `briefing-in`). The resting
            // style is fully visible: if the animation never runs, the text is still there.
            <div key={data.generated_at} className={cn("briefing-in flex flex-1 flex-col", busy && "opacity-60")}>
              <p className="max-w-[26ch] font-display text-2xl font-semibold leading-tight tracking-tight text-balance text-white sm:text-[1.75rem]">
                {data.headline}
              </p>
              {data.briefing ? (
                <p className="mt-4 max-w-[62ch] text-[15px] leading-relaxed text-emerald-50/85">{data.briefing}</p>
              ) : null}
              <p className="mt-auto pt-6 text-xs text-emerald-100/60">
                {sourceLine(data)}
                {data.generated_at ? (
                  <>
                    {" · "}
                    <time dateTime={data.generated_at} title={formatAbsoluteTime(data.generated_at)}>
                      {formatRelativeTime(data.generated_at)}
                    </time>
                  </>
                ) : null}
              </p>
            </div>
          )}
          {refresh.isError ? (
            <p role="alert" className="mt-3 text-xs text-amber-200">Couldn't refresh the briefing. The earlier one is still shown.</p>
          ) : null}
        </div>

        {attention.length > 0 ? (
          <div className="min-w-0 border-t border-white/10 pt-6 lg:border-l lg:border-t-0 lg:pl-10 lg:pt-0">
            <h3 className="mb-4 text-sm font-semibold text-emerald-100">Needs attention</h3>
            <ul className="divide-y divide-white/10">
              {attention.map((item) => (
                <AttentionItem key={item.title} item={item} />
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}

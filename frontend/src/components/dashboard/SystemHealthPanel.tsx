import { useQuery } from "@tanstack/react-query";
import { Cpu, Database, Mail, Phone, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { fetchDependencyHealth } from "../../api/health";
import { Card } from "../ui/Card";
import { Button } from "../ui/Button";
import type { DependencyStatus } from "../ui/StatusIndicator";
import { formatRelativeTime } from "../../lib/format";
import { toast } from "../../lib/toastStore";

const STATUS_CONFIG: Record<
  DependencyStatus,
  { label: string; dotClass: string; textClass: string; cardClass: string; iconClass: string }
> = {
  operational: {
    label: "Ready",
    dotClass: "bg-emerald-500",
    textClass: "text-emerald-700",
    cardClass: "border-stone-200/80 bg-stone-50 hover:bg-stone-100/70 hover:border-stone-300",
    iconClass: "border-stone-200 bg-white text-stone-700",
  },
  degraded: {
    label: "Degraded",
    dotClass: "bg-amber-500",
    textClass: "text-amber-700",
    cardClass: "border-amber-200/80 bg-amber-50/40 hover:bg-amber-50/70 hover:border-amber-300",
    iconClass: "border-amber-200 bg-white text-amber-700",
  },
  down: {
    label: "Down",
    dotClass: "bg-rose-500 animate-pulse",
    textClass: "text-rose-700 font-semibold",
    cardClass: "border-rose-200 bg-rose-50/40 hover:bg-rose-50/70 hover:border-rose-300",
    iconClass: "border-rose-200 bg-white text-rose-600",
  },
  unknown: {
    label: "Unknown",
    dotClass: "bg-stone-400",
    textClass: "text-stone-500",
    cardClass: "border-stone-200/80 bg-stone-50 hover:bg-stone-100/70 hover:border-stone-300",
    iconClass: "border-stone-200 bg-white text-stone-600",
  },
};

export function SystemHealthPanel() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["health", "dependencies"],
    queryFn: fetchDependencyHealth,
    refetchInterval: 30_000,
  });

  const services = [
    {
      name: "OpenAI Voice & LLM",
      icon: Cpu,
      status: (data?.openai.status ?? "unknown") as DependencyStatus,
      lastChecked: data?.openai.lastChecked,
      role: "Speech & Summarization",
      to: "/ai-insights",
    },
    {
      name: "Twilio Telephony",
      icon: Phone,
      status: (data?.twilio.status ?? "unknown") as DependencyStatus,
      lastChecked: data?.twilio.lastChecked,
      role: "Inbound Telephony Trunk",
      to: "/calls",
    },
    {
      name: "PostgreSQL Database",
      icon: Database,
      status: (data?.database.status ?? "unknown") as DependencyStatus,
      lastChecked: data?.database.lastChecked,
      role: "Primary Persistent Store",
      to: "/tickets",
    },
    {
      name: "Email Dispatch",
      icon: Mail,
      status: (data?.email.status ?? "unknown") as DependencyStatus,
      lastChecked: data?.email.lastChecked,
      role: "Notification Relay",
      to: "/settings",
    },
  ];

  const anyDown = services.some((s) => s.status === "down");
  const anyDegraded = services.some((s) => s.status === "degraded");
  const allOperational = services.every((s) => s.status === "operational");

  function handleRefresh() {
    refetch().then(() => {
      toast.info("Refreshed dependency health status");
    });
  }

  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-stone-900">System Health</h2>
          <span className="text-xs text-stone-400">• Live Dependency Status</span>
        </div>

        <div className="flex items-center gap-3">
          {allOperational ? (
            <div className="flex items-center gap-1.5 rounded-full border border-emerald-200/80 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-800 shadow-2xs">
              <span className="size-2 rounded-full bg-emerald-500" />
              <span>All Systems Operational</span>
            </div>
          ) : anyDown ? (
            <div className="flex items-center gap-1.5 rounded-full border border-rose-200/80 bg-rose-50 px-3 py-1 text-xs font-medium text-rose-700 shadow-2xs">
              <span className="size-2 rounded-full bg-rose-500 animate-pulse" />
              <span>Service Disruption</span>
            </div>
          ) : anyDegraded ? (
            <div className="flex items-center gap-1.5 rounded-full border border-amber-200/80 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700 shadow-2xs">
              <span className="size-2 rounded-full bg-amber-500" />
              <span>Degraded Performance</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 rounded-full border border-stone-200 bg-stone-100 px-3 py-1 text-xs font-medium text-stone-600">
              <span className="size-2 rounded-full bg-stone-400" />
              <span>Connecting to Backend...</span>
            </div>
          )}

          <Button
            variant="secondary"
            size="icon"
            onClick={handleRefresh}
            disabled={isLoading}
            className="size-7.5 border-stone-200/80 text-stone-600 hover:text-stone-900 hover:bg-stone-50"
            title="Check dependencies now"
            aria-label="Refresh dependencies"
          >
            <RefreshCw className={`size-3.5 ${isLoading ? "animate-spin text-emerald-800" : ""}`} />
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {services.map((svc) => {
          const Icon = svc.icon;
          const config = STATUS_CONFIG[svc.status];

          return (
            <Link
              key={svc.name}
              to={svc.to}
              className={`group flex items-center justify-between rounded-xl border p-3.5 shadow-2xs transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800 ${config.cardClass}`}
              title={`View ${svc.name} operations`}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <div className={`flex size-9 shrink-0 items-center justify-center rounded-lg border shadow-2xs transition-transform duration-150 group-hover:scale-105 ${config.iconClass}`}>
                  <Icon className="size-4.5" />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold text-stone-900 group-hover:text-emerald-950">{svc.name}</p>
                  <p className="truncate text-[11px] text-stone-500">{svc.role}</p>
                </div>
              </div>

              <div className="flex shrink-0 flex-col items-end pl-2">
                <span className={`flex items-center gap-1.5 text-xs font-medium ${config.textClass}`}>
                  <span className={`size-1.5 rounded-full ${config.dotClass}`} />
                  {config.label}
                </span>
                <span className="text-[10px] text-stone-400 tabular-nums">
                  {svc.lastChecked ? formatRelativeTime(svc.lastChecked) : "this minute"}
                </span>
              </div>
            </Link>
          );
        })}
      </div>
    </Card>
  );
}




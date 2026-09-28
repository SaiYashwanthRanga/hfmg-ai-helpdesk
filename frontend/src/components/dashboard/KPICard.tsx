import type { LucideIcon } from "lucide-react";
import { AlertTriangle, PhoneCall, Sparkles, Ticket, Zap } from "lucide-react";
import { Link } from "react-router-dom";
import { Card } from "../ui/Card";
import { LoadingState } from "../ui/LoadingState";
import type { KpiValue } from "../../types/analytics";
import { cn } from "../../lib/cn";

export interface KPICardProps {
  label: string;
  data?: KpiValue;
  isLoading?: boolean;
  trend?: { value: string; positive: boolean };
  iconType?: "ticket" | "zap" | "call" | "alert" | "ai";
  to?: string;
}

const ICON_MAP: Record<string, { icon: LucideIcon; color: string; bg: string }> = {
  ticket: { icon: Ticket, color: "text-emerald-800", bg: "bg-emerald-50/80 border-emerald-200/70" },
  zap: { icon: Zap, color: "text-stone-700", bg: "bg-stone-100/80 border-stone-200/80" },
  call: { icon: PhoneCall, color: "text-emerald-700", bg: "bg-emerald-50/80 border-emerald-200/70" },
  alert: { icon: AlertTriangle, color: "text-rose-700", bg: "bg-rose-50/80 border-rose-200/80" },
  ai: { icon: Sparkles, color: "text-stone-700", bg: "bg-stone-100/80 border-stone-200/80" },
};

export function KPICard({ label, data, isLoading, trend, iconType = "ticket", to }: KPICardProps) {
  if (isLoading) {
    return <LoadingState variant="card" />;
  }

  const iconConfig = ICON_MAP[iconType] || ICON_MAP.ticket;
  const Icon = iconConfig.icon;

  const content = (
    <Card
      padding="sm"
      className={cn(
        "flex flex-col justify-between border-stone-200/80 bg-white p-4 shadow-sm transition-all duration-150",
        to && "cursor-pointer group-hover:border-stone-300 group-hover:shadow-md group-hover:bg-stone-50/50",
      )}
    >
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-stone-500 transition-colors group-hover:text-stone-800">{label}</p>
        <div className={cn("flex size-7.5 items-center justify-center rounded-lg border shadow-2xs transition-transform duration-150 group-hover:scale-105", iconConfig.bg, iconConfig.color)}>
          <Icon className="size-3.5" aria-hidden="true" />
        </div>
      </div>

      <div className="mt-3 flex items-baseline justify-between gap-2">
        {data && data.status === "ready" ? (
          <p className="text-2xl font-bold tracking-tight text-stone-900 tabular-nums sm:text-3xl">
            {data.value}
          </p>
        ) : (
          <p className="text-2xl font-bold text-stone-400 tabular-nums sm:text-3xl" title={data?.blocked_reason ?? undefined}>
            —
          </p>
        )}

        {trend ? (
          <span
            className={`text-xs font-medium ${
              trend.positive ? "text-emerald-700" : "text-amber-700"
            }`}
          >
            {trend.value}
          </span>
        ) : data?.status === "blocked" ? (
          <span className="text-[11px] font-medium text-stone-400">Definition pending</span>
        ) : null}
      </div>
    </Card>
  );

  if (to) {
    return (
      <Link to={to} className="group block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800 rounded-xl">
        {content}
      </Link>
    );
  }

  return content;
}



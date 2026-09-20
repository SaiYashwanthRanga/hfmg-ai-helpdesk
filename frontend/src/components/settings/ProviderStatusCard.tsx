import type { LucideIcon } from "lucide-react";
import { Card } from "../ui/Card";
import { StatusIndicator } from "../ui/StatusIndicator";
import { formatAbsoluteTime } from "../../lib/format";
import type { ProviderStatus } from "../../types/settings";

export interface ProviderStatusCardProps {
  icon: LucideIcon;
  label: string;
  data: ProviderStatus;
}

/**
 * Read-only provider status (DESIGN.md §12). Renders exactly the masked
 * value the server sends — never unmasks, never adds an input, never adds
 * a save action. This component's props type (ProviderStatus) has no field
 * capable of holding a raw secret, so there is nothing here to leak even
 * by accident.
 */
export function ProviderStatusCard({ icon: Icon, label, data }: ProviderStatusCardProps) {
  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
          {label}
        </h2>
        <StatusIndicator status={data.status} label={data.configured ? "Configured" : "Not configured"} />
      </div>
      <dl className="grid grid-cols-[100px_1fr] gap-y-1 text-sm">
        <dt className="text-muted-foreground">Key</dt>
        <dd className="font-mono text-foreground">{data.masked_key ?? "Not configured"}</dd>
        {data.detail ? (
          <>
            <dt className="text-muted-foreground">Detail</dt>
            <dd className="text-foreground">{data.detail}</dd>
          </>
        ) : null}
        <dt className="text-muted-foreground">Verified</dt>
        <dd className="text-foreground">{formatAbsoluteTime(data.last_verified)}</dd>
      </dl>
    </Card>
  );
}
